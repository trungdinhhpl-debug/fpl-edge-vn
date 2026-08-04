"""Projection orchestrator: DB -> TeamStrength -> xMins/xP/MonteCarlo -> DB.

Produces ExpectedMinutes + PlayerProjection rows for every player across the
projection horizon. This is the single source the API and optimizer read from.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.engine import risk as risk_mod
from app.engine.montecarlo import MCPlayer, simulate_fixture, summarise
from app.engine.team_strength import (
    NO_HISTORY_RATIO,
    TeamStrength,
    load_market_map,
    load_promoted_map,
)
from app.engine.xmins import estimate_minutes
from app.engine.xpoints import _poisson_ge_k, expected_points
from app.models import (
    ExpectedMinutes,
    Fixture,
    Gameweek,
    Player,
    PlayerGameweekStat,
    PlayerProjection,
    Team,
)
from app.scoring import RULES


def get_planning_start_gw(db: Session) -> int:
    nxt = db.scalar(select(Gameweek).where(Gameweek.is_next.is_(True)))
    if nxt:
        return nxt.id
    cur = db.scalar(select(Gameweek).where(Gameweek.is_current.is_(True)))
    if cur:
        return cur.id + 1
    unfinished = db.scalars(
        select(Gameweek).where(Gameweek.finished.is_(False)).order_by(Gameweek.id)
    ).first()
    return unfinished.id if unfinished else 1


def _fixtures_by_gw(fixtures: list[Fixture]) -> dict[int, dict[int, list[tuple]]]:
    """gw -> team_id -> [(opp_id, is_home, fixture_id), ...]"""
    out: dict[int, dict[int, list[tuple]]] = defaultdict(lambda: defaultdict(list))
    for f in fixtures:
        if f.event is None:
            continue
        out[f.event][f.team_h].append((f.team_a, True, f.id))
        out[f.event][f.team_a].append((f.team_h, False, f.id))
    return out


def build_projections(
    db: Session,
    horizon: int | None = None,
    mc_iterations: int | None = None,
) -> dict:
    # luật mùa hiện tại được nạp từ DB (ingestion lưu từ FPL game_config)
    from app.scoring import load_rules

    load_rules(db)

    horizon = horizon or settings.projection_horizon
    iters = min(mc_iterations or settings.montecarlo_iterations, 6000)
    rng = np.random.default_rng(42)
    now = datetime.now(timezone.utc)

    teams = db.scalars(select(Team)).all()
    players = db.scalars(select(Player)).all()
    fixtures = db.scalars(select(Fixture)).all()
    finished = [f for f in fixtures if f.finished]

    market = load_market_map(db)
    ts = TeamStrength(
        teams, players, finished,
        market=market,
        market_weight=settings.odds_market_weight,
        promoted=load_promoted_map(db),
        promoted_damping=settings.championship_damping,
    )

    # matches played per team
    matches_played: dict[int, int] = defaultdict(int)
    for f in finished:
        matches_played[f.team_h] += 1
        matches_played[f.team_a] += 1

    # team season expected-goal totals (for MC shares)
    team_xg_total: dict[int, float] = defaultdict(float)
    team_xa_total: dict[int, float] = defaultdict(float)
    for p in players:
        team_xg_total[p.team_id] += p.expected_goals or 0.0
        team_xa_total[p.team_id] += p.expected_assists or 0.0

    # recent per-GW minutes (optional; present only if detail sync ran)
    recent_minutes: dict[int, list[int]] = defaultdict(list)
    stats = db.scalars(
        select(PlayerGameweekStat).order_by(PlayerGameweekStat.gameweek)
    ).all()
    for s in stats:
        recent_minutes[s.player_id].append(s.minutes)

    fx_by_gw = _fixtures_by_gw(fixtures)
    start_gw = get_planning_start_gw(db)
    gws = [gw for gw in range(start_gw, start_gw + horizon) if gw <= 38]

    # wipe & rebuild projections for this horizon
    db.execute(delete(PlayerProjection).where(PlayerProjection.gameweek.in_(gws)))
    db.execute(delete(ExpectedMinutes).where(ExpectedMinutes.gameweek.in_(gws)))

    players_by_team: dict[int, list[Player]] = defaultdict(list)
    for p in players:
        players_by_team[p.team_id].append(p)

    # Đội chưa có dữ liệu Ngoại hạng (mới lên hạng): cầu thủ của họ có 0 phút nên
    # tỷ lệ đá chính tính từ mẫu rỗng sẽ ra ~2% cho tất cả. Thay vào đó xếp hạng
    # vai trò dự kiến theo giá FPL trong từng vị trí (giá do FPL đặt theo vai trò).
    team_minutes: dict[int, int] = defaultdict(int)
    for p in players:
        team_minutes[p.team_id] += p.minutes or 0
    ref_minutes = max(team_minutes.values()) if team_minutes else 0
    no_history_teams = {
        tid for tid, mins in team_minutes.items()
        if ref_minutes > 0 and mins < NO_HISTORY_RATIO * ref_minutes
    }
    # Xếp hạng cho MỌI đội: tân binh ở CLB lâu năm cũng có 0 phút Ngoại hạng
    # (vd cầu thủ mua từ giải nước ngoài) nên cũng cần ước lượng theo vai trò.
    # Xếp hạng trong toàn bộ vị trí của đội để so được với người đã có chỗ đứng.
    role_rank: dict[int, int] = {}
    for tid, squad in players_by_team.items():
        by_pos: dict[int, list[Player]] = defaultdict(list)
        for p in squad:
            by_pos[p.element_type].append(p)
        for pos_players in by_pos.values():
            # giá cao hơn = vai trò dự kiến lớn hơn; hoà giá thì theo ownership
            pos_players.sort(key=lambda x: (-x.now_cost, -x.selected_by_percent))
            for i, p in enumerate(pos_players):
                role_rank[p.id] = i

    n_written = 0
    for gw in gws:
        gw_fx = fx_by_gw.get(gw, {})
        run_mc = True  # run MC across the whole horizon (background job)

        # ---- per-team MC draws reused across that team's players ----
        for team in teams:
            team_fx = gw_fx.get(team.id, [])
            n_fix = len(team_fx)
            squad = players_by_team.get(team.id, [])

            # collect MC arrays per player across their fixture(s)
            mc_accum: dict[int, np.ndarray] = {}

            for (opp_id, is_home, fixture_id) in team_fx:
                lam_for, lam_against = ts.expected_goals(team.id, opp_id, is_home)
                mc_players: list[MCPlayer] = []

                for p in squad:
                    est = estimate_minutes(
                        element_type=p.element_type,
                        status=p.status,
                        chance_of_playing=p.chance_of_playing_next_round,
                        season_starts=p.starts,
                        season_minutes=p.minutes,
                        team_matches_played=matches_played.get(team.id, 0),
                        recent_minutes=recent_minutes.get(p.id) or None,
                        n_fixtures_this_gw=1,  # per-fixture; DGW handled by summing
                        no_pl_history=team.id in no_history_teams,
                        role_rank=role_rank.get(p.id),
                    )
                    bd = expected_points(
                        element_type=p.element_type,
                        minutes_season=p.minutes,
                        xg_season=p.expected_goals,
                        xa_season=p.expected_assists,
                        saves_season=p.saves,
                        dc_season=p.defensive_contribution,
                        yellow_season=p.yellow_cards,
                        red_season=p.red_cards,
                        bps_season=p.bps,
                        penalties_order=p.penalties_order,
                        xmins=est.xmins,
                        p_start=est.p_start,
                        p_appear=est.p_start + est.p_sub,
                        p_60_plus=est.p_60_plus,
                        lam_team_goals=lam_for,
                        lam_conceded=lam_against,
                        team_avg_gf=ts.season_avg_gf(team.id),
                        n_fixtures=1,
                    )
                    # stash analytic breakdown on the player object for this fixture
                    p._acc = getattr(p, "_acc", None) or _Acc()
                    p._acc.add(est, bd, opp_id, is_home, fixture_id, lam_against)

                    if run_mc and est.xmins > 3:
                        share_goal = (
                            (p.expected_goals or 0) / team_xg_total[team.id]
                            if team_xg_total[team.id] > 0.1
                            else 0.0
                        )
                        share_assist = (
                            (p.expected_assists or 0) / max(team_xg_total[team.id], 0.1)
                        )
                        threshold = (
                            RULES.defcon_threshold_def
                            if p.element_type == 2
                            else RULES.defcon_threshold_att
                        )
                        dc90 = bd.components["dc90"]
                        p_hit = _poisson_ge_k(dc90 * bd.components["minutes_frac"], threshold)
                        mc_players.append(
                            MCPlayer(
                                player_id=p.id,
                                element_type=p.element_type,
                                p_start=est.p_start,
                                p_sub=est.p_sub,
                                p_60_plus=est.p_60_plus,
                                share_goal=share_goal,
                                share_assist=share_assist,
                                saves90=(p.saves / max(p.minutes, 1) * 90) if p.element_type == 1 else 0.0,
                                dc_hit_prob=p_hit,
                                yellow90=p.yellow_cards / max(p.minutes, 1) * 90,
                                bonus_base=min(0.6, bd.bonus),
                            )
                        )

                if run_mc and mc_players:
                    sims = simulate_fixture(mc_players, lam_for, lam_against, iters, rng)
                    for pid, arr in sims.items():
                        mc_accum[pid] = mc_accum.get(pid, 0) + arr

            # ---- write rows for every player in this team for this GW ----
            for p in squad:
                acc: _Acc | None = getattr(p, "_acc", None)
                n_fix_player = acc.n if acc else 0

                if not acc or n_fix_player == 0:
                    # blank gameweek for this player
                    proj = PlayerProjection(
                        player_id=p.id, gameweek=gw, xp=0.0, xmins=0.0, p_start=0.0,
                        n_fixtures=0, confidence=0.5, minutes_risk="High",
                        performance_risk="Medium", overall_risk="High",
                        model_version=settings.model_version, data_cutoff=now,
                    )
                    db.add(proj)
                    n_written += 1
                    if hasattr(p, "_acc"):
                        delattr(p, "_acc")
                    continue

                est = acc.first_est
                xp = acc.xp
                mc = summarise(mc_accum[p.id]) if p.id in mc_accum else _analytic_dist(xp)

                mr = risk_mod.minutes_risk(est.p_start, p.status, est.p_no_play)
                goal_dep = acc.xp_goals / xp if xp > 0.01 else 0.0
                pr = risk_mod.performance_risk(
                    minutes_season=p.minutes, xp=xp, goal_dependency=goal_dep,
                    goals_scored=p.goals_scored, expected_goals=p.expected_goals,
                    variance=mc["variance"],
                )
                overall = risk_mod.combine(mr, pr)
                conf = risk_mod.confidence_from(est.confidence, p.minutes, bool(recent_minutes.get(p.id)))

                db.add(ExpectedMinutes(
                    player_id=p.id, gameweek=gw, xmins=round(acc.xmins, 1),
                    p_start=est.p_start, p_sub=est.p_sub, p_no_play=est.p_no_play,
                    p_60_plus=est.p_60_plus, confidence=est.confidence,
                    ci_low=est.ci_low, ci_high=est.ci_high, reason=est.reason,
                    model_version=settings.model_version, data_cutoff=now,
                ))
                db.add(PlayerProjection(
                    player_id=p.id, gameweek=gw,
                    xp=round(xp, 3), xp_appearance=round(acc.xp_appearance, 3),
                    xp_goals=round(acc.xp_goals, 3), xp_assists=round(acc.xp_assists, 3),
                    xp_clean_sheet=round(acc.xp_cs, 3), xp_saves=round(acc.xp_saves, 3),
                    xp_bonus=round(acc.xp_bonus, 3), xp_defcon=round(acc.xp_defcon, 3),
                    xp_negative=round(acc.xp_negative, 3),
                    xmins=round(acc.xmins, 1), p_start=est.p_start,
                    clean_sheet_prob=round(acc.cs_prob, 3), goal_prob=round(acc.goal_prob, 3),
                    assist_prob=round(acc.assist_prob, 3),
                    mc_mean=round(mc["mc_mean"], 2), mc_median=round(mc["mc_median"], 2),
                    mc_p25=round(mc["mc_p25"], 2), mc_p75=round(mc["mc_p75"], 2),
                    mc_p90=round(mc["mc_p90"], 2), mc_ceiling=round(mc["mc_ceiling"], 2),
                    p_blank=round(mc["p_blank"], 3), p_returns=round(mc["p_returns"], 3),
                    p_haul=round(mc["p_haul"], 3), variance=round(mc["variance"], 2),
                    fixture_id=acc.fixture_id, opponent_team=acc.opponent_id,
                    was_home=acc.is_home, n_fixtures=n_fix_player,
                    confidence=conf, minutes_risk=mr, performance_risk=pr, overall_risk=overall,
                    model_version=settings.model_version, data_cutoff=now,
                ))
                n_written += 1
                if hasattr(p, "_acc"):
                    delattr(p, "_acc")

    db.commit()
    return {"gameweeks": gws, "projections_written": n_written, "mc_iterations": iters}


def _analytic_dist(xp: float) -> dict:
    """Fallback distribution when MC wasn't run for a player (blank-ish)."""
    return {
        "mc_mean": xp, "mc_median": max(0, xp - 0.5), "mc_p25": max(0, xp - 1.5),
        "mc_p75": xp + 1.5, "mc_p90": xp + 3.5, "mc_ceiling": xp + 5.0,
        "p_blank": 0.4 if xp < 3 else 0.25, "p_returns": min(0.6, xp / 8),
        "p_haul": min(0.2, xp / 30), "variance": max(1.0, xp),
    }


class _Acc:
    """Accumulates a player's per-fixture EV across a (possibly double) GW."""

    def __init__(self) -> None:
        self.n = 0
        self.xp = 0.0
        self.xmins = 0.0
        self.xp_appearance = self.xp_goals = self.xp_assists = 0.0
        self.xp_cs = self.xp_saves = self.xp_bonus = self.xp_defcon = self.xp_negative = 0.0
        self.cs_prob = 0.0
        self.goal_prob = 0.0
        self.assist_prob = 0.0
        self.first_est = None
        self.fixture_id = None
        self.opponent_id = None
        self.is_home = None

    def add(self, est, bd, opp_id, is_home, fixture_id, lam_against) -> None:
        if self.n == 0:
            self.first_est = est
            self.fixture_id = fixture_id
            self.opponent_id = opp_id
            self.is_home = is_home
        self.n += 1
        self.xmins += est.xmins
        self.xp += bd.xp
        self.xp_appearance += bd.appearance
        self.xp_goals += bd.goals
        self.xp_assists += bd.assists
        self.xp_cs += bd.clean_sheet
        self.xp_saves += bd.saves
        self.xp_bonus += bd.bonus
        self.xp_defcon += bd.defcon
        self.xp_negative += bd.negative
        # probabilities: combine across fixtures (at least one)
        self.cs_prob = max(self.cs_prob, bd.clean_sheet_prob)
        self.goal_prob = 1 - (1 - self.goal_prob) * (1 - bd.goal_prob)
        self.assist_prob = 1 - (1 - self.assist_prob) * (1 - bd.assist_prob)

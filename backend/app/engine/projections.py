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
    load_market_support,
    load_promoted_map,
)
from app.engine import bonus as bonus_mod
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
    market_only: bool = False,
    persist: bool = True,
    collect_components: dict[tuple[int, int], dict[str, float]] | None = None,
) -> dict:
    """Tính dự báo cho `horizon` vòng tới.

    `market_only=True` + `persist=False` cho ra **baseline kèo**: đúng engine này,
    chỉ khác một chỗ duy nhất — sức mạnh đội lấy **hoàn toàn từ kèo** thay vì pha
    với mô hình nội bộ. Dùng chung toàn bộ phần còn lại (xMins, chia quỹ bonus,
    luật điểm) là điều kiện để phép so có nghĩa: nếu baseline chạy trên một đường
    tính khác thì chênh lệch đo được sẽ lẫn cả sự khác nhau về cách tính, chứ không
    còn là "sức mạnh đội đến từ đâu".

    Ở chế độ này Monte Carlo bị bỏ (baseline chỉ cần xP) và không ghi gì vào DB;
    hàm trả về `{"xp": {(player_id, gameweek): xp}}`.
    """
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
        # baseline kèo: tin thị trường tuyệt đối, và KHÔNG hạ trọng số theo độ mỏng
        # (full_support_books=1 làm min(1, n/1) luôn bằng 1) — hạ trọng số là kéo
        # baseline về phía mô hình, tức làm nó bớt là baseline kèo.
        market_weight=1.0 if market_only else settings.odds_market_weight,
        market_support=None if market_only else load_market_support(db),
        full_support_books=1 if market_only else settings.odds_full_support_books,
        promoted=load_promoted_map(db),
        promoted_damping=settings.championship_damping,
    )
    baseline_xp: dict[tuple[int, int], float] = {}

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

    # How far last season still describes each player (new club / new manager).
    # Neither is in the FPL API, so this comes from a configured list.
    from app.services.season_state import prior_reliability as _prior_rel
    from app.services.season_state import stats_season as _stats_season

    _team_short = {t.id: t.short_name for t in teams}
    prior_rel = {p.id: _prior_rel(p, _team_short.get(p.team_id)) for p in players}

    # Mùa mà tổng cả-mùa của cầu thủ thuộc về. Nếu là mùa trước thì BPS được quy
    # đổi sang luật đang áp trong xpoints (luật BPS 2026/27 đã đổi).
    stats_from_season = _stats_season(db)

    fx_by_gw = _fixtures_by_gw(fixtures)
    start_gw = get_planning_start_gw(db)
    gws = [gw for gw in range(start_gw, start_gw + horizon) if gw <= 38]

    # wipe & rebuild projections for this horizon (baseline không đụng vào DB)
    if persist:
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

    def _minutes_estimate(p, team_id: int):
        """estimate_minutes cho một cầu thủ ở một trận (thuần, không phụ thuộc vòng)."""
        return estimate_minutes(
            element_type=p.element_type,
            status=p.status,
            chance_of_playing=p.chance_of_playing_next_round,
            season_starts=p.starts,
            season_minutes=p.minutes,
            team_matches_played=matches_played.get(team_id, 0),
            recent_minutes=recent_minutes.get(p.id) or None,
            n_fixtures_this_gw=1,   # per-fixture; DGW handled by summing
            no_pl_history=team_id in no_history_teams,
            role_rank=role_rank.get(p.id),
            prior_reliability=prior_rel.get(p.id, 1.0),
        )

    def _xp(p, est, lam_for: float, lam_against: float, team_id: int,
            bonus_override: float | None, team_goal_scale: float = 1.0):
        return expected_points(
            element_type=p.element_type,
            minutes_season=p.minutes,
            xg_season=p.expected_goals,
            xa_season=p.expected_assists,
            saves_season=p.saves,
            dc_season=p.defensive_contribution,
            yellow_season=p.yellow_cards,
            red_season=p.red_cards,
            bps_season=p.bps,
            cbi_season=p.clearances_blocks_interceptions or 0.0,
            stats_season=stats_from_season,
            bonus_override=bonus_override,
            team_goal_scale=team_goal_scale,
            penalties_order=p.penalties_order,
            xmins=est.xmins,
            p_start=est.p_start,
            p_appear=est.p_start + est.p_sub,
            p_60_plus=est.p_60_plus,
            lam_team_goals=lam_for,
            lam_conceded=lam_against,
            team_avg_gf=ts.season_avg_gf(team_id),
            n_fixtures=1,
        )

    def _allocate_gw_bonus(gw_fx: dict) -> dict[tuple[int, int], float]:
        """Chia quỹ 6 điểm bonus của TỪNG TRẬN, trả về {(player_id, fixture_id): bonus}.

        Phải chạy trước vòng lặp chính vì bonus của một cầu thủ phụ thuộc vào 21
        người còn lại của trận — trong đó có 11 người thuộc đội đối phương, mà vòng
        lặp chính xử lý mỗi đội một lượt nên không bao giờ thấy cả hai bên cùng lúc.

        Ở đây `expected_points` được gọi thêm một lượt chỉ để lấy nguyên liệu
        (bps90, bàn/kiến tạo kỳ vọng, xác suất sạch lưới). Nó là phép tính số học
        thuần, không truy vấn gì, và rẻ hơn nhiều so với Monte Carlo phía sau —
        đánh đổi này để tránh phải nhân đôi công thức ở hai chỗ.
        """
        by_fixture: dict[int, list[bonus_mod.BonusEntry]] = defaultdict(list)
        raw_goals: dict[tuple[int, int], float] = defaultdict(float)
        sim_xg: dict[tuple[int, int], float] = defaultdict(float)
        sim_xa: dict[tuple[int, int], float] = defaultdict(float)
        lam_of: dict[tuple[int, int], float] = {}
        for team_id, fixtures in gw_fx.items():
            for (opp_id, is_home, fixture_id) in fixtures:
                lam_for, lam_against = ts.expected_goals(team_id, opp_id, is_home)
                lam_of[(team_id, fixture_id)] = lam_for
                for p in players_by_team.get(team_id, []):
                    est = _minutes_estimate(p, team_id)
                    if est.xmins <= 0:
                        continue
                    c = _xp(p, est, lam_for, lam_against, team_id, None).components
                    raw_goals[(team_id, fixture_id)] += c["exp_goals"]
                    # Mẫu số của share trong Monte Carlo phải là tổng xG của đúng
                    # những người ĐƯỢC MÔ PHỎNG. Dùng tổng cả đội (kể cả người bị
                    # loại vì xMins <= 3) thì các share cộng lại nhỏ hơn 1, và phần
                    # thiếu đó biến thành bàn thắng thất lạc — đo được: tiền đạo chỉ
                    # nhận 88% mức giải tích dù tổng bàn của đội đã được bảo toàn.
                    if est.xmins > 3:
                        sim_xg[(team_id, fixture_id)] += p.expected_goals or 0.0
                        sim_xa[(team_id, fixture_id)] += p.expected_assists or 0.0
                    by_fixture[fixture_id].append(bonus_mod.BonusEntry(
                        player_id=p.id,
                        expected_bps=bonus_mod.expected_fixture_bps(
                            bps90=c["bps90"],
                            minutes_frac=c["minutes_frac"],
                            exp_goals=c["exp_goals"],
                            exp_assists=c["exp_assists"],
                            cs_prob=c["cs_prob"],
                            p_60_plus=c["p_60_plus"],
                            element_type=p.element_type,
                        ),
                    ))
        out: dict[tuple[int, int], float] = {}
        for fixture_id, entries in by_fixture.items():
            for pid, val in bonus_mod.allocate(
                entries, max_bonus=RULES.max_bonus
            ).items():
                out[(pid, fixture_id)] = val

        # Hệ số bảo toàn bàn thắng cho từng (đội, trận). Chặn trong [0.5, 2.0]: một
        # hệ số ngoài khoảng đó nghĩa là dữ liệu cầu thủ và mô hình sức mạnh đội
        # đang mâu thuẫn nặng, và ép khớp bằng mọi giá sẽ bóp méo từng cầu thủ hơn
        # là sửa được gì.
        scales: dict[tuple[int, int], float] = {}
        for key, raw in raw_goals.items():
            lam = lam_of.get(key, 0.0)
            if raw <= 1e-6 or lam <= 0:
                continue
            scales[key] = max(0.5, min(2.0, lam / raw))
        return out, scales, dict(sim_xg), dict(sim_xa)

    for gw in gws:
        gw_fx = fx_by_gw.get(gw, {})
        # baseline chỉ cần xP, không cần phân phối — bỏ Monte Carlo cho nhanh
        run_mc = not market_only
        bonus_by_player_fixture, goal_scales, sim_xg, sim_xa = _allocate_gw_bonus(gw_fx)

        if market_only:
            # Chỉ tính cho trận CÓ kèo. Trận không có kèo thì TeamStrength rơi về
            # mô hình nội bộ, và một "baseline kèo" như vậy thật ra là chính mô
            # hình đội lốt — so với nó là tự so với mình.
            for team in teams:
                for (opp_id, is_home, fixture_id) in gw_fx.get(team.id, []):
                    if not ts.has_market(team.id, opp_id, is_home):
                        continue
                    lam_for, lam_against = ts.expected_goals(team.id, opp_id, is_home)
                    for p in players_by_team.get(team.id, []):
                        est = _minutes_estimate(p, team.id)
                        bd = _xp(
                            p, est, lam_for, lam_against, team.id,
                            bonus_by_player_fixture.get((p.id, fixture_id)),
                            goal_scales.get((team.id, fixture_id), 1.0),
                        )
                        key = (p.id, gw)
                        baseline_xp[key] = baseline_xp.get(key, 0.0) + bd.xp
            continue

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
                # tổng xG của riêng nhóm được mô phỏng (xem chú thích ở pre-pass);
                # rơi về tổng cả đội nếu pre-pass không có số cho trận này
                denom_xg = sim_xg.get(
                    (team.id, fixture_id), team_xg_total[team.id]
                ) or team_xg_total[team.id]

                for p in squad:
                    est = _minutes_estimate(p, team.id)
                    bd = _xp(
                        p, est, lam_for, lam_against, team.id,
                        bonus_by_player_fixture.get((p.id, fixture_id)),
                        goal_scales.get((team.id, fixture_id), 1.0),
                    )
                    # stash analytic breakdown on the player object for this fixture
                    p._acc = getattr(p, "_acc", None) or _Acc()
                    p._acc.add(est, bd, opp_id, is_home, fixture_id, lam_against)

                    if run_mc and est.xmins > 3:
                        share_goal = (
                            (p.expected_goals or 0) / denom_xg
                            if denom_xg > 0.1
                            else 0.0
                        )
                        share_assist = (
                            (p.expected_assists or 0) / max(denom_xg, 0.1)
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
                                # kỳ vọng bonus giải tích, KHÔNG cắt trần: MC quy
                                # nó thành tần suất (chia 2) nên cắt ở đây sẽ làm
                                # nhóm đầu bão hoà, xem engine/montecarlo.py
                                bonus_base=bd.bonus,
                            )
                        )

                if run_mc and mc_players:
                    # Chế độ chẩn đoán: gom trung bình từng thành phần của MC để so
                    # với phân rã giải tích. Phải đi qua CHÍNH pipeline này, vì một
                    # bản dựng lại bằng tay sẽ có xMins khác (matches_played,
                    # role_rank, no_pl_history) và hai bên hết so được với nhau.
                    col: dict | None = {} if collect_components is not None else None
                    sims = simulate_fixture(
                        mc_players, lam_for, lam_against, iters, rng, collect=col
                    )
                    if col is not None and collect_components is not None:
                        for pid, c in col.items():
                            slot = collect_components.setdefault((pid, gw), {})
                            for k, v in c.items():
                                slot[k] = slot.get(k, 0.0) + v
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
                    mc_p10=round(mc["mc_p10"], 2),
                    mc_p25=round(mc["mc_p25"], 2), mc_p75=round(mc["mc_p75"], 2),
                    mc_p90=round(mc["mc_p90"], 2), mc_ceiling=round(mc["mc_ceiling"], 2),
                    p_blank=round(mc["p_blank"], 3), p_returns=round(mc["p_returns"], 3),
                    p_haul=round(mc["p_haul"], 3), p_15=round(mc["p_15"], 3),
                    variance=round(mc["variance"], 2),
                    fixture_id=acc.fixture_id, opponent_team=acc.opponent_id,
                    was_home=acc.is_home, n_fixtures=n_fix_player,
                    confidence=conf, minutes_risk=mr, performance_risk=pr, overall_risk=overall,
                    model_version=settings.model_version, data_cutoff=now,
                ))
                n_written += 1
                if hasattr(p, "_acc"):
                    delattr(p, "_acc")

    if market_only:
        # không commit: chế độ baseline không được để lại dấu vết nào trong DB
        return {"gameweeks": gws, "market_only": True, "xp": baseline_xp,
                "players_covered": len({pid for pid, _ in baseline_xp})}
    if persist:
        db.commit()
    return {"gameweeks": gws, "projections_written": n_written, "mc_iterations": iters}


def _analytic_dist(xp: float) -> dict:
    """Fallback distribution when MC wasn't run for a player (blank-ish)."""
    return {
        "mc_mean": xp, "mc_median": max(0, xp - 0.5),
        "mc_p10": max(0.0, xp - 2.5), "mc_p25": max(0, xp - 1.5),
        "mc_p75": xp + 1.5, "mc_p90": xp + 3.5, "mc_ceiling": xp + 5.0,
        "p_blank": 0.4 if xp < 3 else 0.25, "p_returns": min(0.6, xp / 8),
        "p_haul": min(0.2, xp / 30), "p_15": min(0.08, xp / 90),
        "variance": max(1.0, xp),
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

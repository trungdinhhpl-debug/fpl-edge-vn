"""Monte Carlo simulation of gameweek points (spec §8).

Simulates at the *team-fixture* level so that correlation is preserved:
  * a team's clean sheet is shared by its GK + defenders (same conceded draw);
  * players' goals are drawn from the SAME team-goals total, so a high-scoring
    match lifts team-mates together (no false independence assumption).

Returns per-player point distributions -> summary percentiles & tail probs.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.scoring import RULES


@dataclass
class MCPlayer:
    player_id: int
    element_type: int
    p_start: float
    p_sub: float
    p_60_plus: float
    share_goal: float       # player's share of team goals
    share_assist: float     # player's share of team assists (approx)
    saves90: float
    dc_hit_prob: float      # P(defensive-contribution threshold) if started
    yellow90: float
    bonus_base: float       # expected bonus at full involvement


# A single player may never be credited with more than this share of the pool,
# however few team-mates are on the pitch alongside him.
MAX_INDIVIDUAL_SHARE = 0.95
# Ceiling on how far an absent player's share may inflate those who did play.
MAX_SHARE_TRANSFER = 3.0


def _effective_shares(
    players: list[MCPlayer], played: dict[int, np.ndarray], attr: str, n: int
) -> tuple[dict[int, np.ndarray], float]:
    """Per-simulation shares, with an absent player's share passed to the rest.

    The old code simply zeroed a missing player's goals, so his share of the
    team's output evaporated: a team-mate's expected points were byte-identical
    whether the first-choice striker started 95% or 5% of the time. Understudies
    were therefore under-rated by construction. Here the shares of whoever
    actually took the pitch are scaled back up to the full squad total, which
    hands the absentee's share to his replacements in proportion.
    """
    full = sum(getattr(p, attr) for p in players)
    if full <= 1e-9:
        return {p.player_id: np.zeros(n) for p in players}, 0.0

    present = np.zeros(n)
    for p in players:
        present += getattr(p, attr) * played[p.player_id]
    scale = np.where(present > 1e-9, full / np.maximum(present, 1e-9), 0.0)
    scale = np.minimum(scale, MAX_SHARE_TRANSFER)

    eff = {
        p.player_id: np.minimum(
            getattr(p, attr) * scale * played[p.player_id], MAX_INDIVIDUAL_SHARE
        )
        for p in players
    }
    return eff, full


def _allocate(
    totals: np.ndarray,
    eff: dict[int, np.ndarray],
    order: list[int],
    rng: np.random.Generator,
    cap: dict[int, np.ndarray] | None = None,
) -> dict[int, np.ndarray]:
    """Split `totals` among players by drawing conditional binomials in turn.

    This is a multinomial sampled sequentially, which numpy can vectorise while
    `rng.multinomial` cannot (both the count and the probability vector vary per
    simulation). Drawing each player independently — as the old code did — let
    the parts exceed the whole: 20.9% of simulated matches allocated more goals
    than the team had scored, once handing out 14 goals in a 4-goal match. The
    mean was right; the tail was not, and the tail is exactly where P(haul) and
    the ceiling are read off.

    The pool starts at 1.0 rather than at the sum of shares, so goals nobody in
    the modelled squad is credited with (own goals, fringe players excluded from
    the simulation) stay unattributed instead of being forced onto the last
    player in the list.
    """
    remaining = totals.astype(np.int64).copy()
    pool = np.ones(len(remaining))
    out: dict[int, np.ndarray] = {}
    for pid in order:
        share = eff[pid]
        prob = np.divide(share, pool, out=np.zeros_like(share), where=pool > 1e-9)
        np.clip(prob, 0.0, 1.0, out=prob)
        available = remaining if cap is None else np.minimum(remaining, cap[pid])
        drawn = rng.binomial(np.maximum(available, 0), prob)
        out[pid] = drawn
        remaining -= drawn
        pool = np.maximum(pool - share, 0.0)
    return out


def simulate_fixture(
    players: list[MCPlayer],
    lam_for: float,
    lam_conceded: float,
    n: int,
    rng: np.random.Generator,
) -> dict[int, np.ndarray]:
    """Return {player_id: array[n] of points} for one fixture."""
    team_goals = rng.poisson(max(lam_for, 0.01), n)
    team_conceded = rng.poisson(max(lam_conceded, 0.01), n)
    clean_sheet = team_conceded == 0
    conceded_penalty = -(team_conceded // 2)

    # ---- who is on the pitch, drawn once and reused by every stage ----
    started_by: dict[int, np.ndarray] = {}
    subbed_by: dict[int, np.ndarray] = {}
    played_by: dict[int, np.ndarray] = {}
    for p in players:
        r = rng.random(n)
        st = r < p.p_start
        sb = (r >= p.p_start) & (r < p.p_start + p.p_sub)
        started_by[p.player_id] = st
        subbed_by[p.player_id] = sb
        played_by[p.player_id] = st | sb

    # ---- share out the team's goals, then its assists ----
    order = [p.player_id for p in players]
    goal_eff, _ = _effective_shares(players, played_by, "share_goal", n)
    goals_by = _allocate(team_goals, goal_eff, order, rng)

    # A player cannot assist his own goal, so each is limited to the goals his
    # team-mates scored. The old draw was independent of the goal draw entirely,
    # which credited the same player with both on 26% of simulated matches.
    assist_eff, _ = _effective_shares(players, played_by, "share_assist", n)
    assist_cap = {
        pid: np.maximum(team_goals - goals_by[pid], 0) for pid in order
    }
    assists_by = _allocate(team_goals, assist_eff, order, rng, cap=assist_cap)

    out: dict[int, np.ndarray] = {}
    for p in players:
        started = started_by[p.player_id]
        subbed = subbed_by[p.player_id]
        played = played_by[p.player_id]
        pts = np.zeros(n, dtype=float)

        # appearance
        pts += np.where(started, RULES.points_play_60_plus, 0.0)
        pts += np.where(subbed, RULES.points_play_under_60, 0.0)

        g = goals_by[p.player_id]
        pts += g * RULES.goal_points.get(p.element_type, 4)

        a = assists_by[p.player_id]
        pts += a * RULES.assist_points

        # clean sheet (needs a start ~ 60'+)
        cs_pts = RULES.clean_sheet_points.get(p.element_type, 0)
        if cs_pts:
            pts += np.where(started & clean_sheet, cs_pts, 0.0)

        # conceded penalty (GK/DEF, needs 60'+)
        if p.element_type in RULES.conceded_penalty_positions:
            pts += np.where(started, conceded_penalty, 0)

        # saves (GK). The 0.3 factor is for a keeper who came ON, so it must not
        # apply to one who never left the bench: `np.where(started, 1, 0.3)` gave
        # a permanent reserve 0.064 points a match from saves he could not have
        # made — small, but it quietly flattered every cheap bench goalkeeper.
        if p.element_type == 1 and p.saves90 > 0:
            rate = np.where(started, 1.0, np.where(subbed, 0.3, 0.0))
            saves = rng.poisson(p.saves90 * rate, n)
            pts += np.where(played, np.floor(saves / RULES.saves_per_point), 0.0)

        # defensive contribution
        if p.element_type in RULES.defcon_positions and p.dc_hit_prob > 0:
            hit = rng.random(n) < p.dc_hit_prob
            pts += np.where(started & hit, RULES.defcon_points, 0.0)

        # bonus — 1..3 điểm khi có mặt trong top 3 BPS của trận.
        #
        # Tần suất được khoá vào KỲ VỌNG GIẢI TÍCH (`bonus_base`, do
        # engine/bonus.allocate chia trong nội bộ trận). Khi mask nổ, số điểm rút
        # trong {1,2,3} nên trung bình là 2 — vậy để trung bình mô phỏng khớp kỳ
        # vọng, xác suất mask phải là `bonus_base / 2`.
        #
        # Bản trước dùng `min(1.0, 0.4 + bonus_base)` với `bonus_base` bị cắt ở
        # 0.6: mọi cầu thủ có kỳ vọng bonus từ 0.6 trở lên đều bão hoà ở xác suất
        # 1.0, tức là *luôn* được bonus. Với mô hình bonus mới (cầu thủ đầu bảng
        # ~1.8 điểm) thì phần lớn nhóm đầu rơi vào chỗ bão hoà đó.
        returned = (g + a) > 0
        bonus = np.where(
            returned,
            rng.integers(1, 4, n),  # 1..3 when involved
            np.where(started & clean_sheet & (p.element_type <= 2), 1, 0),
        )
        bonus_mask = rng.random(n) < min(1.0, max(0.0, p.bonus_base) / 2.0)
        pts += np.where(bonus_mask, bonus, 0)

        # yellow cards
        if p.yellow90 > 0:
            yc = rng.random(n) < (p.yellow90 * np.where(played, 1.0, 0.0))
            pts += np.where(yc, RULES.yellow_card_points, 0)

        out[p.player_id] = pts
    return out


def summarise(points: np.ndarray) -> dict:
    return {
        "mc_mean": float(np.mean(points)),
        "mc_median": float(np.median(points)),
        # P10 là "sàn xấu": mức mà 1 trong 10 vòng sẽ tệ hơn. Cần cho quyết
        # định giữ/bán, vì P25 chưa chạm phần đuôi mà người chơi thật sự sợ.
        "mc_p10": float(np.percentile(points, 10)),
        "mc_p25": float(np.percentile(points, 25)),
        "mc_p75": float(np.percentile(points, 75)),
        "mc_p90": float(np.percentile(points, 90)),
        "mc_ceiling": float(np.percentile(points, 95)),
        "p_blank": float(np.mean(points <= 2)),
        "p_returns": float(np.mean(points >= 5)),
        "p_haul": float(np.mean(points >= 10)),
        # A captain doubles the score, so >=15 is the "won me the gameweek"
        # tail that separates ceiling picks from merely high-EV ones.
        "p_15": float(np.mean(points >= 15)),
        "variance": float(np.var(points)),
    }

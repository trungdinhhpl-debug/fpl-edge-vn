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

    out: dict[int, np.ndarray] = {}
    for p in players:
        r = rng.random(n)
        started = r < p.p_start
        subbed = (r >= p.p_start) & (r < p.p_start + p.p_sub)
        played = started | subbed
        pts = np.zeros(n, dtype=float)

        # appearance
        pts += np.where(started, RULES.points_play_60_plus, 0.0)
        pts += np.where(subbed, RULES.points_play_under_60, 0.0)

        # goals — binomial share of the team's goals (only if played)
        if p.share_goal > 0:
            g = rng.binomial(team_goals, min(p.share_goal, 0.95))
            g = np.where(played, g, 0)
            pts += g * RULES.goal_points.get(p.element_type, 4)
        else:
            g = np.zeros(n)

        # assists
        if p.share_assist > 0:
            a = rng.binomial(team_goals, min(p.share_assist, 0.6))
            a = np.where(played, a, 0)
            pts += a * RULES.assist_points
        else:
            a = np.zeros(n)

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

        # bonus — more likely when the player returned
        returned = (g + a) > 0
        bonus = np.where(
            returned,
            rng.integers(1, 4, n),  # 1..3 when involved
            np.where(started & clean_sheet & (p.element_type <= 2), 1, 0),
        )
        # scale bonus frequency by the player's bonus propensity
        bonus_mask = rng.random(n) < min(1.0, 0.4 + p.bonus_base)
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

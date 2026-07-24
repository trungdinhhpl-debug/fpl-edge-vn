"""Custom fixture difficulty (spec §13) — separate attack & defence ratings.

Rather than reusing FPL's single FDR, we derive per-fixture:
  * attacking difficulty  <- expected goals the team will SCORE (higher = easier)
  * defensive difficulty  <- expected goals the team will CONCEDE / CS prob
  * projected goals, clean-sheet probability, home/away.

Ratings are mapped onto a 1 (easiest) .. 5 (hardest) scale for display.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.engine.team_strength import TeamStrength


@dataclass
class FixtureRating:
    team_id: int
    opponent_id: int
    gameweek: int
    is_home: bool
    proj_goals_for: float
    proj_goals_against: float
    clean_sheet_prob: float
    attack_difficulty: float   # 1 easy .. 5 hard
    defence_difficulty: float  # 1 easy .. 5 hard


def _to_scale(value: float, lo: float, hi: float, invert: bool = False) -> float:
    """Map value in [lo,hi] to 1..5. invert=True => higher value is easier."""
    value = max(lo, min(hi, value))
    frac = (value - lo) / (hi - lo) if hi > lo else 0.5
    if invert:
        frac = 1 - frac
    return round(1 + frac * 4, 1)


def rate_fixture(
    ts: TeamStrength, team_id: int, opp_id: int, gw: int, is_home: bool
) -> FixtureRating:
    lam_for, lam_against = ts.expected_goals(team_id, opp_id, is_home)
    cs = ts.clean_sheet_prob(team_id, opp_id, is_home)
    return FixtureRating(
        team_id=team_id,
        opponent_id=opp_id,
        gameweek=gw,
        is_home=is_home,
        proj_goals_for=round(lam_for, 2),
        proj_goals_against=round(lam_against, 2),
        clean_sheet_prob=round(cs, 3),
        # more expected goals-for => easier attack => lower difficulty number
        attack_difficulty=_to_scale(lam_for, 0.6, 2.6, invert=True),
        defence_difficulty=_to_scale(lam_against, 0.6, 2.6),
    )

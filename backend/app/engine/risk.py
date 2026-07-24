"""Risk indices (spec §19): minutes, performance, structural.

Each returns one of: Low / Medium / High / Very High.
"""
from __future__ import annotations

_LEVELS = ["Low", "Medium", "High", "Very High"]


def _bucket(score: float) -> str:
    """score in 0..1 -> level."""
    if score < 0.25:
        return "Low"
    if score < 0.5:
        return "Medium"
    if score < 0.75:
        return "High"
    return "Very High"


def minutes_risk(p_start: float, status: str, p_no_play: float) -> str:
    score = 0.0
    score += (1.0 - p_start) * 0.6
    score += p_no_play * 0.4
    if status != "a":
        score += 0.25
    return _bucket(min(score, 1.0))


def performance_risk(
    *,
    minutes_season: int,
    xp: float,
    goal_dependency: float,   # share of xP coming from goals
    goals_scored: int,
    expected_goals: float,
    variance: float,
) -> str:
    """High when points rely on a small/over-performing sample or are goal-heavy."""
    score = 0.0
    # small sample
    if minutes_season < 450:
        score += 0.3
    elif minutes_season < 900:
        score += 0.15
    # goal dependency (boom-or-bust)
    score += min(goal_dependency, 1.0) * 0.3
    # overperformance vs xG (regression risk)
    if expected_goals > 0.5:
        over = (goals_scored - expected_goals) / max(expected_goals, 1.0)
        if over > 0.4:
            score += min(over, 1.0) * 0.3
    # raw variance relative to mean
    if xp > 0 and variance / max(xp, 1.0) > 4:
        score += 0.15
    return _bucket(min(score, 1.0))


def combine(minutes: str, performance: str) -> str:
    """Overall = the worse of the two, nudged up if both are elevated."""
    mi = _LEVELS.index(minutes)
    pi = _LEVELS.index(performance)
    idx = max(mi, pi)
    if mi >= 1 and pi >= 1:
        idx = min(idx + 1, len(_LEVELS) - 1)
    return _LEVELS[idx]


def confidence_from(minutes_conf: str, minutes_season: int, has_recent: bool) -> float:
    """0..1 model confidence for a projection."""
    base = {"High": 0.8, "Medium": 0.6, "Low": 0.4}.get(minutes_conf, 0.5)
    if minutes_season > 900:
        base += 0.1
    elif minutes_season < 300:
        base -= 0.15
    if has_recent:
        base += 0.05
    return round(max(0.15, min(0.95, base)), 2)

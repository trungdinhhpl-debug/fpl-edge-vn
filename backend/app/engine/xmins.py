"""Expected minutes model (spec §6).

Outputs, per player per fixture:
  xmins, p_start, p_sub, p_no_play, p_60_plus, confidence, CI, main reason.

Inputs: season starts/minutes, recent per-GW minutes (if available), FPL
availability status + chance_of_playing_next_round, and fixture congestion
(double gameweeks add rotation risk).

xMins is treated as a first-class driver of xP — never a cosmetic label.
"""
from __future__ import annotations

from dataclasses import dataclass

E_MIN_START = 84.0     # avg minutes when a player starts
E_MIN_SUB = 20.0       # avg minutes when introduced from the bench
CONGESTION_PENALTY = 0.08   # rotation risk multiplier per extra fixture in a DGW


@dataclass
class MinutesEstimate:
    xmins: float
    p_start: float
    p_sub: float
    p_no_play: float
    p_60_plus: float
    confidence: str
    ci_low: float
    ci_high: float
    reason: str


def _availability_multiplier(status: str, chance: int | None) -> tuple[float, str]:
    """Return (multiplier, reason) from FPL availability fields."""
    if status == "a":
        if chance is not None and chance < 100:
            return chance / 100.0, f"chance_of_playing {chance}%"
        return 1.0, ""
    if status == "d":  # doubtful
        return (chance / 100.0 if chance is not None else 0.5), "doubtful (flagged)"
    if status in ("i", "u", "n", "s"):  # injured / unavailable / not-in-squad / suspended
        label = {"i": "injured", "u": "unavailable", "n": "not in squad", "s": "suspended"}[status]
        return (chance / 100.0 if chance is not None else 0.0), label
    return 1.0, ""


def estimate_minutes(
    *,
    element_type: int,
    status: str,
    chance_of_playing: int | None,
    season_starts: int,
    season_minutes: int,
    team_matches_played: int,
    recent_minutes: list[int] | None = None,
    n_fixtures_this_gw: int = 1,
) -> MinutesEstimate:
    avail_mult, avail_reason = _availability_multiplier(status, chance_of_playing)

    # Reference number of games for rate calculations. In pre-season no current
    # fixtures are finished yet the cumulative FPL stats reflect the PRIOR full
    # season, so divide by ~38 — otherwise a player with 2 starts / 135 mins gets
    # treated as a nailed starter. Once the season is under way, use games played.
    games_ref = 38 if team_matches_played <= 0 else team_matches_played

    # season start & appearance rates
    start_rate = min(season_starts / games_ref, 1.0)
    appearances = 0
    if recent_minutes:
        appearances = sum(1 for m in recent_minutes if m > 0)
    # crude appearance count if we lack per-GW data
    est_appearances = max(appearances, round(season_minutes / 75.0)) or 1
    appear_rate = min(est_appearances / games_ref, 1.0)

    # recent-form start signal (weight recent games higher)
    if recent_minutes:
        last = recent_minutes[-5:]
        recent_start = sum(1 for m in last if m >= 60) / len(last)
        recent_appear = sum(1 for m in last if m > 0) / len(last)
        start_signal = 0.6 * recent_start + 0.4 * start_rate
        appear_signal = 0.6 * recent_appear + 0.4 * appear_rate
        sample_note = f"{len(last)} recent games"
    else:
        start_signal = start_rate
        appear_signal = appear_rate
        sample_note = "season averages"

    p_start = min(0.985, max(0.0, start_signal * avail_mult))
    p_appear = min(0.99, max(p_start, appear_signal * avail_mult))
    p_sub = max(0.0, p_appear - p_start)
    p_no_play = max(0.0, 1.0 - p_start - p_sub)

    # double gameweek rotation risk for squad (non-nailed) players
    if n_fixtures_this_gw > 1 and p_start < 0.9:
        rot = CONGESTION_PENALTY * (n_fixtures_this_gw - 1)
        moved = p_start * rot
        p_start -= moved
        p_sub += moved * 0.5
        p_no_play = max(0.0, 1.0 - p_start - p_sub)

    xmins = p_start * E_MIN_START + p_sub * E_MIN_SUB
    xmins *= n_fixtures_this_gw  # a DGW roughly doubles the minutes on offer
    p_60_plus = p_start * 0.92   # most starters clear 60'

    # confidence
    if status != "a" and (chance_of_playing is None or chance_of_playing < 75):
        confidence = "Low"
    elif recent_minutes and len(recent_minutes) >= 4 and 0.15 < p_start < 0.9:
        confidence = "Medium"
    elif p_start >= 0.9 or p_start <= 0.05:
        confidence = "High"
    else:
        confidence = "Medium"

    spread = {"High": 8.0, "Medium": 16.0, "Low": 26.0}[confidence]
    ci_low = max(0.0, xmins - spread)
    ci_high = min(90.0 * n_fixtures_this_gw, xmins + spread)

    # main reason
    if avail_reason:
        reason = f"Availability: {avail_reason}"
    elif n_fixtures_this_gw > 1:
        reason = f"Double gameweek ({n_fixtures_this_gw} fixtures); {sample_note}"
    elif p_start >= 0.9:
        reason = f"Nailed starter ({sample_note})"
    elif p_start <= 0.25:
        reason = f"Rotation/bench risk ({sample_note})"
    else:
        reason = f"Squad rotation possible ({sample_note})"

    return MinutesEstimate(
        xmins=round(xmins, 1),
        p_start=round(p_start, 3),
        p_sub=round(p_sub, 3),
        p_no_play=round(p_no_play, 3),
        p_60_plus=round(p_60_plus, 3),
        confidence=confidence,
        ci_low=round(ci_low, 1),
        ci_high=round(ci_high, 1),
        reason=reason,
    )

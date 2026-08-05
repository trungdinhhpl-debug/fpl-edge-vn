"""Expected minutes model (spec §6).

Outputs, per player per fixture:
  xmins, p_start, p_sub, p_no_play, p_60_plus, confidence, CI, main reason.

Inputs: season starts/minutes, recent per-GW minutes (if available), FPL
availability status + chance_of_playing_next_round, and fixture congestion
(double gameweeks add rotation risk).

xMins is treated as a first-class driver of xP — never a cosmetic label.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

E_MIN_START = 84.0     # avg minutes when a player starts
E_MIN_SUB = 20.0       # avg minutes when introduced from the bench
CONGESTION_PENALTY = 0.08   # rotation risk multiplier per extra fixture in a DGW

# Laplace smoothing for start/appearance rates. Without it, the first gameweek of
# a season yields absurd certainty: one start => 98% nailed, one benching => 0%
# chance of starting. PRIOR_GAMES acts as that many "imaginary" games at the
# baseline rate, so confidence grows only as real evidence accumulates.
PRIOR_GAMES = 2.0
PRIOR_START_RATE = 0.45
PRIOR_APPEAR_RATE = 0.60

# Players at newly-promoted clubs have ZERO Premier League minutes, so the
# minutes-based rate above would rate every one of them at ~2% to start — i.e.
# the model would claim the club fields nobody. For those squads we fall back to
# expected role, ranked by FPL's own price within each position (price is set by
# FPL to reflect expected involvement). Clearly a weak signal: confidence is
# forced to Low and it is replaced by real minutes within a few gameweeks.
TYPICAL_STARTERS = {1: 1, 2: 4, 3: 4, 4: 2}   # GK, DEF, MID, FWD in a typical XI
# trong đội hình chính / cận kề / còn lại
ROLE_START_PROB = (0.62, 0.34, 0.12)
ROLE_APPEAR_BONUS = (0.10, 0.22, 0.16)        # thêm xác suất vào sân từ ghế
# Thủ môn gần như "được ăn cả": thủ môn số 2 hiếm khi ra sân, và cũng không được
# tung vào từ ghế dự bị như cầu thủ ngoài sân.
ROLE_START_PROB_GK = (0.62, 0.10, 0.03)
ROLE_APPEAR_BONUS_GK = (0.02, 0.01, 0.01)


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


# P(clears 60' | started) when we have no evidence either way. Most starters do.
DEFAULT_COMPLETION = 0.92
# A recent appearance of at least this many minutes is treated as a start.
LIKELY_START_MINUTES = 45


def _completion_rate(recent_minutes: list[int] | None, season_starts: int,
                     season_minutes: int) -> tuple[float, str]:
    """P(still on the pitch at 60' | started), per player.

    This used to be a flat 0.92 for everybody, which made any "substitution
    risk" derived from it the same constant for every player in the game —
    a number that looks like an insight but carries none. Two real signals:

      1. recent games he started — did he actually see 60'?
      2. season minutes per start — a regular hooked on 65' shows up here.

    Falls back to the flat rate only when neither exists, and says so, so the
    caller can label the difference instead of implying certainty.
    """
    if recent_minutes:
        started = [m for m in recent_minutes[-6:] if m >= LIKELY_START_MINUTES]
        if len(started) >= 3:
            rate = sum(1 for m in started if m >= 60) / len(started)
            # never fully 0 or 1 on a handful of games (Laplace-style smoothing)
            smoothed = (rate * len(started) + DEFAULT_COMPLETION) / (len(started) + 1)
            return min(0.98, max(0.30, smoothed)), "recent starts"

    if season_starts >= 3:
        # Slightly optimistic: season minutes include cameos off the bench, so
        # this over-credits players who both start and sub. Directionally right,
        # and only used when recent form is unavailable.
        mins_per_start = season_minutes / season_starts
        rate = 0.5 + (mins_per_start - 60) / 60
        return min(0.97, max(0.35, rate)), "season minutes per start"

    return DEFAULT_COMPLETION, "no data — league default"


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
    no_pl_history: bool = False,
    role_rank: int | None = None,
    prior_reliability: float = 1.0,
) -> MinutesEstimate:
    avail_mult, avail_reason = _availability_multiplier(status, chance_of_playing)

    # Reference number of games for rate calculations. In pre-season no current
    # fixtures are finished yet the cumulative FPL stats reflect the PRIOR full
    # season, so divide by ~38 — otherwise a player with 2 starts / 135 mins gets
    # treated as a nailed starter. Once the season is under way, use games played.
    games_ref = 38 if team_matches_played <= 0 else team_matches_played

    # Season start & appearance rates, smoothed toward a neutral prior so a
    # one-game sample can't read as certainty in either direction.
    #
    # `prior_reliability` < 1 means last season describes this player less well
    # (he changed club, or his manager did). Rather than discarding the sample,
    # replace the share we no longer believe with the positional baseline: at
    # reliability r, (1 - r) of last season's games are swapped for prior games.
    #
    # Scaling PRIOR_GAMES alone is far too weak a lever — 2 imaginary games
    # against a 38-game sample moved a transferred starter's p_start by two
    # percentage points, which is not "reducing the weight" in any real sense.
    # Tying the prior to the sample size makes the dilution actually bite:
    # a new signing drops from ~0.77 to ~0.66.
    rel = max(0.1, min(1.0, prior_reliability))
    prior_games = PRIOR_GAMES + games_ref * (1.0 - rel)
    start_rate = min(
        (season_starts + prior_games * PRIOR_START_RATE) / (games_ref + prior_games), 1.0
    )
    appearances = 0
    if recent_minutes:
        appearances = sum(1 for m in recent_minutes if m > 0)
    # crude appearance count if we lack per-GW data. ceil (not round) so that
    # a short cameo still counts as an appearance, and 0 minutes stays 0 —
    # otherwise an unused player looks identical to a regular substitute.
    est_appearances = max(appearances, math.ceil(season_minutes / 75.0))
    appear_rate = min(
        (est_appearances + prior_games * PRIOR_APPEAR_RATE) / (games_ref + prior_games), 1.0
    )

    # Không có phút Ngoại hạng nào (đội mới lên hạng, hoặc tân binh từ giải khác)
    # -> ước lượng theo vai trò dự kiến, xếp hạng bằng giá FPL trong từng vị trí,
    # thay vì tính tỷ lệ từ mẫu rỗng (vốn ra ~2% cho tất cả).
    role_based = role_rank is not None and season_minutes == 0 and not recent_minutes
    if role_based:
        slots = TYPICAL_STARTERS.get(element_type, 4)
        tier = 0 if role_rank < slots else (1 if role_rank == slots else 2)
        probs = ROLE_START_PROB_GK if element_type == 1 else ROLE_START_PROB
        bonus = ROLE_APPEAR_BONUS_GK if element_type == 1 else ROLE_APPEAR_BONUS
        start_rate = probs[tier]
        appear_rate = min(0.95, start_rate + bonus[tier])

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
    completion, completion_basis = _completion_rate(
        recent_minutes, season_starts, season_minutes
    )
    p_60_plus = p_start * completion

    # confidence
    if status != "a" and (chance_of_playing is None or chance_of_playing < 75):
        confidence = "Low"
    elif role_based:
        confidence = "Low"   # suy từ giá/vai trò, chưa có phút Ngoại hạng nào
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
    elif role_based:
        reason = "Đội mới lên hạng — ước lượng theo vai trò/giá, chưa có dữ liệu Ngoại hạng"
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

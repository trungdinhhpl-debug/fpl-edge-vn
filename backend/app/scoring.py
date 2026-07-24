"""FPL scoring rules — configurable per season.

IMPORTANT (spec §7): scoring rules must be read from the *current* season config,
never hard-coded from a previous season. This module centralises them so the
projection engine can be re-pointed at a new ruleset without touching model code.

Values below reflect the 2025/26 ruleset, which added **Defensive Contribution**
points. Where the live FPL `bootstrap-static.game_settings` exposes a value we
prefer that at ingest time; these are the documented fallbacks.

Source: official FPL rules (fantasy.premierleague.com/help/rules) — see
`SCORING_SOURCE`.  Update `SEASON` + this table each season.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SEASON = "2025/26"
SCORING_SOURCE = "https://fantasy.premierleague.com/help/rules"


@dataclass(frozen=True)
class ScoringRules:
    # appearance
    points_play_under_60: int = 1
    points_play_60_plus: int = 2

    # goals by position code (FPL element_type: 1 GK, 2 DEF, 3 MID, 4 FWD)
    goal_points: dict[int, int] = field(
        default_factory=lambda: {1: 6, 2: 6, 3: 5, 4: 4}
    )
    assist_points: int = 3

    # clean sheet (requires >=60 mins)
    clean_sheet_points: dict[int, int] = field(
        default_factory=lambda: {1: 4, 2: 4, 3: 1, 4: 0}
    )

    # goalkeeper / defender goals-conceded: -1 per 2 conceded (>=60 mins)
    conceded_penalty_positions: tuple[int, ...] = (1, 2)
    points_per_two_conceded: int = -1

    # goalkeeper saves: +1 per 3 saves
    saves_per_point: int = 3

    # cards
    yellow_card_points: int = -1
    red_card_points: int = -3
    own_goal_points: int = -2
    penalty_miss_points: int = -2
    penalty_save_points: int = 5

    # bonus (0-3, from BPS ranking)
    max_bonus: int = 3

    # ---- Defensive Contribution (NEW 2025/26) ----
    # Defenders: +2 for reaching threshold_def CBIT (clearances+blocks+interceptions+tackles)
    # Mid/Fwd:   +2 for reaching threshold_att CBIT + ball recoveries
    defcon_points: int = 2
    defcon_threshold_def: int = 10   # element_type 2
    defcon_threshold_att: int = 12   # element_type 3 & 4
    defcon_positions: tuple[int, ...] = (2, 3, 4)


# module-level singleton (can be swapped by ingestion from game_settings)
RULES = ScoringRules()


def position_name(element_type: int) -> str:
    return {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}.get(element_type, "UNK")


def position_name_vi(element_type: int) -> str:
    return {1: "Thủ môn", 2: "Hậu vệ", 3: "Tiền vệ", 4: "Tiền đạo"}.get(
        element_type, "?"
    )

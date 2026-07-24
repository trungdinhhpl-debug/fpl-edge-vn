"""FPL squad rules — single source of truth for the optimizer & validators.

Kept separate so unit tests can assert every solver output is a legal squad
(spec §24 acceptance: "Solver phải tuân thủ đúng luật FPL hiện tại").
"""
from __future__ import annotations

from dataclasses import dataclass

# squad composition by element_type
SQUAD_SIZE = 15
SQUAD_BY_TYPE = {1: 2, 2: 5, 3: 5, 4: 3}   # GK, DEF, MID, FWD
MAX_PER_CLUB = 3
DEFAULT_BUDGET = 1000                        # tenths of a million (£100.0m)

# starting XI formation limits
XI_SIZE = 11
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}

BENCH_SIZE = 4
TRANSFER_HIT_COST = 4
MAX_BANKED_FT = 5   # 2025/26


@dataclass
class SquadValidation:
    valid: bool
    errors: list[str]


def validate_squad(elements: list, get_type, get_price, get_club) -> SquadValidation:
    """`elements` is a list of player ids; accessor callables map id -> attr."""
    errors: list[str] = []
    if len(elements) != SQUAD_SIZE:
        errors.append(f"Squad must have {SQUAD_SIZE} players, got {len(elements)}")

    by_type: dict[int, int] = {}
    by_club: dict[int, int] = {}
    total_cost = 0
    for pid in elements:
        t = get_type(pid)
        by_type[t] = by_type.get(t, 0) + 1
        c = get_club(pid)
        by_club[c] = by_club.get(c, 0) + 1
        total_cost += get_price(pid)

    for t, need in SQUAD_BY_TYPE.items():
        if by_type.get(t, 0) != need:
            errors.append(f"Position {t}: need {need}, got {by_type.get(t, 0)}")
    for c, cnt in by_club.items():
        if cnt > MAX_PER_CLUB:
            errors.append(f"Club {c}: {cnt} players exceeds max {MAX_PER_CLUB}")

    return SquadValidation(valid=not errors, errors=errors)


def validate_xi(types: list[int]) -> bool:
    if len(types) != XI_SIZE:
        return False
    counts = {t: types.count(t) for t in (1, 2, 3, 4)}
    if counts[1] != 1:
        return False
    for t in (2, 3, 4):
        if not (XI_MIN[t] <= counts[t] <= XI_MAX[t]):
            return False
    return True

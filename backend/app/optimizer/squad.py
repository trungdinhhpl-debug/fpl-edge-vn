"""Single-gameweek squad + starting XI + captain optimiser (MILP via PuLP/CBC).

Used by Free Hit, Wildcard (multi-GW value vector) and "pick best XI".
Maximises:  XI value + captain value (doubled) + bench_weight * bench value
subject to full FPL legality (budget, 2/5/5/3, max 3 per club, valid formation).
"""
from __future__ import annotations

from dataclasses import dataclass

import pulp

from app.optimizer.constraints import (
    MAX_PER_CLUB,
    SQUAD_BY_TYPE,
    XI_MAX,
    XI_MIN,
    XI_SIZE,
)


@dataclass
class OptPlayer:
    id: int
    element_type: int
    price: int          # tenths
    club: int
    value: float        # objective value (e.g. xP for the GW)
    cap_value: float    # value used when captained (e.g. ceiling for aggressive)


@dataclass
class SquadResult:
    status: str
    squad: list[int]
    starting: list[int]
    bench: list[int]
    captain: int | None
    vice_captain: int | None
    total_value: float
    xi_value: float
    total_cost: int
    formation: str


def optimize_squad(
    players: list[OptPlayer],
    budget: int,
    bench_weight: float = 0.12,
    forced_in: set[int] | None = None,
    forced_out: set[int] | None = None,
    time_limit: int = 20,
) -> SquadResult:
    forced_in = forced_in or set()
    forced_out = forced_out or set()
    players = [p for p in players if p.id not in forced_out]
    idx = {p.id: p for p in players}
    ids = list(idx.keys())

    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    squad = pulp.LpVariable.dicts("squad", ids, cat="Binary")
    start = pulp.LpVariable.dicts("start", ids, cat="Binary")
    cap = pulp.LpVariable.dicts("cap", ids, cat="Binary")

    # objective
    prob += (
        pulp.lpSum(idx[i].value * start[i] for i in ids)
        + pulp.lpSum(idx[i].cap_value * cap[i] for i in ids)
        + bench_weight * pulp.lpSum(idx[i].value * (squad[i] - start[i]) for i in ids)
    )

    # linking
    for i in ids:
        prob += start[i] <= squad[i]
        prob += cap[i] <= start[i]

    # squad composition
    prob += pulp.lpSum(squad[i] for i in ids) == sum(SQUAD_BY_TYPE.values())
    for t, need in SQUAD_BY_TYPE.items():
        prob += pulp.lpSum(squad[i] for i in ids if idx[i].element_type == t) == need

    # starting XI + formation
    prob += pulp.lpSum(start[i] for i in ids) == XI_SIZE
    for t in (1, 2, 3, 4):
        s = pulp.lpSum(start[i] for i in ids if idx[i].element_type == t)
        prob += s >= XI_MIN[t]
        prob += s <= XI_MAX[t]

    # exactly one captain
    prob += pulp.lpSum(cap[i] for i in ids) == 1

    # budget
    prob += pulp.lpSum(idx[i].price * squad[i] for i in ids) <= budget

    # max per club
    clubs = {idx[i].club for i in ids}
    for c in clubs:
        prob += pulp.lpSum(squad[i] for i in ids if idx[i].club == c) <= MAX_PER_CLUB

    # forced picks
    for i in forced_in:
        if i in squad:
            prob += squad[i] == 1

    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit))
    return _extract(prob, idx, ids, squad, start, cap)


def _extract(prob, idx, ids, squad, start, cap) -> SquadResult:
    status = pulp.LpStatus[prob.status]
    chosen = [i for i in ids if squad[i].value() and squad[i].value() > 0.5]
    starting = [i for i in ids if start[i].value() and start[i].value() > 0.5]
    bench = [i for i in chosen if i not in starting]
    caps = [i for i in ids if cap[i].value() and cap[i].value() > 0.5]
    captain = caps[0] if caps else None

    # vice = best XI value that isn't captain
    vice = None
    xi_sorted = sorted(starting, key=lambda i: idx[i].value, reverse=True)
    for i in xi_sorted:
        if i != captain:
            vice = i
            break

    # bench order: GK last-ish? FPL benches outfield by value; keep GK separate
    bench_out = sorted(
        [b for b in bench if idx[b].element_type != 1],
        key=lambda i: idx[i].value, reverse=True,
    )
    bench_gk = [b for b in bench if idx[b].element_type == 1]
    bench_ordered = bench_out + bench_gk

    counts = {t: sum(1 for i in starting if idx[i].element_type == t) for t in (2, 3, 4)}
    formation = f"{counts[2]}-{counts[3]}-{counts[4]}"

    xi_value = sum(idx[i].value for i in starting) + (
        idx[captain].value if captain else 0
    )
    total_cost = sum(idx[i].price for i in chosen)

    return SquadResult(
        status=status, squad=chosen, starting=starting, bench=bench_ordered,
        captain=captain, vice_captain=vice,
        total_value=round(sum(idx[i].value for i in chosen), 2),
        xi_value=round(xi_value, 2), total_cost=total_cost, formation=formation,
    )


def pick_best_xi(players: list[OptPlayer]) -> SquadResult:
    """Given a FIXED 15-man squad, choose the best legal XI + captain."""
    idx = {p.id: p for p in players}
    ids = list(idx.keys())
    prob = pulp.LpProblem("fpl_xi", pulp.LpMaximize)
    start = pulp.LpVariable.dicts("start", ids, cat="Binary")
    cap = pulp.LpVariable.dicts("cap", ids, cat="Binary")

    prob += (
        pulp.lpSum(idx[i].value * start[i] for i in ids)
        + pulp.lpSum(idx[i].cap_value * cap[i] for i in ids)
    )
    for i in ids:
        prob += cap[i] <= start[i]
    prob += pulp.lpSum(start[i] for i in ids) == XI_SIZE
    for t in (1, 2, 3, 4):
        s = pulp.lpSum(start[i] for i in ids if idx[i].element_type == t)
        prob += s >= XI_MIN[t]
        prob += s <= XI_MAX[t]
    prob += pulp.lpSum(cap[i] for i in ids) == 1
    prob.solve(pulp.PULP_CBC_CMD(msg=0))

    # reuse extractor with a dummy squad var (all in squad)
    squad = {i: _One() for i in ids}
    return _extract(prob, idx, ids, squad, start, cap)


class _One:
    def value(self):
        return 1.0

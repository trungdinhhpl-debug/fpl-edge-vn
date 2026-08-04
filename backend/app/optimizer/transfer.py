"""Transfer optimisation.

  next_gw_transfer()  — best transfers for the upcoming GW given current squad,
                        bank, free transfers and hit tolerance.
  long_term_plan()    — multi-GW MILP (spec §9) with free-transfer banking,
                        discounting of distant GWs, hit costs and risk penalties.
                        Compares roll vs act, and returns per-GW transfers, XI,
                        captain, bench, hits and remaining FT.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pulp

from app.optimizer.constraints import (
    MAX_PER_CLUB,
    SQUAD_BY_TYPE,
    TRANSFER_HIT_COST,
    XI_MAX,
    XI_MIN,
    XI_SIZE,
)


@dataclass
class TPlayer:
    id: int
    element_type: int
    price: int
    club: int
    values: dict[int, float]      # gw -> xP
    ceilings: dict[int, float] = field(default_factory=dict)
    minutes_risk: float = 0.0     # 0..1 penalty weight

    def val(self, gw: int) -> float:
        return self.values.get(gw, 0.0)

    def ceil(self, gw: int) -> float:
        return self.ceilings.get(gw, self.val(gw))


# ----------------------------------------------------------- next-GW solver ----
def next_gw_transfer(
    players: list[TPlayer],
    current_squad: set[int],
    gw: int,
    bank: int,
    free_transfers: int,
    max_transfers: int = 3,
    bench_weight: float = 0.12,
    time_limit: int = 20,
) -> dict:
    idx = {p.id: p for p in players}
    ids = list(idx.keys())
    # budget = current squad value (approx now_cost) + bank
    budget = sum(idx[i].price for i in current_squad if i in idx) + bank

    prob = pulp.LpProblem("next_gw", pulp.LpMaximize)
    squad = pulp.LpVariable.dicts("squad", ids, cat="Binary")
    start = pulp.LpVariable.dicts("start", ids, cat="Binary")
    cap = pulp.LpVariable.dicts("cap", ids, cat="Binary")
    hits = pulp.LpVariable("hits", lowBound=0, cat="Integer")

    # transfers out of current squad
    n_transfers = pulp.lpSum(1 - squad[i] for i in current_squad if i in idx)
    prob += hits >= n_transfers - free_transfers
    prob += n_transfers <= max_transfers

    prob += (
        pulp.lpSum(idx[i].val(gw) * start[i] for i in ids)
        + pulp.lpSum(idx[i].val(gw) * cap[i] for i in ids)
        + bench_weight * pulp.lpSum(idx[i].val(gw) * (squad[i] - start[i]) for i in ids)
        - TRANSFER_HIT_COST * hits
    )

    for i in ids:
        prob += start[i] <= squad[i]
        prob += cap[i] <= start[i]
    prob += pulp.lpSum(squad[i] for i in ids) == sum(SQUAD_BY_TYPE.values())
    for t, need in SQUAD_BY_TYPE.items():
        prob += pulp.lpSum(squad[i] for i in ids if idx[i].element_type == t) == need
    prob += pulp.lpSum(start[i] for i in ids) == XI_SIZE
    for t in (1, 2, 3, 4):
        s = pulp.lpSum(start[i] for i in ids if idx[i].element_type == t)
        prob += s >= XI_MIN[t]
        prob += s <= XI_MAX[t]
    prob += pulp.lpSum(cap[i] for i in ids) == 1
    prob += pulp.lpSum(idx[i].price * squad[i] for i in ids) <= budget
    for c in {idx[i].club for i in ids}:
        prob += pulp.lpSum(squad[i] for i in ids if idx[i].club == c) <= MAX_PER_CLUB

    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit))

    chosen = [i for i in ids if squad[i].value() and squad[i].value() > 0.5]
    starting = [i for i in ids if start[i].value() and start[i].value() > 0.5]
    caps = [i for i in ids if cap[i].value() and cap[i].value() > 0.5]
    transfers_in = [i for i in chosen if i not in current_squad]
    transfers_out = [i for i in current_squad if i not in chosen]
    n_hits = int(hits.value() or 0)

    return {
        "status": pulp.LpStatus[prob.status],
        "gameweek": gw,
        "squad": chosen,
        "starting": starting,
        "captain": caps[0] if caps else None,
        "transfers_in": transfers_in,
        "transfers_out": transfers_out,
        "n_transfers": len(transfers_in),
        "hits": n_hits,
        "hit_cost": n_hits * TRANSFER_HIT_COST,
        "xi_xp": round(sum(idx[i].val(gw) for i in starting)
                       + (idx[caps[0]].val(gw) if caps else 0), 2),
    }


# --------------------------------------------------------- multi-GW planner ----
def long_term_plan(
    players: list[TPlayer],
    current_squad: set[int],
    gws: list[int],
    bank: int,
    free_transfers: int,
    *,
    discount: float = 0.9,
    bench_weight: float = 0.1,
    risk_weight: float = 0.0,
    max_hits_total: int = 8,
    ceiling_weight: float = 0.0,
    time_limit: int = 25,
) -> dict:
    """MILP across `gws`. Returns per-GW picks + summary.

    `risk_weight` penalises starting minutes-risky players (safe plans use more).
    `ceiling_weight` blends upside into value (aggressive plans use more).
    """
    idx = {p.id: p for p in players}
    ids = list(idx.keys())
    T = gws
    t0 = T[0]
    budget = sum(idx[i].price for i in current_squad if i in idx) + bank

    def value(i: int, t: int) -> float:
        base = idx[i].val(t) + ceiling_weight * (idx[i].ceil(t) - idx[i].val(t))
        return base - risk_weight * idx[i].minutes_risk

    prob = pulp.LpProblem("long_term", pulp.LpMaximize)
    own = pulp.LpVariable.dicts("own", (ids, T), cat="Binary")
    start = pulp.LpVariable.dicts("start", (ids, T), cat="Binary")
    cap = pulp.LpVariable.dicts("cap", (ids, T), cat="Binary")
    tin = pulp.LpVariable.dicts("tin", (ids, T), cat="Binary")
    tout = pulp.LpVariable.dicts("tout", (ids, T), cat="Binary")
    hits = pulp.LpVariable.dicts("hits", T, lowBound=0, cat="Integer")
    # trần free transfer lấy từ luật mùa hiện tại (FPL game_config)
    from app.scoring import GAME

    max_ft = GAME.max_free_transfers
    ft = pulp.LpVariable.dicts("ft", T, lowBound=1, upBound=max_ft)

    # objective
    obj = []
    for k, t in enumerate(T):
        disc = discount ** k
        obj.append(disc * pulp.lpSum(value(i, t) * start[i][t] for i in ids))
        obj.append(disc * pulp.lpSum(value(i, t) * cap[i][t] for i in ids))
        obj.append(disc * bench_weight * pulp.lpSum(
            value(i, t) * (own[i][t] - start[i][t]) for i in ids))
        obj.append(-TRANSFER_HIT_COST * hits[t])
    # value of ending with banked free transfers
    obj.append(0.6 * ft[T[-1]])
    prob += pulp.lpSum(obj)

    for t in T:
        # composition
        prob += pulp.lpSum(own[i][t] for i in ids) == sum(SQUAD_BY_TYPE.values())
        for typ, need in SQUAD_BY_TYPE.items():
            prob += pulp.lpSum(own[i][t] for i in ids if idx[i].element_type == typ) == need
        # formation
        prob += pulp.lpSum(start[i][t] for i in ids) == XI_SIZE
        for typ in (1, 2, 3, 4):
            s = pulp.lpSum(start[i][t] for i in ids if idx[i].element_type == typ)
            prob += s >= XI_MIN[typ]
            prob += s <= XI_MAX[typ]
        prob += pulp.lpSum(cap[i][t] for i in ids) == 1
        prob += pulp.lpSum(idx[i].price * own[i][t] for i in ids) <= budget
        for c in {idx[i].club for i in ids}:
            prob += pulp.lpSum(own[i][t] for i in ids if idx[i].club == c) <= MAX_PER_CLUB
        for i in ids:
            prob += start[i][t] <= own[i][t]
            prob += cap[i][t] <= start[i][t]

    # transitions + FT accounting
    for k, t in enumerate(T):
        for i in ids:
            prev = current_squad and (1 if i in current_squad else 0) if k == 0 else None
            if k == 0:
                prob += own[i][t] - (1 if i in current_squad else 0) == tin[i][t] - tout[i][t]
            else:
                prob += own[i][t] - own[i][T[k - 1]] == tin[i][t] - tout[i][t]
            prob += tin[i][t] + tout[i][t] <= 1
        n_tr = pulp.lpSum(tout[i][t] for i in ids)
        prob += hits[t] >= n_tr - ft[t]
        if k == 0:
            prob += ft[t] == min(free_transfers, max_ft)
        else:
            prob += ft[t] <= ft[T[k - 1]] - pulp.lpSum(tout[i][T[k - 1]] for i in ids) + 1
    prob += pulp.lpSum(hits[t] for t in T) <= max_hits_total

    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit))

    # extract per-GW plan
    weeks = []
    prev_squad = set(current_squad)
    for t in T:
        squad_t = {i for i in ids if own[i][t].value() and own[i][t].value() > 0.5}
        starting_t = [i for i in ids if start[i][t].value() and start[i][t].value() > 0.5]
        caps_t = [i for i in ids if cap[i][t].value() and cap[i][t].value() > 0.5]
        t_in = list(squad_t - prev_squad)
        t_out = list(prev_squad - squad_t)
        weeks.append({
            "gameweek": t,
            "squad": sorted(squad_t),
            "starting": starting_t,
            "captain": caps_t[0] if caps_t else None,
            "transfers_in": t_in,
            "transfers_out": t_out,
            "n_transfers": len(t_in),
            "hits": int(hits[t].value() or 0),
            "free_transfers": round(ft[t].value() or 1, 1),
            "xi_xp": round(sum(idx[i].val(t) for i in starting_t)
                           + (idx[caps_t[0]].val(t) if caps_t else 0), 2),
        })
        prev_squad = squad_t

    total_xp = round(sum(w["xi_xp"] for w in weeks), 2)
    total_hits = sum(w["hits"] for w in weeks)
    return {
        "status": pulp.LpStatus[prob.status],
        "gameweeks": T,
        "weeks": weeks,
        "total_xp": total_xp,
        "total_hits": total_hits,
        "total_hit_cost": total_hits * TRANSFER_HIT_COST,
        "net_xp": round(total_xp - total_hits * TRANSFER_HIT_COST, 2),
    }

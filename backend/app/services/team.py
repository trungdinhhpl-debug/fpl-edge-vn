"""My Team analysis + optimisation orchestration (spec §9, §10, §14, §17).

Bridges DB projections into the optimizer's data types, runs the solvers, and
hydrates the raw ids back into explainable, data-backed recommendations.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.optimizer import (
    OptPlayer,
    TPlayer,
    long_term_plan,
    next_gw_transfer,
    optimize_squad,
    pick_best_xi,
)
from app.models import Player, PlayerProjection
from app.services.decision_tree import build_decision_tree
from app.services.common import (
    horizon_xp,
    planning_start_gw,
    player_public,
    projections_for_gw,
    team_lookup,
)

RISK_NUM = {"Low": 0.0, "Medium": 0.4, "High": 0.9, "Very High": 1.5}


# ------------------------------------------------------------- builders --------
def _players_and_projs(db: Session, gw: int):
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    projs = projections_for_gw(db, gw)
    return players, projs


def build_opt_players(db: Session, gw: int, mode: str = "max_ep") -> list[OptPlayer]:
    players, projs = _players_and_projs(db, gw)
    out = []
    for pid, p in players.items():
        pr = projs.get(pid)
        xp = pr.xp if pr else 0.0
        ceiling = pr.mc_ceiling if pr else 0.0
        risk = RISK_NUM.get(pr.overall_risk, 0.4) if pr else 0.4
        if mode == "aggressive":
            value = xp + 0.5 * max(0.0, ceiling - xp)
            cap_value = ceiling
        elif mode == "balanced":
            value = max(0.0, xp - 0.25 * risk)
            cap_value = xp
        else:  # max_ep
            value = xp
            cap_value = xp
        out.append(OptPlayer(
            id=pid, element_type=p.element_type, price=p.now_cost,
            club=p.team_id, value=round(value, 3), cap_value=round(cap_value, 3),
        ))
    return out


def _shortlist(db: Session, gws: list[int], current_squad: set[int],
               per_pos: int = 14) -> list[int]:
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    hx = horizon_xp(db, gws)
    by_pos: dict[int, list[int]] = defaultdict(list)
    for pid, p in players.items():
        by_pos[p.element_type].append(pid)
    keep: set[int] = set(current_squad)
    for t, pool in by_pos.items():
        pool.sort(key=lambda pid: hx.get(pid, 0.0), reverse=True)
        keep.update(pool[:per_pos])
    return [pid for pid in keep if pid in players]


def build_tplayers(db: Session, gws: list[int], ids: list[int]) -> list[TPlayer]:
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    rows = db.scalars(
        select(PlayerProjection).where(PlayerProjection.gameweek.in_(gws))
    ).all()
    val: dict[int, dict[int, float]] = defaultdict(dict)
    ceil: dict[int, dict[int, float]] = defaultdict(dict)
    risk: dict[int, float] = {}
    for r in rows:
        val[r.player_id][r.gameweek] = r.xp
        ceil[r.player_id][r.gameweek] = r.mc_ceiling
        risk[r.player_id] = RISK_NUM.get(r.minutes_risk, 0.4)
    out = []
    for pid in ids:
        p = players.get(pid)
        if not p:
            continue
        out.append(TPlayer(
            id=pid, element_type=p.element_type, price=p.now_cost, club=p.team_id,
            values=val.get(pid, {}), ceilings=ceil.get(pid, {}),
            minutes_risk=risk.get(pid, 0.4),
        ))
    return out


# ------------------------------------------------------------ hydration --------
def hydrate(db: Session, ids: list[int], gw: int) -> list[dict]:
    teams = team_lookup(db)
    players, projs = _players_and_projs(db, gw)
    out = []
    for pid in ids:
        p = players.get(pid)
        if not p:
            continue
        pr = projs.get(pid)
        base = player_public(p, teams.get(p.team_id))
        base.update({
            "xp": round(pr.xp, 2) if pr else 0.0,
            "xmins": round(pr.xmins, 1) if pr else 0.0,
            "ceiling": round(pr.mc_ceiling, 1) if pr else 0.0,
            "overall_risk": pr.overall_risk if pr else None,
            "opponent_team": pr.opponent_team if pr else None,
        })
        out.append(base)
    return out


def _squad_payload(db: Session, result, gw: int) -> dict:
    teams = team_lookup(db)
    starting = hydrate(db, result.starting, gw)
    bench = hydrate(db, result.bench, gw)
    for row in starting:
        row["is_captain"] = row["id"] == result.captain
        row["is_vice"] = row["id"] == result.vice_captain
    return {
        "status": result.status,
        "formation": result.formation,
        "starting": starting,
        "bench": bench,
        "captain": result.captain,
        "vice_captain": result.vice_captain,
        "total_cost": round(result.total_cost / 10.0, 1),
        "xi_xp": result.xi_value,
        "squad_ids": result.squad,
    }


# --------------------------------------------------------------- Free Hit ------
def optimize_free_hit(db: Session, gw: int | None = None, budget: int = 1000,
                      mode: str = "max_ep", locked: set[int] | None = None,
                      excluded: set[int] | None = None) -> dict:
    gw = gw or planning_start_gw(db)
    opt_players = build_opt_players(db, gw, mode)
    bench_weight = {"max_ep": 0.05, "balanced": 0.15, "aggressive": 0.1}.get(mode, 0.1)
    res = optimize_squad(
        opt_players, budget=budget, bench_weight=bench_weight,
        forced_in=locked, forced_out=excluded,
    )
    payload = _squad_payload(db, res, gw)
    payload.update({
        "gameweek": gw, "mode": mode, "budget": round(budget / 10.0, 1),
        "explanation": _free_hit_explanation(mode, payload),
    })
    return payload


def _free_hit_explanation(mode: str, payload: dict) -> dict:
    labels = {
        "max_ep": "Tối đa điểm kỳ vọng tuyệt đối, không quan tâm ownership.",
        "balanced": "Cân bằng EV với rủi ro phút thi đấu, bench đủ khả năng thay thế.",
        "aggressive": "Tăng trọng số ceiling/leverage để đuổi hạng, biến động cao hơn.",
    }
    cap = next((s for s in payload["starting"] if s.get("is_captain")), None)
    return {
        "mode_desc": labels.get(mode, ""),
        "captain_reason": (
            f"Captain {cap['name']}: xP {cap['xp']}, ceiling {cap['ceiling']}, "
            f"xMins {cap['xmins']}." if cap else ""
        ),
        "formation": payload["formation"],
    }


# ------------------------------------------------------------- Wildcard --------
def optimize_wildcard(db: Session, budget: int = 1000, horizon: int = 6,
                      mode: str = "balanced") -> dict:
    start = planning_start_gw(db)
    gws = list(range(start, start + horizon))
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    hx = horizon_xp(db, gws)
    # value = summed horizon xP; captain value uses the first-GW ceiling
    projs0 = projections_for_gw(db, start)
    opt_players = []
    for pid, p in players.items():
        value = hx.get(pid, 0.0)
        cap_value = value + (projs0[pid].mc_ceiling if pid in projs0 else 0.0)
        opt_players.append(OptPlayer(
            id=pid, element_type=p.element_type, price=p.now_cost, club=p.team_id,
            value=round(value, 3), cap_value=round(cap_value, 3),
        ))
    res = optimize_squad(opt_players, budget=budget, bench_weight=0.2)
    payload = _squad_payload(db, res, start)
    payload.update({"gameweeks": gws, "horizon": horizon,
                    "note": "Giá trị = tổng xP toàn horizon (đội hình giữ nguyên)."})
    return payload


# ------------------------------------------------------- Next-GW transfer ------
def optimize_next_gw(db: Session, squad_ids: list[int], bank: int = 0,
                     free_transfers: int = 1, max_transfers: int = 2) -> dict:
    gw = planning_start_gw(db)
    gws = list(range(gw, gw + 3))
    ids = _shortlist(db, gws, set(squad_ids), per_pos=22)
    tplayers = build_tplayers(db, gws, ids)
    res = next_gw_transfer(
        tplayers, set(squad_ids), gw, bank=bank, free_transfers=free_transfers,
        max_transfers=max_transfers,
    )
    res["transfers_in_detail"] = hydrate(db, res["transfers_in"], gw)
    res["transfers_out_detail"] = hydrate(db, res["transfers_out"], gw)
    res["starting_detail"] = hydrate(db, res["starting"], gw)
    res["explanations"] = [
        _explain_transfer(db, i, o, gws)
        for i, o in zip(res["transfers_in"], res["transfers_out"])
    ]
    # compare to rolling / doing nothing
    res["compare"] = _compare_roll_vs_act(db, squad_ids, gw, res)
    return res


def _explain_transfer(db: Session, in_id: int, out_id: int, gws: list[int]) -> dict:
    hx = horizon_xp(db, gws)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    pin, pout = players.get(in_id), players.get(out_id)
    teams = team_lookup(db)
    diff = round(hx.get(in_id, 0.0) - hx.get(out_id, 0.0), 2)
    reasons = [f"Chênh xP {len(gws)} vòng: {diff:+.1f}."]
    if pin and pin.penalties_order == 1:
        reasons.append(f"{pin.web_name} đá penalty số 1.")
    if pout and pout.status != "a":
        reasons.append(f"{pout.web_name} có cảnh báo ra sân ({pout.status}).")
    return {
        "in": {"id": in_id, "name": pin.web_name if pin else "?",
               "team": teams.get(pin.team_id).short_name if pin else "",
               "price": round(pin.now_cost / 10, 1) if pin else 0},
        "out": {"id": out_id, "name": pout.web_name if pout else "?",
                "team": teams.get(pout.team_id).short_name if pout else "",
                "price": round(pout.now_cost / 10, 1) if pout else 0},
        "xp_gain_horizon": diff,
        "reasons": reasons,
    }


def _compare_roll_vs_act(db: Session, squad_ids: list[int], gw: int, act_res: dict) -> dict:
    """Compare acting now vs rolling the transfer (spec §9 free-transfer value)."""
    projs = projections_for_gw(db, gw)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    opt = []
    for pid in squad_ids:
        p = players.get(pid)
        if not p:
            continue
        pr = projs.get(pid)
        opt.append(OptPlayer(id=pid, element_type=p.element_type, price=p.now_cost,
                             club=p.team_id, value=pr.xp if pr else 0.0,
                             cap_value=pr.xp if pr else 0.0))
    roll_xi = pick_best_xi(opt) if len(opt) == 15 else None
    return {
        "act_now_xi_xp": act_res["xi_xp"],
        "act_now_hits": act_res["hits"],
        "roll_xi_xp": roll_xi.xi_value if roll_xi else None,
        "recommendation": (
            "Thực hiện chuyển nhượng" if act_res["hits"] == 0
            and act_res["xi_xp"] > (roll_xi.xi_value if roll_xi else 0)
            else "Cân nhắc giữ (roll) free transfer nếu lợi ích nhỏ"
        ),
    }


# ----------------------------------------------------- Long-term planner -------
def optimize_long_term(db: Session, squad_ids: list[int], bank: int = 0,
                       free_transfers: int = 1, horizon: int = 5,
                       discount: float = 0.9) -> dict:
    start = planning_start_gw(db)
    gws = list(range(start, start + horizon))
    ids = _shortlist(db, gws, set(squad_ids), per_pos=14)
    tplayers = build_tplayers(db, gws, ids)
    cur = set(squad_ids)

    profiles = {
        "safe": dict(risk_weight=1.2, bench_weight=0.18, ceiling_weight=0.0,
                     max_hits_total=2, discount=max(discount, 0.92)),
        "balanced": dict(risk_weight=0.3, bench_weight=0.12, ceiling_weight=0.1,
                         max_hits_total=4, discount=discount),
        "aggressive": dict(risk_weight=0.0, bench_weight=0.08, ceiling_weight=0.5,
                           max_hits_total=8, discount=min(discount, 0.88)),
    }
    plans = {}
    for name, params in profiles.items():
        plan = long_term_plan(tplayers, cur, gws, bank=bank,
                              free_transfers=free_transfers, **params)
        for w in plan["weeks"]:
            w["transfers_in_detail"] = hydrate(db, w["transfers_in"], w["gameweek"])
            w["transfers_out_detail"] = hydrate(db, w["transfers_out"], w["gameweek"])
            w["captain_detail"] = hydrate(db, [w["captain"]] if w["captain"] else [], w["gameweek"])
        plan["risk_profile"] = name
        plan["summary"] = _plan_summary(name, plan)
        # A list of moves does not tell a manager why holding beats acting now;
        # the tree prices that decision. Pure arithmetic on the solved plan, so
        # it adds no solver time.
        plan["decision_tree"] = build_decision_tree(
            db, plan, gws, bank=bank, free_transfers=free_transfers
        )
        plans[name] = plan
    return {"gameweeks": gws, "plans": plans, "current_squad_size": len(squad_ids)}


def _plan_summary(name: str, plan: dict) -> dict:
    labels = {
        "safe": "Ưu tiên xMins cao, ít xoay tua, ít/không hit, giữ cấu trúc linh hoạt.",
        "balanced": "Tối đa hóa xP với rủi ro hợp lý, tận dụng fixture swing.",
        "aggressive": "Ceiling/variance cao cho người đuổi hạng, chấp nhận nhiều hit hơn.",
    }
    total_transfers = sum(w["n_transfers"] for w in plan["weeks"])
    return {
        "desc": labels.get(name, ""),
        "net_xp": plan["net_xp"],
        "total_transfers": total_transfers,
        "total_hits": plan["total_hits"],
        "main_risk": _main_risk(name),
    }


def _main_risk(name: str) -> str:
    return {
        "safe": "Có thể bỏ lỡ upside khi cần bứt phá.",
        "balanced": "Phụ thuộc vào dự báo xMins/xP giữ ổn định.",
        "aggressive": "Biến động lớn, chuỗi hit có thể phản tác dụng nếu differential blank.",
    }.get(name, "")


# --------------------------------------------------------- Team analysis -------
def analyze_team(db: Session, squad_ids: list[int], bank: int = 0) -> dict:
    start = planning_start_gw(db)
    gws = list(range(start, start + 5))
    teams = team_lookup(db)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    projs = projections_for_gw(db, start)
    hx = horizon_xp(db, gws)

    squad = [players[pid] for pid in squad_ids if pid in players]
    strengths, weaknesses, sells = [], [], []

    # concentration risk
    club_count: dict[int, int] = defaultdict(int)
    for p in squad:
        club_count[p.team_id] += 1
    for tid, c in club_count.items():
        if c >= 3:
            weaknesses.append(f"Tập trung {c} cầu thủ {teams[tid].short_name} — rủi ro cấu trúc.")

    # minutes / injury risk
    for p in squad:
        pr = projs.get(p.id)
        if p.status != "a":
            sells.append({"id": p.id, "name": p.web_name,
                          "reason": f"Cảnh báo ra sân ({p.status}).",
                          "xp_next5": round(hx.get(p.id, 0), 1)})
        elif pr and pr.overall_risk in ("High", "Very High"):
            sells.append({"id": p.id, "name": p.web_name,
                          "reason": f"Rủi ro {pr.overall_risk} (xMins {pr.xmins:.0f}).",
                          "xp_next5": round(hx.get(p.id, 0), 1)})

    # best asset
    if squad:
        best = max(squad, key=lambda p: hx.get(p.id, 0))
        strengths.append(f"Tài sản tốt nhất: {best.web_name} (xP5 {hx.get(best.id,0):.1f}).")

    # captain gap
    caps = sorted(squad, key=lambda p: (projs[p.id].xp if p.id in projs else 0), reverse=True)
    if caps and (caps[0].id not in projs or projs[caps[0].id].xp < 4):
        weaknesses.append("Thiếu lựa chọn captain điểm cao rõ ràng.")

    return {
        "strengths": strengths,
        "weaknesses": weaknesses,
        "sell_candidates": sorted(sells, key=lambda s: s["xp_next5"])[:6],
        "squad_xp_next5": round(sum(hx.get(p.id, 0) for p in squad), 1),
        "bank": round(bank / 10.0, 1),
    }

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
from app.services.field import field_eo, tilt
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


def build_opt_players(db: Session, gw: int, mode: str = "max_ep",
                      eo: dict[int, float] | None = None,
                      eo_weight: float = 0.0) -> list[OptPlayer]:
    players, projs = _players_and_projs(db, gw)
    eo = eo or {}
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
        # `cap_value` KHÔNG bị nghiêng theo EO: nó là phần TĂNG THÊM khi đeo băng,
        # mà đám đông thì không đổi khi bạn chọn đội trưởng — phần thưởng biên của
        # tấm băng đúng bằng xP dù người đó phổ biến hay không. Muốn xét EO cho
        # riêng băng đội trưởng thì cần mô hình phương sai, tức trang Đội trưởng.
        value = tilt(value, xp, eo.get(pid, 0.0), eo_weight)
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


def build_tplayers(db: Session, gws: list[int], ids: list[int],
                   eo: dict[int, float] | None = None,
                   eo_weight: float = 0.0) -> list[TPlayer]:
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    rows = db.scalars(
        select(PlayerProjection).where(PlayerProjection.gameweek.in_(gws))
    ).all()
    eo = eo or {}
    val: dict[int, dict[int, float]] = defaultdict(dict)
    ceil: dict[int, dict[int, float]] = defaultdict(dict)
    risk: dict[int, float] = {}
    for r in rows:
        # EO là số của MỘT thời điểm, còn giá trị ở đây trải trên nhiều vòng: cùng
        # một tỷ lệ sở hữu được áp cho mọi vòng trong horizon. Đám đông có đổi đội
        # hình trong lúc đó, nhưng ta không có cách nào biết trước — dùng số hôm
        # nay cho cả horizon là giả định, và nó nằm ở đây chứ không giấu đi đâu.
        val[r.player_id][r.gameweek] = tilt(
            r.xp, r.xp, eo.get(r.player_id, 0.0), eo_weight
        )
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
        # ĐIỂM THẬT, cộng từ xP của chính các cầu thủ đã chọn. Trước đây chỗ này
        # trả về `result.xi_value` — giá trị hàm mục tiêu của solver. Khi hàm mục
        # tiêu còn là xP thuần thì hai số trùng nhau nên không ai nhận ra; từ lúc
        # có núm EO, giá trị mục tiêu bị nghiêng đi và giao diện sẽ hiển thị một
        # con số bị bơm lên dưới nhãn "Tổng xP". Giữ riêng hai đại lượng.
        "xi_xp": round(
            sum(r["xp"] for r in starting)
            + next((r["xp"] for r in starting if r["is_captain"]), 0.0), 2
        ),
        # Số của solver, giữ lại để soi lỗi — không phải điểm.
        "objective_value": result.xi_value,
        "squad_ids": result.squad,
    }


# ------------------------------------------------------------ đám đông --------
def _field(db: Session, gw: int, eo_weight: float,
           league_id: int | None) -> tuple[dict[int, float], dict]:
    """EO + mô tả nguồn. Weight = 0 thì không đi lấy số làm gì cho tốn."""
    if not eo_weight:
        return {}, {
            "eo_weight": 0.0, "kind": "off",
            "label": "Không tính đám đông — tối đa điểm tuyệt đối.",
        }
    eo, source = field_eo(db, gw, league_id)
    return eo, {"eo_weight": eo_weight, **source}


def _tag_eo(rows: list[dict], eo: dict[int, float]) -> None:
    """Gắn EO đã dùng vào từng cầu thủ để giao diện giải thích được lựa chọn."""
    for r in rows:
        if eo:
            r["field_eo"] = round(eo.get(r["id"], 0.0), 1)


# --------------------------------------------------------------- Free Hit ------
def optimize_free_hit(db: Session, gw: int | None = None, budget: int = 1000,
                      mode: str = "max_ep", locked: set[int] | None = None,
                      excluded: set[int] | None = None, eo_weight: float = 0.0,
                      league_id: int | None = None) -> dict:
    gw = gw or planning_start_gw(db)
    eo, source = _field(db, gw, eo_weight, league_id)
    opt_players = build_opt_players(db, gw, mode, eo=eo, eo_weight=eo_weight)
    bench_weight = {"max_ep": 0.05, "balanced": 0.15, "aggressive": 0.1}.get(mode, 0.1)
    res = optimize_squad(
        opt_players, budget=budget, bench_weight=bench_weight,
        forced_in=locked, forced_out=excluded,
    )
    payload = _squad_payload(db, res, gw)
    _tag_eo(payload["starting"] + payload["bench"], eo)
    payload.update({
        "gameweek": gw, "mode": mode, "budget": round(budget / 10.0, 1),
        "field": source,
        "explanation": _free_hit_explanation(mode, payload, source),
    })
    return payload


def _free_hit_explanation(mode: str, payload: dict, source: dict | None = None) -> dict:
    # `mode` nói về khẩu vị RỦI RO, không nói gì về đám đông — câu mô tả max_ep
    # từng khẳng định "không quan tâm ownership", đúng cho tới khi có núm EO, và
    # từ đó thành một lời cam đoan sai ngay trên cùng màn hình với núm đang bật.
    labels = {
        "max_ep": "Tối đa điểm kỳ vọng tuyệt đối.",
        "balanced": "Cân bằng EV với rủi ro phút thi đấu, bench đủ khả năng thay thế.",
        "aggressive": "Tăng trọng số ceiling/leverage để đuổi hạng, biến động cao hơn.",
    }
    weight = (source or {}).get("eo_weight") or 0.0
    crowd = (
        "Không tính đám đông." if not weight
        else ("Có nghiêng RA KHỎI đội hình phổ biến" if weight > 0
              else "Có nghiêng VỀ PHÍA đội hình phổ biến")
        + f" (mức {abs(weight):g})."
    )
    cap = next((s for s in payload["starting"] if s.get("is_captain")), None)
    return {
        "mode_desc": f"{labels.get(mode, '')} {crowd}".strip(),
        "captain_reason": (
            f"Captain {cap['name']}: xP {cap['xp']}, ceiling {cap['ceiling']}, "
            f"xMins {cap['xmins']}." if cap else ""
        ),
        "formation": payload["formation"],
    }


# ------------------------------------------------------------- Wildcard --------
# Bench nặng hơn Free Hit: đội dựng bằng Wildcard phải sống qua nhiều vòng, nên
# một ghế dự bị không đá được là gánh nặng lặp lại chứ không phải sai một lần.
_WILDCARD_BENCH_WEIGHT = {"max_ep": 0.12, "balanced": 0.2, "aggressive": 0.15}


def _wildcard_values(db: Session, gws: list[int], mode: str,
                     eo: dict[int, float] | None = None,
                     eo_weight: float = 0.0) -> dict[int, dict]:
    """Giá trị cả horizon cho từng cầu thủ, theo đúng khẩu vị của `mode`.

    Ba chế độ khác nhau ở chỗ chúng coi cái gì là "tốt", giống hệt cách
    `build_opt_players` phân biệt chúng cho một vòng — chỉ khác là cộng dồn qua
    horizon:

      * max_ep     — tổng xP thuần.
      * balanced   — trừ rủi ro phút thi đấu MỖI VÒNG, vì một người dễ mất suất
                     làm bạn mất điểm lặp lại chứ không chỉ một lần.
      * aggressive — cộng nửa phần ceiling vượt trên trung bình: đuổi hạng cần
                     những tuần bùng nổ, không cần đều đặn.
    """
    rows = db.scalars(
        select(PlayerProjection).where(PlayerProjection.gameweek.in_(gws))
    ).all()
    agg: dict[int, dict] = defaultdict(
        lambda: {"xp": 0.0, "upside": 0.0, "risk": 0.0, "n_gw": 0}
    )
    for r in rows:
        a = agg[r.player_id]
        a["xp"] += r.xp
        a["upside"] += max(0.0, r.mc_ceiling - r.xp)
        a["risk"] += RISK_NUM.get(r.minutes_risk, 0.4)
        a["n_gw"] += 1

    eo = eo or {}
    out: dict[int, dict] = {}
    for pid, a in agg.items():
        if mode == "aggressive":
            value = a["xp"] + 0.5 * a["upside"]
        elif mode == "balanced":
            value = max(0.0, a["xp"] - 0.25 * a["risk"])
        else:
            value = a["xp"]
        out[pid] = {**a, "value": tilt(value, a["xp"], eo.get(pid, 0.0), eo_weight)}
    return out


def optimize_wildcard(db: Session, budget: int = 1000, horizon: int = 6,
                      mode: str = "balanced", locked: set[int] | None = None,
                      excluded: set[int] | None = None, eo_weight: float = 0.0,
                      league_id: int | None = None) -> dict:
    """Dựng 15 người từ con số 0 để tối đa điểm trên CẢ horizon.

    Cũng chính là bài toán chọn đội đầu mùa: chưa có đội cũ nên không có ràng
    buộc chuyển nhượng, chỉ còn ngân sách và luật đội hình.
    """
    start = planning_start_gw(db)
    gws = list(range(start, start + horizon))
    locked = set(locked or ())
    excluded = set(excluded or ())

    players = {p.id: p for p in db.scalars(select(Player)).all()}
    eo, source = _field(db, start, eo_weight, league_id)
    vals = _wildcard_values(db, gws, mode, eo=eo, eo_weight=eo_weight)
    projs0 = projections_for_gw(db, start)

    opt_players = []
    for pid, p in players.items():
        v = vals.get(pid, {}).get("value", 0.0)
        # Băng đội trưởng nhân đôi điểm của MỘT vòng, không phải của cả horizon.
        # Bản cũ đặt cap_value = giá trị horizon + ceiling, tức người được chọn
        # làm đội trưởng được cộng thêm nguyên một horizon nữa — đủ để bóp méo
        # cả 15 suất quanh một cái tên. Ở đây phần thưởng đội trưởng đúng bằng
        # những gì nhân đôi mang lại ở vòng đầu tiên.
        pr0 = projs0.get(pid)
        cap_extra = 0.0
        if pr0:
            cap_extra = pr0.mc_ceiling if mode == "aggressive" else pr0.xp
        opt_players.append(OptPlayer(
            id=pid, element_type=p.element_type, price=p.now_cost, club=p.team_id,
            value=round(v, 3), cap_value=round(cap_extra, 3),
        ))

    # Một khoá trỏ vào người không có trong pool sẽ bị optimizer lặng lẽ bỏ qua,
    # và người dùng chỉ thấy đội hình trả về thiếu đúng người họ vừa khoá mà
    # không hiểu vì sao. Đối chiếu trước rồi báo lại thành dữ liệu.
    pool_ids = {p.id for p in opt_players}
    locked_ignored = sorted(locked - pool_ids)
    locked_applied = sorted(locked & pool_ids)

    res = optimize_squad(
        opt_players, budget=budget,
        bench_weight=_WILDCARD_BENCH_WEIGHT.get(mode, 0.2),
        forced_in=set(locked_applied), forced_out=excluded,
    )
    payload = _squad_payload(db, res, start)

    for row in payload["starting"] + payload["bench"]:
        a = vals.get(row["id"], {})
        row["xp_horizon"] = round(a.get("xp", 0.0), 2)
        row["is_locked"] = row["id"] in locked
    _tag_eo(payload["starting"] + payload["bench"], eo)

    xi_horizon = sum(r["xp_horizon"] for r in payload["starting"])
    payload.update({
        "gameweeks": gws,
        "horizon": horizon,
        "mode": mode,
        "budget": round(budget / 10.0, 1),
        # Hai con số khác nhau, và trộn chúng là cách dễ nhất để tự lừa mình: cái
        # này là cả horizon với đội hình đứng yên, còn `xi_xp` (từ `_squad_payload`)
        # là điểm vòng tới đã tính băng đội trưởng.
        "xi_horizon_xp": round(xi_horizon, 1),
        "locked": locked_applied,
        "locked_ignored": locked_ignored,
        "excluded": sorted(excluded),
        "field": source,
        "note": (
            f"Giá trị tối ưu = tổng xP {horizon} vòng (GW{gws[0]}–GW{gws[-1]}) với "
            f"đội hình ĐỨNG YÊN — không tính chuyển nhượng về sau, không tính chip."
        ),
    })
    return payload


# ------------------------------------------------------- Next-GW transfer ------
def optimize_next_gw(db: Session, squad_ids: list[int], bank: int = 0,
                     free_transfers: int = 1, max_transfers: int = 2,
                     eo_weight: float = 0.0, league_id: int | None = None) -> dict:
    gw = planning_start_gw(db)
    gws = list(range(gw, gw + 3))
    ids = _shortlist(db, gws, set(squad_ids), per_pos=22)
    eo, source = _field(db, gw, eo_weight, league_id)
    tplayers = build_tplayers(db, gws, ids, eo=eo, eo_weight=eo_weight)
    res = next_gw_transfer(
        tplayers, set(squad_ids), gw, bank=bank, free_transfers=free_transfers,
        max_transfers=max_transfers,
    )
    res["transfers_in_detail"] = hydrate(db, res["transfers_in"], gw)
    res["transfers_out_detail"] = hydrate(db, res["transfers_out"], gw)
    res["starting_detail"] = hydrate(db, res["starting"], gw)
    res["field"] = source
    _tag_eo(res["transfers_in_detail"] + res["transfers_out_detail"]
            + res["starting_detail"], eo)
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
                       discount: float = 0.9, eo_weight: float = 0.0,
                       league_id: int | None = None) -> dict:
    start = planning_start_gw(db)
    gws = list(range(start, start + horizon))
    ids = _shortlist(db, gws, set(squad_ids), per_pos=14)
    eo, source = _field(db, start, eo_weight, league_id)
    tplayers = build_tplayers(db, gws, ids, eo=eo, eo_weight=eo_weight)
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
    return {"gameweeks": gws, "plans": plans, "current_squad_size": len(squad_ids),
            "field": source}


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

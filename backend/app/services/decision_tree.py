"""Decision-tree view of the long-term plan (spec §9).

A flat list of "GW4: C + D -> E + F" is not something a manager can act on. It
says what to do but never why waiting beats acting now, and it quietly assumes
nothing changes between now and GW4. This module turns a solved multi-GW plan
into:

  * a **main line** where every step — above all every Roll — carries a costed
    reason, not just a list of high-xP names;
  * **branches** for the two things that actually break these plans in practice:
    the target getting injured, and the target changing price before you buy.

The reasoning is arithmetic over the already-solved plan plus stored
projections: no second MILP solve, so this costs no measurable request time.

The central number is the **cost of acting now**. For a Roll at GW t we price
what would happen if you pulled the next planned move forward to t:

    gain  = sum over GWs t..m-1 of ( xP(player in) - xP(player out) )
    cost  = 4 x (extra hits this forces later, by replaying FT accounting)
    net   = gain - cost

Because the MILP already optimised the schedule, `net` is normally negative —
and that negative number, decomposed into its two halves, IS the explanation of
why you hold. When it is not negative the honest answer is that banking is not
what is driving the Roll, and we say so rather than inventing a reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Gameweek, InjuryReport, Player, PlayerProjection
from app.services.common import player_public, team_lookup

HIT_COST = 4
# FPL runs price changes once a day, ~01:30 UK time. Stated in UK time because
# the UK/Vietnam offset moves with British Summer Time and a hard-coded local
# hour would be wrong for half the season.
PRICE_CHANGE_TIME_UK = "01:30"
# Net transfers this GW at or above which we call buying momentum "strong".
# FPL's real price-change threshold is proprietary and ownership-scaled, so this
# is an indicator, never a prediction — see `_price_pressure`.
STRONG_NET_TRANSFERS = 60_000
MODERATE_NET_TRANSFERS = 25_000


@dataclass
class _Ctx:
    gws: list[int]
    xp: dict[int, dict[int, float]]          # player -> gw -> xP
    players: dict[int, Player]
    teams: dict
    deadlines: dict[int, datetime | None]
    injuries: dict[int, InjuryReport]
    max_ft: int


# ------------------------------------------------------------------ setup ----
def _load_ctx(db: Session, gws: list[int]) -> _Ctx:
    from app.scoring import GAME

    players = {p.id: p for p in db.scalars(select(Player)).all()}
    xp: dict[int, dict[int, float]] = {}
    for r in db.scalars(
        select(PlayerProjection).where(PlayerProjection.gameweek.in_(gws))
    ).all():
        xp.setdefault(r.player_id, {})[r.gameweek] = r.xp
    deadlines = {
        g.id: g.deadline_time
        for g in db.scalars(select(Gameweek).where(Gameweek.id.in_(gws))).all()
    }
    injuries = {
        r.player_id: r for r in db.scalars(select(InjuryReport)).all()
    }
    return _Ctx(gws, xp, players, team_lookup(db), deadlines, injuries, GAME.max_free_transfers)


def _signed(x: float) -> str:
    """'+1.4' / '−0.82' — never the '+-0.82' you get from f'+{x}'."""
    return f"+{x}" if x >= 0 else f"−{abs(x)}"


def _xp(ctx: _Ctx, pid: int, gw: int) -> float:
    return ctx.xp.get(pid, {}).get(gw, 0.0)


def _xp_sum(ctx: _Ctx, pid: int, gws: list[int]) -> float:
    return sum(_xp(ctx, pid, g) for g in gws)


def _brief(ctx: _Ctx, pid: int) -> dict:
    """Compact player card — enough for the UI to render a branch node."""
    p = ctx.players.get(pid)
    if not p:
        return {"id": pid, "name": f"#{pid}"}
    base = player_public(p, ctx.teams.get(p.team_id))
    return {
        k: base[k]
        for k in ("id", "name", "team", "position", "price", "status",
                  "chance_of_playing", "news", "selected_by_percent")
    }


# ------------------------------------------------- free-transfer accounting ---
def _replay_hits(counts: list[int], ft_start: float, max_ft: int,
                 extra_at: int | None = None) -> int:
    """Total hits taken if the plan makes `counts[k]` transfers each week.

    Mirrors the MILP's own FT recurrence (`ft_next = ft - used + 1`, capped at
    the season's max, floored at 1) so the counterfactual is priced with exactly
    the rule the plan was solved under. `extra_at` adds one transfer at that
    week — that is the "what if I acted now" case.
    """
    ft = ft_start
    hits = 0
    for k, n in enumerate(counts):
        used_wanted = n + (1 if k == extra_at else 0)
        paid_free = min(used_wanted, ft)
        hits += max(0, used_wanted - int(ft))
        ft = min(max_ft, max(1.0, ft - paid_free + 1))
    return hits


# ------------------------------------------------------------- move pairing ---
def _pair_moves(ctx: _Ctx, out_ids: list[int], in_ids: list[int]) -> list[dict]:
    """Pair each outgoing player with an incoming one of the same position.

    The solver returns two unordered sets. Zipping them by index — as the old
    UI did — can pair a defender out with a midfielder in and print a move that
    is not legal FPL. Squad-composition constraints guarantee the multiset of
    positions matches, so pairing within a position is always possible.
    """
    remaining = list(in_ids)
    pairs = []
    for o in out_ids:
        o_type = ctx.players[o].element_type if o in ctx.players else None
        match = next(
            (i for i in remaining
             if i in ctx.players and ctx.players[i].element_type == o_type),
            None,
        )
        if match is None:
            match = remaining[0] if remaining else None
        if match is not None:
            remaining.remove(match)
        pairs.append({"out": o, "in": match})
    return pairs


def _label(ctx: _Ctx, pairs: list[dict]) -> str:
    if not pairs:
        return "Roll"
    outs = " + ".join(_brief(ctx, p["out"])["name"] for p in pairs)
    ins = " + ".join(
        _brief(ctx, p["in"])["name"] if p["in"] else "?" for p in pairs
    )
    return f"{outs} → {ins}"


# ------------------------------------------------------------- explanations ---
def _why_roll(ctx: _Ctx, weeks: list[dict], k: int, counts: list[int],
              ft_start: float) -> list[dict]:
    """Costed reasons for holding the transfer at week index `k`."""
    t = weeks[k]["gameweek"]
    reasons: list[dict] = []

    nxt = next((j for j in range(k + 1, len(weeks)) if counts[j] > 0), None)
    if nxt is None:
        reasons.append({
            "kind": "no_move_worth_it",
            "text": ("Không có chuyển nhượng nào trong tầm nhìn này đủ bù chi phí — "
                     "giữ nguyên đội và tích luỹ free transfer."),
        })
        return reasons

    m = weeks[nxt]["gameweek"]
    pairs = _pair_moves(ctx, weeks[nxt]["transfers_out"], weeks[nxt]["transfers_in"])
    lead = pairs[0] if pairs else None
    bridge = [g for g in ctx.gws if t <= g < m]      # weeks you'd own them early

    # 1) what pulling the next move forward would actually buy you, in xP
    if lead and lead["in"]:
        gain = round(
            sum(_xp(ctx, lead["in"], g) - _xp(ctx, lead["out"], g) for g in bridge), 2
        )
        a, b = _brief(ctx, lead["in"])["name"], _brief(ctx, lead["out"])["name"]
        span = f"GW{t}" if len(bridge) == 1 else f"GW{bridge[0]}–{bridge[-1]}"
        if gain > 0:
            text = (f"Làm {b} → {a} ngay từ GW{t} chỉ được thêm "
                    f"**+{gain} xP** trong {span}.")
        else:
            text = (f"Ở {span}, {a} còn **kém {b} {abs(gain)} xP** — "
                    f"lịch của {a} chỉ tốt lên từ GW{m}.")
        reasons.append({
            "kind": "pull_forward_gain",
            "text": text,
            "numbers": {"xp_gain": gain, "from_gw": t, "target_gw": m,
                        "player_in": a, "player_out": b},
        })
    else:
        gain = 0.0

    # 2) what it would cost later — replay the FT rule with one extra move now
    base_hits = _replay_hits(counts, ft_start, ctx.max_ft)
    forced_hits = _replay_hits(counts, ft_start, ctx.max_ft, extra_at=k)
    extra = forced_hits - base_hits
    if extra > 0:
        cost = extra * HIT_COST
        reasons.append({
            "kind": "banking_funds_later_move",
            "text": (f"Tiêu free transfer bây giờ thì GW{m} không đủ FT cho "
                     f"{counts[nxt]} chuyển nhượng → phải chịu **−{cost}đ** điểm trừ."),
            "numbers": {"extra_hits": extra, "hit_cost": cost, "at_gw": m,
                        "transfers_needed": counts[nxt]},
        })
        net = round(gain - cost, 2)
        reasons.append({
            "kind": "net_cost_of_acting_now",
            "text": (f"Cộng lại: hành động ngay **{'lỗ' if net < 0 else 'lãi'} "
                     f"{abs(net)} điểm**. Đó là lý do giữ, không phải vì thiếu "
                     f"cầu thủ xP cao."),
            "numbers": {"net_xp": net},
        })
    else:
        # Banking is NOT the binding reason — say so instead of inventing one.
        reasons.append({
            "kind": "no_banking_pressure",
            "text": (f"Giữ FT ở đây không phải để dồn cho GW{m} (kế hoạch vẫn đủ "
                     f"FT nếu tiêu sớm) — lý do là {'lợi ích quá nhỏ' if gain <= 0.5 else 'thời điểm'}: "
                     f"nước đi chỉ đáng giá từ GW{m}."),
            "numbers": {"extra_hits": 0},
        })

    # 3) information that arrives before you would have to commit
    if lead and lead["in"]:
        doubt = _doubt(ctx, lead["in"])
        if doubt:
            reasons.append({
                "kind": "wait_for_information",
                "text": (f"Chờ tới hạn chót GW{m} còn có thêm thông tin: "
                         f"{_brief(ctx, lead['in'])['name']} {doubt}."),
            })
    return reasons


def _why_transfer(ctx: _Ctx, weeks: list[dict], k: int, pairs: list[dict]) -> list[dict]:
    """Why this move, at this GW, and whether a hit is justified."""
    t = weeks[k]["gameweek"]
    rest = [g for g in ctx.gws if g >= t]
    reasons = []

    total = 0.0
    for pr in pairs:
        if not pr["in"]:
            continue
        gain = round(_xp_sum(ctx, pr["in"], rest) - _xp_sum(ctx, pr["out"], rest), 2)
        total += gain
        a, b = _brief(ctx, pr["in"])["name"], _brief(ctx, pr["out"])["name"]
        reasons.append({
            "kind": "move_value",
            "text": (f"{b} → {a}: **{_signed(gain)} xP** cộng dồn từ GW{t} đến "
                     f"GW{ctx.gws[-1]}."),
            "numbers": {"xp_gain": gain, "player_in": a, "player_out": b},
        })

    hits = weeks[k]["hits"]
    if hits:
        cost = hits * HIT_COST
        reasons.append({
            "kind": "hit_justified" if total > cost else "hit_marginal",
            "text": (f"Chịu **−{cost}đ** điểm trừ: {'đáng' if total > cost else 'sát nút'} "
                     f"vì tổng lợi ích {_signed(round(total, 2))} xP."),
            "numbers": {"hit_cost": cost, "xp_gain": round(total, 2),
                        "net": round(total - cost, 2)},
        })

    # A move in the final planned GW is only ever credited for that one week,
    # because the horizon stops there — it is not a verdict, it is the edge of
    # what we can see. Say so instead of letting a thin number look decisive.
    if t == ctx.gws[-1] and len(ctx.gws) > 1:
        reasons.append({
            "kind": "horizon_edge",
            "text": (f"GW{t} là vòng cuối của tầm nhìn, nên lợi ích trên chỉ tính "
                     f"được **đúng 1 vòng**. Hãy chạy lại kế hoạch với tầm nhìn dài "
                     f"hơn trước khi thực hiện nước đi này."),
            "numbers": {"gws_counted": 1},
        })
    return reasons


def _doubt(ctx: _Ctx, pid: int) -> str | None:
    """Human phrase describing an availability doubt, or None if fit."""
    p = ctx.players.get(pid)
    if not p:
        return None
    if p.status and p.status != "a":
        label = {"d": "đang có nghi ngờ ra sân", "i": "đang chấn thương",
                 "s": "đang bị treo giò", "u": "không còn thi đấu ở giải",
                 "n": "chưa đủ điều kiện ra sân"}.get(p.status, f"trạng thái `{p.status}`")
        chance = f", {p.chance_of_playing_next_round}% khả năng ra sân" \
            if p.chance_of_playing_next_round is not None else ""
        return f"{label}{chance}"
    if p.chance_of_playing_next_round is not None and p.chance_of_playing_next_round < 100:
        return f"chỉ {p.chance_of_playing_next_round}% khả năng ra sân"
    return None


# ------------------------------------------------------------- price signal ---
def _price_pressure(ctx: _Ctx, pid: int) -> dict | None:
    """Net-transfer momentum for a player, as an indicator — not a prediction.

    FPL's price-change threshold is proprietary and scales with ownership, so we
    report the raw net transfers this GW and a confidence label, and never claim
    to know that a change will happen.
    """
    p = ctx.players.get(pid)
    if not p:
        return None
    net = (p.transfers_in_event or 0) - (p.transfers_out_event or 0)
    if abs(net) < MODERATE_NET_TRANSFERS:
        return None
    strong = abs(net) >= STRONG_NET_TRANSFERS
    return {
        "net_transfers": net,
        "direction": "rise" if net > 0 else "fall",
        "confidence": "Medium" if strong else "Low",
        "basis": (f"{net:+,} lượt chuyển nhượng ròng trong vòng này "
                  f"(sở hữu {p.selected_by_percent}%)"),
        "caveat": ("Ngưỡng đổi giá của FPL không công khai và phụ thuộc tỷ lệ sở "
                   "hữu — đây là chỉ báo động lượng, không phải dự báo chắc chắn."),
    }


# ---------------------------------------------------------------- branches ---
def _replacement(ctx: _Ctx, target: int, out_id: int, bank: int,
                 exclude: set[int], from_gw: int) -> list[dict]:
    """Best same-position alternatives to `target`, affordable in place of `out_id`."""
    tgt = ctx.players.get(target)
    out = ctx.players.get(out_id)
    if not tgt or not out:
        return []
    budget = out.now_cost + bank
    rest = [g for g in ctx.gws if g >= from_gw]
    cands = []
    for pid, p in ctx.players.items():
        if pid in exclude or pid == target:
            continue
        if p.element_type != tgt.element_type or p.now_cost > budget:
            continue
        if p.status and p.status != "a":
            continue
        total = _xp_sum(ctx, pid, rest)
        if total <= 0:
            continue
        cands.append((total, pid))
    cands.sort(reverse=True)
    best_target = _xp_sum(ctx, target, rest)
    out_rows = []
    for total, pid in cands[:2]:
        row = _brief(ctx, pid)
        row["xp_rest"] = round(total, 2)
        row["xp_vs_target"] = round(total - best_target, 2)
        out_rows.append(row)
    return out_rows


def _branches(ctx: _Ctx, weeks: list[dict], bank: int,
              planned_squad: set[int]) -> list[dict]:
    out: list[dict] = []
    for k, w in enumerate(weeks):
        if not w["transfers_in"]:
            continue
        pairs = _pair_moves(ctx, w["transfers_out"], w["transfers_in"])
        t = w["gameweek"]
        deadline = ctx.deadlines.get(t)
        for pr in pairs:
            tgt = pr["in"]
            if not tgt:
                continue
            name = _brief(ctx, tgt)["name"]

            # --- branch: the target is not available when you get there ---
            doubt = _doubt(ctx, tgt)
            report = ctx.injuries.get(tgt)
            if doubt or report:
                alts = _replacement(ctx, tgt, pr["out"], bank,
                                    planned_squad | {tgt}, t)
                evidence = doubt or ""
                if report and report.news:
                    evidence = (evidence + " · " if evidence else "") + report.news.strip()
                out.append({
                    "trigger": "injury",
                    "at_gameweek": t,
                    "condition": f"Nếu {name} chấn thương / không kịp bình phục",
                    "player": _brief(ctx, tgt),
                    "evidence": evidence or "Có báo cáo chấn thương đang theo dõi",
                    "confidence": (report.impact if report else "Low"),
                    "action": {
                        "label": (f"{_brief(ctx, pr['out'])['name']} → "
                                  f"{alts[0]['name']}" if alts else "Hoãn nước đi, giữ FT"),
                        "alternatives": alts,
                        "cost_vs_main": alts[0]["xp_vs_target"] if alts else None,
                    },
                })

            # --- branch: price moves before you buy ---
            pressure = _price_pressure(ctx, tgt)
            if pressure and pressure["direction"] == "rise" and k > 0:
                out.append({
                    "trigger": "price_rise",
                    "at_gameweek": t,
                    "condition": f"Nếu {name} tăng giá trước GW{t}",
                    "player": _brief(ctx, tgt),
                    "evidence": pressure["basis"],
                    "confidence": pressure["confidence"],
                    "caveat": pressure["caveat"],
                    "action": {
                        "label": (f"Thực hiện {_label(ctx, [pr])} sớm — trước lần đổi "
                                  f"giá kế tiếp (~{PRICE_CHANGE_TIME_UK} giờ Anh hằng đêm)"),
                        "deadline": deadline.isoformat() if deadline else None,
                        "price_change_time_uk": PRICE_CHANGE_TIME_UK,
                        "tradeoff": ("Đổi lại mất phần free transfer đang tích — chỉ nên "
                                     "làm nếu 0.1tr giá trị đội đáng hơn con số ở nhánh chính."),
                    },
                })

        # --- branch: an outgoing player is falling and you are holding him ---
        for pr in pairs:
            pressure = _price_pressure(ctx, pr["out"])
            if pressure and pressure["direction"] == "fall" and k > 0:
                nm = _brief(ctx, pr["out"])["name"]
                out.append({
                    "trigger": "price_fall",
                    "at_gameweek": t,
                    "condition": f"Nếu {nm} giảm giá trước GW{t}",
                    "player": _brief(ctx, pr["out"]),
                    "evidence": pressure["basis"],
                    "confidence": pressure["confidence"],
                    "caveat": pressure["caveat"],
                    "action": {
                        "label": f"Bán {nm} sớm để giữ giá trị đội",
                        "deadline": deadline.isoformat() if deadline else None,
                        "tradeoff": ("Bán sớm nghĩa là mất số xP mà kế hoạch chính còn "
                                     "trông vào cầu thủ này ở các vòng trước GW"
                                     f"{t}."),
                    },
                })
    return out


# -------------------------------------------------------------------- entry ---
def build_decision_tree(db: Session, plan: dict, gws: list[int], bank: int,
                        free_transfers: int) -> dict:
    """Turn a solved long-term plan into a main line plus conditional branches."""
    ctx = _load_ctx(db, gws)
    weeks = plan["weeks"]
    counts = [w["n_transfers"] for w in weeks]

    main_line = []
    for k, w in enumerate(weeks):
        pairs = _pair_moves(ctx, w["transfers_out"], w["transfers_in"]) if w["transfers_in"] else []
        deadline = ctx.deadlines.get(w["gameweek"])
        rolling = not pairs
        main_line.append({
            "gameweek": w["gameweek"],
            "deadline": deadline.isoformat() if deadline else None,
            "action": "roll" if rolling else ("transfer_with_hit" if w["hits"] else "transfer"),
            "label": _label(ctx, pairs),
            "moves": [
                {"out": _brief(ctx, p["out"]),
                 "in": _brief(ctx, p["in"]) if p["in"] else None}
                for p in pairs
            ],
            "hits": w["hits"],
            "hit_cost": w["hits"] * HIT_COST,
            "free_transfers": w["free_transfers"],
            "xi_xp": w["xi_xp"],
            "why": (_why_roll(ctx, weeks, k, counts, free_transfers) if rolling
                    else _why_transfer(ctx, weeks, k, pairs)),
        })

    planned_squad: set[int] = set()
    for w in weeks:
        planned_squad |= set(w["squad"])

    return {
        "main_line": main_line,
        "branches": _branches(ctx, weeks, bank, planned_squad),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": ("Mỗi bước Roll đều kèm chi phí của việc hành động ngay, tính bằng "
                 "chính luật free transfer mà bài toán tối ưu đã dùng. Nhánh điều "
                 "kiện chỉ xuất hiện khi dữ liệu thật sự có tín hiệu."),
    }

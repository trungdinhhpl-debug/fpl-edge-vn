"""Chip Calendar — một bảng thống nhất cho cả 8 chip của mùa 2026/27.

Trước file này website chỉ có Free Hit Lab: tối ưu một đội hình cho MỘT vòng, không
trả lời được câu hỏi thật của người chơi — *dùng chip nào, ở vòng nào, và giữ lại
thì được gì*. Bảng này xếp mọi chip còn dùng được cạnh nhau trên mọi vòng trong
cửa sổ của nó.

Khung chip đọc từ `seasons.chips_json` (FPL công bố), KHÔNG ghi cứng. Mùa 2026/27
API trả về: wildcard & freehit GW2–19 rồi GW20–38; bboost & 3xc GW1–19 rồi GW20–38.
Lưu ý wildcard/freehit **không dùng được ở GW1** — chi tiết đó chỉ có trong API.

Ba giới hạn được khai báo thẳng trong payload chứ không bị làm mờ:

1. **Ngoài tầm dự báo thì không có điểm.** Dự báo chỉ tồn tại tới
   `PROJECTION_HORIZON` vòng (mặc định 8). Vòng nào chưa có dự báo thì `gain` là
   `None` kèm `status="no_projection"` — không nội suy, không lấy trung bình vòng
   khác. Một con số như "Free Hit GW18: +15.8" khi mới GW1 là số bịa, không phải
   dự báo.

2. **Xác suất Blank/Double không suy ra được từ dữ liệu.** Blank và double sinh ra
   khi FA Cup / cúp châu Âu buộc hoãn trận, và lịch hoãn chỉ được công bố dần
   trong mùa. Lịch đang công bố hiện có đúng 10 trận mỗi vòng cho cả 38 vòng, nên
   mọi "xác suất DGW vòng 34" lúc này là số tự đặt. Ta báo cáo blank/double **đã
   có trong lịch công bố** kèm cờ `provisional`, và `probability=None` với lý do.

3. **Giá trị của việc giữ chip chỉ tính được trong tầm dự báo.** Nếu vòng tốt nhất
   còn lại nằm ngoài tầm, `hold_value` là `None` — không phải 0.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, PlayerProjection, Season
from app.optimizer import OptPlayer, optimize_squad, pick_best_xi
from app.services.common import (
    gw_fixture_count_by_team,
    planning_start_gw,
    projections_for_gw,
    team_lookup,
)

# Tên chip trong API FPL -> tên hiển thị. Khoá là `chip_type`+`name` của FPL.
CHIP_LABELS = {
    "wildcard": "Wildcard",
    "freehit": "Free Hit",
    "bboost": "Bench Boost",
    "3xc": "Triple Captain",
}

# Ngưỡng số vòng còn lại trong cửa sổ để xếp mức rủi ro hết hạn. Đây là quy tắc
# do ta đặt (không phải luật FPL) nên được nêu thẳng trong payload.
EXPIRY_THRESHOLDS = {"high": 3, "medium": 7}

# Wildcard được so trên một khoảng CỐ ĐỊNH bao nhiêu vòng. Nếu đo "từ vòng này
# tới hết tầm dự báo" thì gain tự động giảm dần theo vòng — không phải vì dùng
# muộn kém hơn mà vì khoảng đo ngắn lại, và bảng sẽ luôn khuyên dùng vòng sớm
# nhất. Cố định khoảng đo làm các vòng so được với nhau; vòng nào không còn đủ
# `WILDCARD_WINDOW` vòng dự báo phía sau thì để trống thay vì báo một số méo.
WILDCARD_WINDOW = 6

# Chênh lệch điểm dưới mức này thì coi như không phân biệt được: sai số của mô
# hình xP lớn hơn nhiều. Không khuyên "giữ chip" chỉ vì hơn 0.1 điểm.
MIN_MEANINGFUL_MARGIN = 1.0


@dataclass
class ChipOption:
    """Một chip ở một vòng cụ thể."""

    chip: str
    label: str
    gameweek: int
    gain: float | None = None          # điểm cộng thêm so với không dùng chip
    status: str = "ok"                 # ok | no_projection | needs_squad | outside_window
    detail: str = ""
    blank_double: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "chip": self.chip,
            "label": self.label,
            "gameweek": self.gameweek,
            "gain": None if self.gain is None else round(self.gain, 1),
            "status": self.status,
            "detail": self.detail,
            "blank_double": self.blank_double,
        }


# ------------------------------------------------------------------ khung ------
def chip_windows(db: Session) -> list[dict]:
    """Cửa sổ dùng của từng chip, đọc nguyên văn từ FPL.

    Trả về danh sách {chip, label, start_event, stop_event, set_index} — set_index
    0 là bộ nửa đầu mùa, 1 là bộ nửa sau. Bộ nửa đầu KHÔNG chuyển sang nửa sau.
    """
    season = db.scalar(select(Season).where(Season.is_current.is_(True)))
    if not season or not season.chips_json:
        return []
    try:
        raw = json.loads(season.chips_json)
    except ValueError:
        return []

    out: list[dict] = []
    for c in raw:
        name = c.get("name")
        if name not in CHIP_LABELS:
            continue
        start, stop = c.get("start_event"), c.get("stop_event")
        if start is None or stop is None:
            continue
        out.append({
            "chip": name,
            "label": CHIP_LABELS[name],
            "start_event": int(start),
            "stop_event": int(stop),
        })
    # bộ nào bắt đầu sớm hơn là bộ nửa đầu mùa
    out.sort(key=lambda w: (w["chip"], w["start_event"]))
    seen: dict[str, int] = {}
    for w in out:
        idx = seen.get(w["chip"], 0)
        w["set_index"] = idx
        seen[w["chip"]] = idx + 1
    return out


def projection_horizon(db: Session) -> tuple[int, int] | None:
    """(vòng nhỏ nhất, vòng lớn nhất) đang CÓ dự báo, hoặc None nếu chưa chạy."""
    lo = db.scalar(select(PlayerProjection.gameweek).order_by(PlayerProjection.gameweek))
    hi = db.scalar(
        select(PlayerProjection.gameweek).order_by(PlayerProjection.gameweek.desc())
    )
    if lo is None or hi is None:
        return None
    return int(lo), int(hi)


# ------------------------------------------------------- blank / double --------
def fixture_outlook(db: Session, gw: int, teams: dict) -> dict:
    """Blank/double của một vòng theo lịch ĐANG công bố, kèm mức chắc chắn.

    `probability` luôn là None: blank/double do lịch hoãn cúp sinh ra, mà lịch hoãn
    được công bố dần trong mùa. Suy ra xác suất từ lịch hiện tại là bịa số, nên
    thay vì đoán, ta nói rõ cái đang biết và cái chưa biết.
    """
    counts = gw_fixture_count_by_team(db, gw)
    if not counts:
        return {
            "known": False,
            "probability": None,
            "note": "Chưa có lịch cho vòng này.",
        }
    blanks = sorted(tid for tid, n in counts.items() if n == 0)
    doubles = sorted(tid for tid, n in counts.items() if n >= 2)
    short = lambda ids: [teams[t].short_name for t in ids if t in teams]  # noqa: E731
    return {
        "known": True,
        "blank_teams": short(blanks),
        "double_teams": short(doubles),
        "is_blank": bool(blanks),
        "is_double": bool(doubles),
        # lịch chỉ chắc chắn sau khi vòng đã đá; trước đó cúp còn có thể xáo trộn
        "provisional": True,
        "probability": None,
        "note": (
            "Theo lịch đang công bố. Blank/double phát sinh khi FA Cup hoặc cúp "
            "châu Âu buộc hoãn trận, và lịch hoãn chỉ được công bố dần trong mùa "
            "— nên đây là ảnh chụp hiện tại, không phải kết luận."
        ),
    }


# ------------------------------------------------------------ tính gain --------
def _squad_opt_players(db: Session, gw: int, squad_ids: list[int]) -> list[OptPlayer]:
    """OptPlayer cho đúng 15 người trong đội, giá trị = xP vòng `gw`."""
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    projs = projections_for_gw(db, gw)
    out = []
    for pid in squad_ids:
        p = players.get(pid)
        if not p:
            continue
        pr = projs.get(pid)
        xp = pr.xp if pr else 0.0
        out.append(OptPlayer(
            id=pid, element_type=p.element_type, price=p.now_cost, club=p.team_id,
            value=round(xp, 3), cap_value=round(xp, 3),
        ))
    return out


def _xi_and_bench(db: Session, gw: int, squad_ids: list[int]):
    """Đội hình ra sân tốt nhất + băng ghế của ĐỘI HIỆN TẠI ở vòng `gw`."""
    opt = _squad_opt_players(db, gw, squad_ids)
    if len(opt) < 15:
        return None
    return pick_best_xi(opt), {p.id: p.value for p in opt}


def bench_boost_gain(db: Session, gw: int, squad_ids: list[int]) -> tuple[float | None, str, str]:
    """Bench Boost = cộng thêm xP của 4 người trên băng ghế.

    Không so với đội hình khác: chip chỉ mở rộng số người tính điểm, nên phần lợi
    đúng bằng tổng xP băng ghế của chính đội đang có.
    """
    got = _xi_and_bench(db, gw, squad_ids)
    if got is None:
        return None, "Cần đúng 15 cầu thủ trong đội.", "needs_squad"
    res, values = got
    bench_xp = sum(values.get(pid, 0.0) for pid in res.bench)
    names = len(res.bench)
    return bench_xp, f"Tổng xP của {names} cầu thủ dự bị ở vòng này.", "ok"


def triple_captain_gain(db: Session, gw: int, squad_ids: list[int]) -> tuple[float | None, str, str]:
    """Triple Captain = thêm MỘT lần xP của đội trưởng (từ x2 lên x3)."""
    got = _xi_and_bench(db, gw, squad_ids)
    if got is None:
        return None, "Cần đúng 15 cầu thủ trong đội."
    res, values = got
    if res.captain is None:
        return None, "Không chọn được đội trưởng.", "needs_squad"
    cap_xp = values.get(res.captain, 0.0)
    return cap_xp, "Thêm một lần xP của đội trưởng (x2 -> x3).", "ok"


def free_hit_gain(db: Session, gw: int, squad_ids: list[int],
                  budget: int) -> tuple[float | None, str, str]:
    """Free Hit = đội tối ưu một vòng, so với đội hiện tại ở đúng vòng đó.

    Cả hai phía đều nhân đôi đội trưởng để so cùng thước đo.
    """
    got = _xi_and_bench(db, gw, squad_ids)
    if got is None:
        return None, "Cần đúng 15 cầu thủ trong đội."
    cur, values = got
    cur_xp = sum(values.get(pid, 0.0) for pid in cur.starting)
    if cur.captain is not None:
        cur_xp += values.get(cur.captain, 0.0)

    from app.services.team import build_opt_players

    pool = build_opt_players(db, gw, "max_ep")
    best = optimize_squad(pool, budget=budget, bench_weight=0.05)
    idx = {p.id: p.value for p in pool}
    fh_xp = sum(idx.get(pid, 0.0) for pid in best.starting)
    if best.captain is not None:
        fh_xp += idx.get(best.captain, 0.0)
    return fh_xp - cur_xp, (
        f"Đội tối ưu một vòng ({round(fh_xp, 1)} xP) trừ đội hiện tại "
        f"({round(cur_xp, 1)} xP), cùng nhân đôi đội trưởng."
    ), "ok"


def wildcard_gain(db: Session, gw: int, squad_ids: list[int], budget: int,
                  horizon_end: int) -> tuple[float | None, str, str]:
    """Wildcard = đổi đội VĨNH VIỄN, nên lợi tích luỹ qua nhiều vòng.

    So tổng xP của đội tối ưu với đội hiện tại trên một khoảng **cố định**
    `WILDCARD_WINDOW` vòng kể từ `gw`, giữ nguyên đội hình suốt khoảng đó ở cả hai
    phía. Khoảng cố định là điều kiện để các vòng so được với nhau — xem chú thích
    ở `WILDCARD_WINDOW`. Xấp xỉ này có lợi cho wildcard (thực tế còn chuyển nhượng
    tiếp), nên nó là giới hạn TRÊN chứ không phải kỳ vọng.
    """
    end = gw + WILDCARD_WINDOW - 1
    if end > horizon_end:
        return None, (
            f"Cần {WILDCARD_WINDOW} vòng dự báo liên tiếp từ GW{gw} (tới GW{end}) "
            f"để so công bằng với các vòng khác, nhưng dự báo chỉ tới GW{horizon_end}."
        ), "no_projection"
    gws = list(range(gw, end + 1))

    from app.services.common import horizon_xp
    from app.services.team import build_opt_players

    hx = horizon_xp(db, gws)
    players = {p.id: p for p in db.scalars(select(Player)).all()}

    cur = [pid for pid in squad_ids if pid in players]
    if len(cur) < 15:
        return None, "Cần đúng 15 cầu thủ trong đội."
    # xấp xỉ: 11/15 số người ghi điểm mỗi vòng, áp cho cả hai phía như nhau
    cur_total = sum(hx.get(pid, 0.0) for pid in cur) * (11 / 15)

    pool = [
        OptPlayer(
            id=pid, element_type=p.element_type, price=p.now_cost, club=p.team_id,
            value=round(hx.get(pid, 0.0), 3), cap_value=round(hx.get(pid, 0.0), 3),
        )
        for pid, p in players.items()
    ]
    best = optimize_squad(pool, budget=budget, bench_weight=0.2)
    idx = {p.id: p.value for p in pool}
    best_total = sum(idx.get(pid, 0.0) for pid in best.squad) * (11 / 15)
    n = len(gws)
    return best_total - cur_total, (
        f"Tổng xP {n} vòng (GW{gw}–{end}) của đội tối ưu trừ đội hiện tại. "
        f"Giới hạn TRÊN: cả hai phía đều giả định giữ nguyên đội suốt {n} vòng."
    ), "ok"


# --------------------------------------------------------------- lịch chip -----
def _expiry_risk(gws_left: int, best_gain_gw: int | None, current_gw: int) -> dict:
    """Rủi ro chip hết hạn mà chưa dùng — quy tắc do ta đặt, nêu rõ ngưỡng."""
    if gws_left <= 0:
        level = "Đã hết hạn"
    elif gws_left <= EXPIRY_THRESHOLDS["high"]:
        level = "Cao"
    elif gws_left <= EXPIRY_THRESHOLDS["medium"]:
        level = "Trung bình"
    else:
        level = "Thấp"
    return {
        "level": level,
        "gameweeks_left": max(0, gws_left),
        "rule": (
            f"<= {EXPIRY_THRESHOLDS['high']} vòng: Cao; "
            f"<= {EXPIRY_THRESHOLDS['medium']} vòng: Trung bình; còn lại Thấp. "
            "Ngưỡng do FPL Edge đặt, không phải luật FPL."
        ),
    }


def chip_calendar(
    db: Session,
    squad_ids: list[int] | None = None,
    bank: int = 0,
    free_transfers: int = 1,
    chips_used: list[str] | None = None,
    budget: int | None = None,
) -> dict:
    """Bảng chip thống nhất: mọi chip còn dùng được, trên mọi vòng có thể dùng."""
    squad_ids = squad_ids or []
    chips_used = [c.lower() for c in (chips_used or [])]
    teams = team_lookup(db)
    current_gw = planning_start_gw(db)
    windows = chip_windows(db)
    horizon = projection_horizon(db)

    # Ngân sách Free Hit / Wildcard = giá trị đội hiện tại + tiền trong bank.
    if budget is None:
        players = {p.id: p for p in db.scalars(select(Player)).all()}
        squad_value = sum(players[pid].now_cost for pid in squad_ids if pid in players)
        budget = (squad_value + bank) if squad_value else 1000

    has_squad = len(squad_ids) == 15
    hi = horizon[1] if horizon else None

    chips_out: list[dict] = []
    for w in windows:
        chip = w["chip"]
        used = chip in chips_used or f"{chip}_{w['set_index'] + 1}" in chips_used
        # chỉ xét các vòng còn ở phía trước, trong cửa sổ của chip
        gw_from = max(w["start_event"], current_gw)
        gw_to = w["stop_event"]
        options: list[ChipOption] = []

        for gw in range(gw_from, gw_to + 1):
            opt = ChipOption(
                chip=chip, label=w["label"], gameweek=gw,
                blank_double=fixture_outlook(db, gw, teams),
            )
            if used:
                opt.status = "used"
                opt.detail = "Chip này đã dùng."
            elif hi is None or gw > hi:
                opt.status = "no_projection"
                opt.detail = (
                    f"Chưa có dự báo cho GW{gw}. Dự báo hiện tới GW{hi}."
                    if hi else "Chưa chạy dự báo."
                )
            elif not has_squad:
                opt.status = "needs_squad"
                opt.detail = "Cần đội hình 15 người để tính."
            else:
                if chip == "bboost":
                    gain, detail, status = bench_boost_gain(db, gw, squad_ids)
                elif chip == "3xc":
                    gain, detail, status = triple_captain_gain(db, gw, squad_ids)
                elif chip == "freehit":
                    gain, detail, status = free_hit_gain(db, gw, squad_ids, budget)
                else:
                    gain, detail, status = wildcard_gain(db, gw, squad_ids, budget, hi)
                opt.gain, opt.detail, opt.status = gain, detail, status
            options.append(opt)

        scored = [o for o in options if o.gain is not None]
        best = max(scored, key=lambda o: o.gain) if scored else None
        now = next((o for o in options if o.gameweek == current_gw), None)
        gws_left = w["stop_event"] - current_gw + 1

        # giá trị của việc GIỮ chip: vòng tốt nhất còn lại hơn vòng này bao nhiêu.
        # Các lý do dồn vào một danh sách chứ không ghi đè nhau — mỗi cái nói một
        # điều khác về vì sao con số này chưa đầy đủ.
        hold_value = None
        notes: list[str] = []
        if used:
            pass       # chip đã tiêu: mọi ghi chú về dự báo đều vô nghĩa
        elif best and now and now.gain is not None:
            hold_value = best.gain - now.gain
            if abs(hold_value) < MIN_MEANINGFUL_MARGIN:
                notes.append(
                    f"Chênh lệch {round(hold_value, 1)} điểm nằm dưới ngưỡng phân "
                    f"biệt được ({MIN_MEANINGFUL_MARGIN} điểm) — coi như ngang nhau."
                )
        elif best is None:
            notes.append(_blocked_reason(options, has_squad, hi))
        else:
            notes.append(
                f"Vòng hiện tại (GW{current_gw}) ngoài cửa sổ chip này "
                f"(mở từ GW{w['start_event']})."
            )
        if not used and best and hi is not None and w["stop_event"] > hi:
            notes.append(
                f"Chỉ so trong tầm dự báo (tới GW{hi}); cửa sổ chip còn tới "
                f"GW{w['stop_event']} nên vòng tốt nhất thật sự có thể chưa xuất hiện."
            )
        hold_note = " ".join(notes)

        chips_out.append({
            "chip": chip,
            "label": w["label"],
            "set_index": w["set_index"],
            "set_label": "Nửa đầu mùa" if w["set_index"] == 0 else "Nửa sau mùa",
            "window": {"start": w["start_event"], "stop": w["stop_event"]},
            "used": used,
            "best": best.as_dict() if best else None,
            "this_gw": now.as_dict() if now else None,
            "hold_value": None if hold_value is None else round(hold_value, 1),
            "hold_note": hold_note,
            "expiry_risk": _expiry_risk(gws_left, best.gameweek if best else None, current_gw),
            "options": [o.as_dict() for o in options],
            "recommendation": _recommend(
                best, now, gws_left, used, hi, w["stop_event"],
                options=options, has_squad=has_squad,
            ),
        })

    return {
        "current_gameweek": current_gw,
        "projection_range": (
            {"from": horizon[0], "to": horizon[1]} if horizon else None
        ),
        "squad": {
            "provided": has_squad,
            "size": len(squad_ids),
            "bank": round(bank / 10.0, 1),
            "budget": round(budget / 10.0, 1),
            "free_transfers": free_transfers,
            "note": (
                "Wildcard và Free Hit KHÔNG tiêu free transfer đang bank — số FT "
                "được giữ nguyên qua vòng dùng chip (tối đa 5)."
            ),
        },
        "chips": chips_out,
        "conflicts": _conflicts(chips_out),
        "limits": _limits(hi, chips_out),
    }


def _blocked_reason(options: list[ChipOption], has_squad: bool,
                    hi: int | None) -> str:
    """Vì sao không tính được — phải nói ĐÚNG nguyên nhân, không nói bừa.

    Bản đầu luôn trả về "không có dự báo" ngay cả khi dự báo có đủ mà chỉ thiếu đội
    hình. Người đọc sẽ đi chạy lại dự báo trong khi việc cần làm là nhập Team ID.
    """
    if not has_squad:
        return (
            "Chưa tính được: cần đội hình 15 người. Nhập FPL Team ID để lấy đội "
            "hiện tại, hoặc xem cột cửa sổ chip và blank/double mà không cần đội."
        )
    if hi is None:
        return "Chưa tính được: chưa chạy dự báo."
    statuses = {o.status for o in options}
    if statuses == {"no_projection"}:
        return f"Chưa tính được: không vòng nào trong cửa sổ có dự báo (dự báo tới GW{hi})."
    return f"Chưa tính được: dự báo chỉ tới GW{hi} nên cửa sổ này chưa đủ dữ liệu."


def _recommend(best, now, gws_left: int, used: bool, hi: int | None,
               stop: int, options: list[ChipOption] | None = None,
               has_squad: bool = True) -> dict:
    """Khuyến nghị dùng/giữ, kèm lý do và điều kiện khiến nó đổi."""
    if used:
        return {"action": "Đã dùng", "reason": "Chip này không còn khả dụng."}
    if best is None:
        return {
            "action": "Chưa kết luận",
            "reason": _blocked_reason(options or [], has_squad, hi),
        }
    if now is None or now.gain is None:
        return {
            "action": "Giữ chip",
            "reason": f"Vòng tốt nhất trong tầm dự báo là GW{best.gameweek} (+{round(best.gain, 1)}).",
        }
    margin = best.gain - now.gain
    if best.gameweek == now.gameweek:
        return {
            "action": "Dùng vòng này",
            "reason": f"GW{now.gameweek} đang là vòng tốt nhất trong tầm dự báo (+{round(now.gain, 1)}).",
        }
    if margin < MIN_MEANINGFUL_MARGIN:
        return {
            "action": "Ngang nhau",
            "reason": (
                f"GW{best.gameweek} chỉ hơn vòng này {round(margin, 1)} điểm, dưới "
                f"ngưỡng {MIN_MEANINGFUL_MARGIN} điểm mà mô hình phân biệt được — "
                f"chờ thêm dữ liệu chứ không phải chờ vì GW{best.gameweek} tốt hơn."
            ),
        }
    if gws_left <= EXPIRY_THRESHOLDS["high"] and margin < 2.0:
        return {
            "action": "Dùng sớm",
            "reason": (
                f"Chỉ còn {gws_left} vòng trong cửa sổ và GW{best.gameweek} chỉ hơn "
                f"{round(margin, 1)} điểm — không đủ bù rủi ro hết hạn."
            ),
        }
    return {
        "action": "Giữ chip",
        "reason": (
            f"GW{best.gameweek} hơn vòng này {round(margin, 1)} điểm "
            f"(+{round(best.gain, 1)} so với +{round(now.gain, 1)})."
        )
        + (
            f" Lưu ý cửa sổ còn tới GW{stop} nhưng dự báo chỉ tới GW{hi}."
            if hi is not None and stop > hi else ""
        ),
    }


def _conflicts(chips_out: list[dict]) -> list[dict]:
    """Vòng nào có hai chip cùng đạt đỉnh — FPL chỉ cho dùng MỘT chip mỗi vòng."""
    by_gw: dict[int, list[dict]] = {}
    for c in chips_out:
        if c["used"] or not c["best"]:
            continue
        by_gw.setdefault(c["best"]["gameweek"], []).append(c)
    out = []
    for gw, group in sorted(by_gw.items()):
        if len(group) < 2:
            continue
        ranked = sorted(group, key=lambda c: -(c["best"]["gain"] or 0))
        out.append({
            "gameweek": gw,
            "chips": [c["label"] for c in ranked],
            "keep": ranked[0]["label"],
            "message": (
                f"GW{gw} là vòng tốt nhất của {len(group)} chip cùng lúc, nhưng FPL "
                f"chỉ cho dùng một chip mỗi vòng. Lợi nhất là {ranked[0]['label']} "
                f"(+{ranked[0]['best']['gain']}); các chip còn lại phải đổi vòng."
            ),
        })
    return out


def _limits(hi: int | None, chips_out: list[dict]) -> list[str]:
    """Những điều bảng này KHÔNG trả lời được — hiển thị cùng bảng, không ẩn."""
    out: list[str] = []
    if not chips_out:
        out.append(
            "Chưa đọc được cửa sổ chip: bảng `seasons` chưa có `chips_json` (chưa "
            "đồng bộ được từ FPL). Bảng để trống thay vì đoán khung chip, vì khung "
            "chip mỗi mùa mỗi khác — đồng bộ lại rồi mở trang này."
        )
        return out
    if hi is None:
        out.append("Chưa chạy dự báo nên không có vòng nào tính được điểm.")
    else:
        beyond = sorted({
            c["window"]["stop"] for c in chips_out if c["window"]["stop"] > hi
        })
        if beyond:
            out.append(
                f"Dự báo chỉ tới GW{hi}, còn cửa sổ chip kéo tới GW{max(beyond)}. "
                f"Các vòng ngoài tầm để trống thay vì điền số nội suy — một con số "
                f"kiểu 'Free Hit GW18: +15.8' khi mới GW1 là số bịa, không phải dự báo."
            )
    out.append(
        "Không có xác suất Blank/Double: hai hiện tượng đó do lịch hoãn cúp sinh "
        "ra và lịch hoãn chỉ được công bố dần trong mùa. Lịch đang công bố có đúng "
        "10 trận mỗi vòng cho cả 38 vòng, nên mọi xác suất suy ra lúc này là số tự đặt."
    )
    out.append(
        "Gain của Wildcard là giới hạn TRÊN: nó giả định cả đội cũ và đội mới đứng "
        "yên suốt tầm dự báo, trong khi thực tế bạn vẫn chuyển nhượng tiếp."
    )
    return out

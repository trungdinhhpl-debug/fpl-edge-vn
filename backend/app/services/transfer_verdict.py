"""Khuyến nghị chuyển nhượng theo một cấu trúc cố định: ROLL hay TRANSFER.

Một danh sách "bán B mua A, +0.8 xP" không đủ để quyết định. Nó bỏ qua ba thứ mà
người chơi thật sự cân: lợi ích trải ra bao nhiêu vòng, việc tiêu một free transfer
có giá của nó, và cầu thủ mới có chắc ra sân không. Module này ép mọi khuyến nghị
vào cùng một khung để hai lựa chọn luôn so được với nhau.

Ba điều chỉnh, mỗi cái có căn cứ chứ không phải hệ số cho đẹp:

  * **Mất giá trị FT** — cái giá của việc tiêu quyền chuyển nhượng bây giờ. KHÔNG
    phải 4 điểm (đó là phí hit) và cũng KHÔNG phải lợi ích của nâng cấp tốt tiếp
    theo — xem `ft_value()` để biết vì sao cách thứ hai sai theo kế toán. Nó là giá
    trị quyền chọn của việc chờ thêm tin đội hình, và được khai báo rõ là **một giả
    định**, không phải số đo được. Bank tối đa 5 FT thì bằng 0.
  * **Rủi ro rotation** — trừ theo P(không ra sân) của người mua vào so với người
    bán đi. Mua một người xP cao nhưng chỉ 60% chắc đá chính không giống mua người
    chắc suất.
  * **Lợi ích ròng** — tổng của những phần trên. Đây là con số ra kết luận.

`Confidence` KHÔNG phải một con số trang trí: nó là trung bình độ tin cậy dự báo
của hai cầu thủ liên quan, hạ xuống khi dữ liệu cũ hoặc khi biên lợi ích nhỏ hơn
sai số của mô hình.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ExpectedMinutes, Gameweek, Player, PlayerProjection
from app.optimizer.constraints import MAX_BANKED_FT
from app.services.common import planning_start_gw, team_lookup

# Biên lợi ích ròng dưới mức này thì coi như không phân biệt được: sai số của mô
# hình xP lớn hơn nhiều (xem trang Model Performance — MAE đo được ~1 điểm/vòng).
MIN_MEANINGFUL_NET = 1.0

# Các mốc horizon hiển thị.
HORIZONS = (1, 3, 5)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ft_value(free_transfers: int, next_best_gain: float | None = None) -> dict:
    """Cái giá của việc TIÊU một free transfer bây giờ.

    Hai định nghĩa sai đã thử và loại, vì cả hai đều dẫn tới kết luận sai một cách
    hệ thống:

      * **Lấy 4 điểm.** 4 là phí *hit* khi chuyển nhượng vượt số FT — chuyện hoàn
        toàn khác với giá trị của một quyền chưa dùng.
      * **Lấy lợi ích của nâng cấp tốt tiếp theo.** Sai theo kế toán. Xét cửa sổ 3
        vòng: *chuyển ngay* thì vòng 1 làm nước A, vòng 2 (FT mới) làm B, vòng 3
        làm C. *Giữ lại* thì vòng 2 có 2 FT nên làm A và B, vòng 3 làm C. Hai đường
        làm đúng những nước như nhau — chỉ khác là giữ lại **làm A muộn một vòng**.
        Nên giữ FT không mua thêm cho bạn nước nào; nó chỉ trì hoãn. Bản đầu tính
        theo cách này ra 5.30 điểm, tức phóng đại gấp nhiều lần.

    Vậy cái giá thật là **giá trị quyền chọn của việc chờ**: chờ thêm thì tin đội
    hình, họp báo và chấn thương sẽ rõ hơn, và nước tốt nhất có thể đổi. Đó không
    phải một lượng xP suy ra được từ dự báo — nên ở đây nó là **một giả định khai
    báo rõ**, không phải một con số đo được.

    Khi nào đo được: bảng `projection_snapshots` đã đóng băng dự báo trước mỗi
    deadline. Sau vài chục vòng, đếm được bao nhiêu phần trăm số lần nước tốt nhất
    THAY ĐỔI giữa lần chụp đầu và deadline — nhân với lợi ích trung bình bị mất là
    ra giá trị quyền chọn thật, thay cho giả định này.
    """
    if free_transfers >= MAX_BANKED_FT:
        return {
            "value": 0.0,
            "is_assumption": False,
            "basis": (
                f"Đã bank tối đa {MAX_BANKED_FT} free transfer — giữ thêm không giữ "
                f"được nữa, nên tiêu một cái không mất gì."
            ),
        }
    v = settings.ft_option_value
    basis = (
        f"Giá trị quyền chọn của việc chờ thêm tin đội hình: {v:.2f} điểm "
        f"(FT_OPTION_VALUE). Theo kế toán FT thuần xP, giữ lại KHÔNG mua thêm nước "
        f"nào — nó chỉ trì hoãn nước tốt nhất một vòng — nên đây là giá của thông "
        f"tin, không phải của một nước đi."
    )
    if next_best_gain is not None and next_best_gain > 0:
        basis += (
            f" (Tham khảo: nâng cấp tốt tiếp theo đáng {next_best_gain:.2f} điểm, "
            f"nhưng nước đó vẫn làm được bằng FT của vòng sau nên KHÔNG cộng vào đây.)"
        )
    return {"value": round(v, 2), "is_assumption": True, "basis": basis}


def rotation_adjustment(p_dnp_in: float, p_dnp_out: float, xp_in: float) -> dict:
    """Trừ điểm cho việc người mua vào kém chắc suất hơn người bán đi.

    Trừ theo phần CHÊNH LỆCH rủi ro không ra sân, nhân với xP của người mua: mua
    một người 8 điểm mà 30% khả năng không ra sân thì phần kỳ vọng mất đi lớn hơn
    hẳn so với mua một người 3 điểm cùng mức rủi ro.
    """
    delta = max(0.0, p_dnp_in - p_dnp_out)
    penalty = -delta * xp_in
    return {
        "value": round(penalty, 2),
        "basis": (
            f"P(không ra sân) của người mua {p_dnp_in:.0%} so với người bán "
            f"{p_dnp_out:.0%} → chênh {delta:.0%}, nhân xP {xp_in:.2f}"
        ) if delta > 0 else (
            f"Người mua chắc suất không kém người bán "
            f"({p_dnp_in:.0%} vs {p_dnp_out:.0%}) — không trừ."
        ),
    }


def _horizon_xp(db: Session, player_id: int, gws: list[int]) -> dict[int, float]:
    rows = db.scalars(
        select(PlayerProjection).where(
            PlayerProjection.player_id == player_id,
            PlayerProjection.gameweek.in_(gws),
        )
    ).all()
    return {r.gameweek: r.xp for r in rows}


def _p_dnp(db: Session, player_id: int, gw: int) -> float:
    xm = db.scalar(
        select(ExpectedMinutes).where(
            ExpectedMinutes.player_id == player_id, ExpectedMinutes.gameweek == gw
        )
    )
    return float(xm.p_no_play) if xm else 0.0


def _news_flags(db: Session, gw: int, player_ids: list[int]) -> list[dict]:
    """Những tin có thể làm đổi quyết định — và nói rõ cái nào ta KHÔNG biết.

    Ba loại người chơi hay chờ, với mức độ dữ liệu rất khác nhau:

      * biến động giá — suy được từ chuyển nhượng ròng (chỉ báo, không phải dự báo);
      * họp báo HLV — FPL API **không** công bố lịch họp báo. Ta chỉ nói được còn
        bao lâu tới deadline, tức còn bao lâu tin đội hình có thể xuất hiện;
      * trận cúp giữa tuần — FPL API **chỉ có lịch Ngoại hạng**, không có cúp quốc
        nội hay châu Âu. Không suy ra được, và nói thẳng là không biết còn tốt hơn
        gợi ý rằng đã kiểm.
    """
    from app.services.player_risk import price_risk

    out: list[dict] = []
    gw_row = db.get(Gameweek, gw)
    deadline = _aware(gw_row.deadline_time) if gw_row else None
    hours_left = None
    if deadline:
        hours_left = (deadline - _now()).total_seconds() / 3600.0

    players = {
        p.id: p for p in db.scalars(select(Player).where(Player.id.in_(player_ids))).all()
    }
    movers = []
    for pid, p in players.items():
        pr = price_risk(p.transfers_in_event, p.transfers_out_event,
                        p.selected_by_percent)
        if pr["level"] != "Thấp":
            movers.append(f"{p.web_name} ({pr['direction']}, {pr['net_transfers']:+,})")
    out.append({
        "kind": "Biến động giá",
        "known": bool(movers),
        "detail": (
            "; ".join(movers) if movers
            else "Chưa cầu thủ nào trong hai người này có động lượng chuyển nhượng đáng kể."
        ),
        "caveat": "Ngưỡng đổi giá của FPL không công khai — chỉ báo, không phải dự báo.",
    })
    out.append({
        "kind": "Họp báo HLV / tin đội hình",
        "known": False,
        "detail": (
            f"Còn {hours_left:.0f} giờ tới deadline — tin đội hình còn có thể xuất hiện."
            if hours_left is not None and hours_left > 0
            else "Không xác định được thời điểm deadline."
        ),
        "caveat": (
            "FPL API không công bố lịch họp báo. Hệ thống KHÔNG theo dõi họp báo; "
            "đây chỉ là thời gian còn lại, không phải một tin đã biết."
        ),
    })
    out.append({
        "kind": "Trận cúp giữa tuần",
        "known": False,
        "detail": "Không có dữ liệu.",
        "caveat": (
            "FPL API chỉ công bố lịch Ngoại hạng, không có cúp quốc nội hay châu Âu. "
            "Hệ thống không biết đội nào đá giữa tuần, nên không đưa vào tính toán."
        ),
    })
    return out


def upgrade_opportunities(db: Session, squad_ids: list[int], bank: int,
                          gws: list[int]) -> list[tuple[float, int, int]]:
    """Với mỗi cầu thủ trong đội, nâng cấp cùng vị trí tốt nhất mà tiền mua nổi.

    Trả về [(lợi ích xP trên `gws`, id bán, id mua)] đã xếp giảm dần. Đây là cơ sở
    để định giá một free transfer: mức cơ hội chung của đội, chứ không phải một
    phương án đơn lẻ.
    """
    from app.services.common import horizon_xp

    players = {p.id: p for p in db.scalars(select(Player)).all()}
    hx = horizon_xp(db, gws)
    squad = [players[pid] for pid in squad_ids if pid in players]
    club_count: dict[int, int] = {}
    for p in squad:
        club_count[p.team_id] = club_count.get(p.team_id, 0) + 1

    out: list[tuple[float, int, int]] = []
    in_squad = set(squad_ids)
    for p_out in squad:
        budget = p_out.now_cost + bank
        best_gain, best_in = 0.0, None
        for cand in players.values():
            if cand.id in in_squad or cand.element_type != p_out.element_type:
                continue
            if cand.now_cost > budget:
                continue
            # giới hạn 3 người mỗi CLB: bán một người cùng CLB thì mở thêm một suất
            same_club = club_count.get(cand.team_id, 0) - (
                1 if cand.team_id == p_out.team_id else 0
            )
            if same_club >= 3:
                continue
            gain = hx.get(cand.id, 0.0) - hx.get(p_out.id, 0.0)
            if gain > best_gain:
                best_gain, best_in = gain, cand.id
        if best_in is not None:
            out.append((round(best_gain, 3), p_out.id, best_in))
    out.sort(reverse=True)
    return out


def transfer_verdict(db: Session, squad_ids: list[int], bank: int = 0,
                     free_transfers: int = 1) -> dict:
    """Khuyến nghị ROLL / TRANSFER theo cấu trúc cố định."""
    gw = planning_start_gw(db)
    gws = list(range(gw, gw + max(HORIZONS)))
    teams = team_lookup(db)

    # Mọi nâng cấp khả thi, xếp theo lợi ích 5 vòng. Phương án tốt nhất là khuyến
    # nghị; phần còn lại dùng để định giá free transfer.
    opportunities = upgrade_opportunities(db, squad_ids, bank, gws[:3])
    if not opportunities:
        return {
            "gameweek": gw,
            "recommendation": "ROLL TRANSFER",
            "best_move": None,
            "conclusion": (
                "Không tìm được nâng cấp cùng vị trí nào có lợi trong tầm tiền — "
                "giữ nguyên đội và cất free transfer."
            ),
            "news_watch": _news_flags(db, gw, squad_ids[:2]),
            "free_transfers": free_transfers,
        }

    _, out_id, in_id = opportunities[0]
    p_out, p_in = db.get(Player, out_id), db.get(Player, in_id)

    xp_out = _horizon_xp(db, out_id, gws)
    xp_in = _horizon_xp(db, in_id, gws)
    deltas = {}
    for h in HORIZONS:
        window = gws[:h]
        deltas[h] = round(
            sum(xp_in.get(g, 0.0) for g in window)
            - sum(xp_out.get(g, 0.0) for g in window),
            2,
        )

    # điều chỉnh — giá trị FT tính từ các nâng cấp khác, KHÔNG tính phương án này
    next_best = opportunities[1][0] if len(opportunities) > 1 else None
    ft = ft_value(free_transfers, next_best)
    rot = rotation_adjustment(
        _p_dnp(db, in_id, gw), _p_dnp(db, out_id, gw), xp_in.get(gw, 0.0)
    )
    # Lợi ích gộp lấy ở mốc 3 vòng: 1 vòng quá nhiễu, 5 vòng thì giả định đội hình
    # đứng yên quá lâu (thực tế bạn còn chuyển nhượng tiếp).
    gross = deltas[3]
    net = round(gross - ft["value"] + rot["value"], 2)

    conf = _confidence(db, [out_id, in_id], gw, net)
    if net >= MIN_MEANINGFUL_NET:
        rec = "TRANSFER"
        conclusion = (
            f"Lợi ích ròng +{net} điểm vượt ngưỡng phân biệt được "
            f"({MIN_MEANINGFUL_NET} điểm) — nên thực hiện."
        )
    elif net <= -MIN_MEANINGFUL_NET:
        rec = "ROLL TRANSFER"
        conclusion = "Không đủ lợi thế để transfer."
    else:
        rec = "ROLL TRANSFER"
        conclusion = (
            f"Lợi ích ròng {net:+} điểm nằm trong sai số của mô hình "
            f"(±{MIN_MEANINGFUL_NET} điểm) — không đủ căn cứ để tiêu một free "
            f"transfer. Giữ lại là lựa chọn ít hối tiếc hơn."
        )

    return {
        "gameweek": gw,
        "recommendation": rec,
        "best_move": {
            "out": {
                "id": out_id, "name": p_out.web_name if p_out else "?",
                "team": teams[p_out.team_id].short_name if p_out and p_out.team_id in teams else "",
                "price": round(p_out.now_cost / 10.0, 1) if p_out else None,
            },
            "in": {
                "id": in_id, "name": p_in.web_name if p_in else "?",
                "team": teams[p_in.team_id].short_name if p_in and p_in.team_id in teams else "",
                "price": round(p_in.now_cost / 10.0, 1) if p_in else None,
            },
        },
        "xp_delta": {f"{h}gw": deltas[h] for h in HORIZONS},
        "adjustments": [
            {"label": "Mất giá trị FT", "value": -ft["value"], "basis": ft["basis"]},
            {"label": "Rủi ro rotation", "value": rot["value"], "basis": rot["basis"]},
        ],
        "gross_gain": gross,
        "net_gain": net,
        "net_basis": (
            f"Lấy chênh lệch 3 vòng ({gross:+}) trừ giá trị FT ({ft['value']:.2f}) "
            f"cộng điều chỉnh rotation ({rot['value']:+.2f}). Mốc 3 vòng vì 1 vòng "
            f"quá nhiễu, còn 5 vòng giả định đội hình đứng yên quá lâu."
        ),
        "conclusion": conclusion,
        "confidence": conf,
        "news_watch": _news_flags(db, gw, [out_id, in_id]),
        "free_transfers": free_transfers,
        "bank": round(bank / 10.0, 1),
    }


def _confidence(db: Session, player_ids: list[int], gw: int, net: float) -> dict:
    """Độ tin cậy của chính khuyến nghị này, không phải của mô hình nói chung."""
    from app.services.player_risk import source_freshness

    rows = db.scalars(
        select(PlayerProjection).where(
            PlayerProjection.player_id.in_(player_ids),
            PlayerProjection.gameweek == gw,
        )
    ).all()
    base = (
        sum(r.confidence for r in rows) / len(rows) if rows else 0.5
    )
    reasons = [f"Độ tin cậy dự báo trung bình của hai cầu thủ: {base:.0%}"]

    # dữ liệu cũ thì hạ
    stale = False
    for pid in player_ids:
        p = db.get(Player, pid)
        if p and source_freshness(db, p)["stale"]:
            stale = True
    if stale:
        base *= 0.85
        reasons.append("dữ liệu cũ hơn 12 giờ → hạ 15%")

    # biên nhỏ hơn sai số mô hình thì hạ mạnh: kết luận lúc đó gần như là tung xu
    if abs(net) < MIN_MEANINGFUL_NET:
        base *= 0.8
        reasons.append(
            f"biên lợi ích |{net}| nhỏ hơn sai số mô hình ({MIN_MEANINGFUL_NET}) → hạ 20%"
        )

    return {
        "value": round(max(0.0, min(1.0, base)), 3),
        "basis": "; ".join(reasons),
    }

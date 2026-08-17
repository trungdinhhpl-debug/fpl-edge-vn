"""Đám đông: mỗi cầu thủ đang được bao nhiêu phần trăm đối thủ "dùng".

Optimizer của site tối đa hoá xP tuyệt đối, tức trả lời câu "đội nào ĐƯỢC NHIỀU
ĐIỂM NHẤT". Nhưng FPL trả thưởng theo thứ hạng, và thứ hạng là điểm của bạn TRỪ
đi điểm của những người bạn đang đua. Module này cấp vế bị thiếu đó.

Một điều phải nói trước, vì nó là lý do module này được thiết kế như bên dưới
chứ không phải như trực giác ban đầu:

    Ở KỲ VỌNG THUẦN, tối ưu theo thứ hạng và tối ưu theo điểm là MỘT.

Chứng minh gọn: lợi thế của bạn so với đám đông cộng trên toàn bộ cầu thủ là

    Σ xP(p)·hệ_số_của_bạn(p)  −  Σ xP(p)·EO(p)/100

Số trừ chạy trên MỌI cầu thủ trong giải và không phụ thuộc gì vào lựa chọn của
bạn — nó là một hằng số. Trừ một hằng số không đổi thứ tự lời giải, nên một MILP
tuyến tính không thể vừa "đúng kỳ vọng" vừa cho ra đội hình khác đi.

Vậy EO thật sự đổi cái gì? Đổi PHƯƠNG SAI của thứ hạng. Trùng đội hình với đám
đông thì tuần nào bạn cũng đi cùng họ — không tụt, mà cũng không vượt. Lệch khỏi
đám đông thì mỗi tuần là một canh bạc hai chiều. Đó là một sở thích của người
chơi (đang dẫn thì giữ, đang đuổi thì phá), không phải một dự báo.

Nên `eo_weight` ở đây được khai đúng bản chất: MỘT NÚM CHỈNH KHẨU VỊ, không phải
một phép tính kỳ vọng. Mặc định 0 — không ai bị đổi hành vi nếu không tự bật.

Về nguồn số: nếu người dùng đưa mã mini-league thì EO là số ĐẾM ĐƯỢC từ đội hình
thật của đối thủ (xem `services/league.py`); nếu không thì rơi về EO toàn cầu,
trong đó sở hữu là số thật của FPL còn phần băng đội trưởng là mô hình. Hai loại
số này không được trộn im lặng — payload luôn kèm nguồn.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, PlayerProjection
from app.services.captains import MIN_START_PROB, project_effective_ownership

# Trần của núm chỉnh. |w| = 1 nghĩa là một cầu thủ EO 100% bị coi như vô giá trị,
# đủ cực đoan để không ai nên vượt qua.
MAX_EO_WEIGHT = 1.0


def _modelled_eo(db: Session, gw: int) -> dict[int, float]:
    """EO toàn cầu: sở hữu thật của FPL + tỷ lệ bắt băng do mô hình ước lượng.

    Dùng lại đúng mô hình của trang Đội trưởng để hai trang không nói hai con số
    khác nhau về cùng một cầu thủ. Người có xP = 0 hoặc gần như chắc không đá
    chính thì chỉ tính phần sở hữu: không ai bắt băng một người không ra sân, và
    ném họ vào phép chuẩn hoá sẽ làm loãng tỷ lệ của những người còn lại.
    """
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    projs = {
        r.player_id: r for r in db.scalars(
            select(PlayerProjection).where(PlayerProjection.gameweek == gw)
        ).all()
    }

    cands: list[dict] = []
    eo: dict[int, float] = {}
    for pid, p in players.items():
        own = p.selected_by_percent or 0.0
        eo[pid] = own                      # mặc định: chỉ có phần sở hữu
        pr = projs.get(pid)
        if pr and pr.xp > 0 and pr.p_start >= MIN_START_PROB:
            cands.append({"id": pid, "captain_xp": pr.xp * 2, "selected_by_percent": own})

    project_effective_ownership(cands)
    for c in cands:
        eo[c["id"]] = c["projected_eo"]
    return eo


def _measured_eo(db: Session, league_id: int, top_n: int = 30) -> tuple[dict[int, float], dict] | None:
    """EO đếm từ đội hình thật trong mini-league. None nếu chưa đọc được."""
    from app.services.league import _fetch_rival_picks, _latest_public_gameweek

    gw_public = _latest_public_gameweek(db)
    if gw_public is None:
        return None
    data = _fetch_rival_picks(league_id, gw_public, top_n)
    rivals = data.get("rivals") or []
    if not rivals:
        return None

    n = len(rivals)
    total: dict[int, float] = {}
    for r in rivals:
        for pick in r["picks"]:
            total[pick["element"]] = total.get(pick["element"], 0.0) + float(pick["multiplier"])
    eo = {pid: 100.0 * s / n for pid, s in total.items()}
    return eo, {
        "kind": "measured",
        "league_id": league_id,
        "league_name": data.get("league_name"),
        "n_rivals": n,
        "measured_gameweek": gw_public,
        "label": (
            f"Đếm trên đội hình thật của {n} đối thủ trong “{data.get('league_name')}” "
            f"ở vòng {gw_public} — họ đã chuyển nhượng sau mốc đó."
        ),
    }


def field_eo(db: Session, gw: int, league_id: int | None = None,
             top_n: int = 30) -> tuple[dict[int, float], dict]:
    """(EO theo cầu thủ, mô tả nguồn). Ưu tiên số đếm được, rơi về số mô hình."""
    if league_id:
        measured = _measured_eo(db, league_id, top_n)
        if measured:
            return measured
    return _modelled_eo(db, gw), {
        "kind": "modelled",
        "league_id": league_id,
        "label": (
            "EO toàn cầu: tỷ lệ sở hữu là số thật của FPL, phần băng đội trưởng là "
            "mô hình (FPL không công khai trước hạn chót)."
            + (" Không đọc được mini-league nên đã rơi về nguồn này."
               if league_id else "")
        ),
    }


def tilt(value: float, xp: float, eo: float, weight: float) -> float:
    """Nghiêng giá trị của một cầu thủ theo mức đám đông đã nắm giữ anh ta.

        giá_trị_mới = giá_trị − w · (EO/100) · xP

    w > 0 đẩy ra khỏi đám đông (đuổi hạng), w < 0 kéo về phía đám đông (giữ thứ
    hạng), w = 0 không đổi gì. Phần trừ tỷ lệ với chính xP nên nó không bẻ gãy
    thang đo: một người 8 điểm mà cả giải đều có bị trừ nhiều hơn một người 2
    điểm cả giải đều có, đúng như trực giác về mức "đám đông đã bỏ túi sẵn".

    Không kẹp sàn về 0: một giá trị âm là câu trả lời hợp lệ ("người này chỉ kéo
    bạn lại gần đám đông"), và kẹp lại sẽ làm mọi cầu thủ EO cao trông giống hệt
    nhau ở đáy bảng.
    """
    if not weight or not eo:
        return value
    return value - weight * (eo / 100.0) * xp

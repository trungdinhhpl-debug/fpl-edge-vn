"""Hai chỗ tiền mùa nói sai vì dữ liệu chưa có, không phải vì tính toán sai.

Cùng một kiểu lỗi: khi FPL chưa phát dữ liệu, trang vẫn dựng ra một câu trả lời
trông hoàn chỉnh. Bảng xếp hạng lượt mua sinh ra từ một cột toàn số 0, và nhãn
"mùa này" dán lên tổng của mùa trước. Cả hai đều không sai một phép tính nào —
chúng chỉ khẳng định thứ mình không biết.
"""
from sqlalchemy import select

from app.models import Fixture, Player
from app.services.gameweek import dashboard
from app.services.players import player_detail


def test_transfer_board_disappears_when_nobody_has_transferred(db):
    """Cột toàn 0 thì thứ tự rơi về id — bảng "được mua nhiều nhất" thành hư cấu."""
    players = db.scalars(select(Player)).all()
    saved = {p.id: p.transfers_in_event for p in players}
    try:
        for p in players:
            p.transfers_in_event = 0
        db.flush()

        d = dashboard(db)
        assert d["top_transfers_in"] == []
        assert d["top_transfers_note"], "phải nói vì sao trống, không im lặng"
        assert "0" in d["top_transfers_note"]
    finally:
        for p in players:
            p.transfers_in_event = saved[p.id]
        db.flush()


def test_transfer_board_comes_back_the_moment_there_is_data(db):
    players = db.scalars(select(Player)).all()
    saved = {p.id: p.transfers_in_event for p in players}
    try:
        for p in players:
            p.transfers_in_event = 0
        target = players[-1]                      # id lớn nhất: nếu xếp theo id
        target.transfers_in_event = 90_000        # thì người này KHÔNG lên đầu
        db.flush()

        d = dashboard(db)
        assert d["top_transfers_note"] is None
        assert d["top_transfers_in"][0]["id"] == target.id
    finally:
        for p in players:
            p.transfers_in_event = saved[p.id]
        db.flush()


def test_underlying_totals_are_labelled_with_the_season_they_belong_to(db, monkeypatch):
    """Mốc là vòng đã KẾT THÚC, giống hệt cách engine chọn luật BPS để quy đổi."""
    from app import scoring

    monkeypatch.setattr(scoring, "SEASON", "2026/27")
    pid = db.scalar(select(Player.id))
    finished = db.scalars(select(Fixture).where(Fixture.finished.is_(True))).all()

    # Dữ liệu demo đang giữa mùa -> tổng thuộc về mùa hiện tại.
    season = player_detail(db, pid)["underlying_season"]
    assert season["is_current_season"] is True
    assert season["label"] == "mùa này (2026/27)"
    assert season["note"] is None

    # Chưa đá trận nào -> FPL vẫn đang phát tổng của mùa trước.
    try:
        for f in finished:
            f.finished = False
        db.flush()

        season = player_detail(db, pid)["underlying_season"]
        assert season["is_current_season"] is False
        assert season["label"] == "mùa trước (2025/26)"
        assert season["note"], "phải giải thích, không chỉ đổi nhãn"
        assert season["season"] != season["current_season"]
    finally:
        for f in finished:
            f.finished = True
        db.flush()


def test_an_unknown_season_name_does_not_erase_which_season_it_is(db):
    """Không biết TÊN mùa vẫn phải nói được đây là mùa trước hay mùa này.

    Ở môi trường chưa nạp được luật từ FPL, `scoring.SEASON` là "—" nên không suy
    ra nổi tên mùa trước. Bản đầu trả về "chưa xác định được mùa" — đánh rơi luôn
    thông tin quan trọng hơn cái tên.
    """
    pid = db.scalar(select(Player.id))
    finished = db.scalars(select(Fixture).where(Fixture.finished.is_(True))).all()
    try:
        for f in finished:
            f.finished = False
        db.flush()

        season = player_detail(db, pid)["underlying_season"]
        assert season["label"] == "mùa trước"       # không có tên, vẫn đúng vế
        assert season["is_current_season"] is False
        assert season["note"]
    finally:
        for f in finished:
            f.finished = True
        db.flush()

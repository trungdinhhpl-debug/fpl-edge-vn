"""Cầu thủ đã rời CLB không được nằm trong bảng, trong tin, hay trong pool.

FPL giữ nguyên người đã chuyển đi trong bootstrap-static với đủ giá, tỷ lệ sở hữu
và một dòng news. Các bài dưới đây khoá lại ba chỗ mà việc đó gây hại: bảng cầu
thủ, News Centre, và pool của optimizer.
"""
import pytest
from sqlalchemy import select

from app.models import Player
from app.services.departures import has_left_club, player_departed
from app.services.news import news_feed
from app.services.players import list_players
from app.services.team import build_opt_players


# ------------------------------------------------------------- vị từ ---------
@pytest.mark.parametrize("news", [
    "Has joined Al Hilal permanently",
    "Has joined Borussia Dortmund on loan for the rest of the season",
    "Has joined Oxford United on loan until January",
    "has joined  Kasimpasa on loan for the rest of the seaon.",   # sic — FPL viết sai
    "has departed the club as a free agent.",
    "has returned to Getafe CF",
    "Has joined New England Revolution permanently",
])
def test_cac_cach_fpl_viet_mot_lan_roi_di(news):
    assert has_left_club("u", news) is True


def test_khong_duoc_dang_ky_thi_khong_phai_da_chuyen_di():
    """Richarlison mang status "u" với news "not included in squad."

    Anh ta vẫn là cầu thủ Tottenham và có thể được đăng ký lại vào tháng Một.
    Gộp trường hợp này vào "đã rời CLB" là giấu nhầm một cầu thủ vẫn còn ở giải.
    """
    assert has_left_club("u", "not included in squad.") is False


@pytest.mark.parametrize("status,news", [
    ("a", "Has joined Al Hilal permanently"),   # status mới là điều kiện cần
    ("i", "Knee injury - Expected back 20 Oct"),
    ("d", "Knock - 75% chance of playing"),
    ("s", "Suspended until 30 Sep"),
    ("u", None),                                 # "u" trần, không có tin: chưa đủ
    ("u", ""),
    (None, None),
])
def test_khong_duoc_lan_sang_nguoi_con_o_lai(status, news):
    assert has_left_club(status, news) is False


def test_hong_theo_huong_giu_lai_chu_khong_giau_nham():
    """Nếu FPL đổi chữ và "joined" mang nghĩa trở lại, ta phải giữ cầu thủ."""
    assert has_left_club("u", "Has rejoined the squad") is False
    assert has_left_club("u", "Has joined the club's training group") is False


# --------------------------------------------------------- bảng cầu thủ ------
def test_bang_cau_thu_an_nguoi_da_roi_nhung_noi_ra_la_minh_an(db):
    p = db.scalars(select(Player)).first()
    old = (p.status, p.news)
    p.status, p.news = "u", "Has joined Al Hilal permanently"
    db.flush()
    try:
        rows, n_hidden = list_players(db, limit=1000)
        assert n_hidden >= 1
        assert p.id not in {r["id"] for r in rows}

        shown, n_still = list_players(db, limit=1000, include_departed=True)
        assert p.id in {r["id"] for r in shown}
        # Con số không đổi khi mở ra: nó là "bao nhiêu người đã rời", không phải
        # "bao nhiêu dòng vừa bị cắt" — giao diện cần nó ở cả hai chiều của nút.
        assert n_still == n_hidden
        assert next(r for r in shown if r["id"] == p.id)["departed"] is True
    finally:
        p.status, p.news = old
        db.flush()


# ----------------------------------------------------------- News Centre -----
def test_tin_chuyen_nhuong_khong_phai_tin_chan_thuong(db):
    """Một dòng "Has joined X" không có hành động nào để khuyên: người đó đã đi."""
    p = db.scalars(select(Player)).first()
    old = (p.status, p.news)
    p.status, p.news = "u", "Has joined Fiorentina permanently"
    db.flush()
    try:
        assert p.id not in {i["player_id"] for i in news_feed(db, limit=500)}
    finally:
        p.status, p.news = old
        db.flush()


# --------------------------------------------------------- pool optimizer ----
def test_nguoi_da_roi_khong_lam_ghe_du_bi_gia_re(db):
    """xP = 0 nhưng vẫn còn GIÁ: với MILP đó là ghế dự bị £4.0 hoàn hảo.

    Đội hình đó không tồn tại ngoài đời — FPL không cho mua họ — nên họ không
    được có mặt trong pool để bắt đầu.
    """
    p = db.scalars(select(Player)).first()
    old = (p.status, p.news)
    p.status, p.news = "u", "Has joined Burnley permanently"
    db.flush()
    try:
        from app.services.common import planning_start_gw

        pool = build_opt_players(db, planning_start_gw(db))
        assert p.id not in {o.id for o in pool}
    finally:
        p.status, p.news = old
        db.flush()


def test_player_departed_doc_thang_tu_model(db):
    p = db.scalars(select(Player)).first()
    old = (p.status, p.news)
    p.status, p.news = "u", "Has joined Celta Vigo on loan for the rest of the season"
    db.flush()
    try:
        assert player_departed(p) is True
    finally:
        p.status, p.news = old
        db.flush()


def test_nguoi_da_roi_trong_doi_ban_duoc_goi_dung_ten(db):
    """Đội của chính bạn là chỗ duy nhất họ còn phải hiện — và phải hiện rõ.

    "Cảnh báo ra sân (u)" là câu trả lời sai cho một người đã sang giải khác:
    không có ngày trở lại để chờ, chỉ có một việc phải làm là bán.
    """
    from app.services.team import analyze_team

    p = db.scalars(select(Player)).first()
    old = (p.status, p.news)
    p.status, p.news = "u", "Has joined Al Hilal permanently"
    db.flush()
    try:
        out = analyze_team(db, [p.id])
        sell = next(s for s in out["sell_candidates"] if s["id"] == p.id)
        assert "Đã rời CLB" in sell["reason"]
        assert "Al Hilal" in sell["reason"]
    finally:
        p.status, p.news = old
        db.flush()

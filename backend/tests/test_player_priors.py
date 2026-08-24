"""Prior riêng từng cầu thủ, dựng từ mùa trước."""
from sqlalchemy import select

from app.engine.xmins import PRIOR_START_RATE, estimate_minutes
from app.models import Player, PlayerSeasonHistory
from app.services.player_priors import (
    MIN_MINUTES_FOR_ROLE, SEASON_MATCHES, blended, load_priors,
)


def _seed(db, player_id: int, season: str, **kw) -> PlayerSeasonHistory:
    row = PlayerSeasonHistory(player_id=player_id, season_name=season, **kw)
    db.add(row)
    db.flush()
    return row


def test_prior_comes_from_the_season_before_the_current_one(db):
    """Chỉ mùa liền trước mới được tính — mùa cũ hơn không được lẫn vào."""
    pid = db.scalars(select(Player.id)).first()
    try:
        _seed(db, pid, "2025/26", minutes=3000, starts=34, expected_goals=20.0)
        _seed(db, pid, "2024/25", minutes=200, starts=1, expected_goals=0.0)
        priors = load_priors(db, "2026/27")
        assert priors[pid].season == "2025/26"
        assert abs(priors[pid].start_rate - 34 / SEASON_MATCHES) < 1e-9
        assert abs(priors[pid].xg90 - 20.0 / (3000 / 90)) < 1e-9
    finally:
        db.rollback()


def test_a_thin_previous_season_yields_no_role_prior(db):
    """Ít phút quá thì không kết luận được vai trò — trả None, không bịa.

    Một người đá 200 phút có thể là cầu thủ trẻ mới lên, có thể là trụ cột chấn
    thương cả mùa. Hai chuyện đó không được gộp thành cùng một tỷ lệ đá chính.
    """
    pid = db.scalars(select(Player.id)).first()
    try:
        _seed(db, pid, "2025/26", minutes=MIN_MINUTES_FOR_ROLE - 1, starts=2)
        assert load_priors(db, "2026/27")[pid].start_rate is None
    finally:
        db.rollback()


def test_blended_falls_back_and_respects_reliability():
    """Đổi câu lạc bộ thì prior riêng phải nhạt dần về mức nền, không mất hẳn."""
    assert blended(None, 0.45, 1.0) == 0.45          # không có prior riêng
    assert blended(0.90, 0.45, 1.0) == 0.90          # vẫn CLB cũ: dùng nguyên
    mid = blended(0.90, 0.45, 0.4)                   # tân binh
    assert 0.45 < mid < 0.90


def test_per_player_prior_separates_a_regular_from_a_squad_player():
    """Hồi quy: một hằng số cho cả giải làm mọi người ra cùng một con số.

    Đo vòng 1 mùa 2026/27 khi chưa có nền lịch sử: Haaland, Isak, Szoboszlai và
    hàng loạt hậu vệ đều ra 65–66 phút — mô hình đang nói "tôi không phân biệt
    được", nhưng giao diện đọc như một dự báo.
    """
    common = dict(element_type=3, status="a", chance_of_playing=None,
                  season_starts=1, season_minutes=90, team_matches_played=1,
                  recent_minutes=[90])

    flat = estimate_minutes(**common)                              # prior toàn giải
    regular = estimate_minutes(**common, prior_start_rate=0.90)    # trụ cột mùa trước
    fringe = estimate_minutes(**common, prior_start_rate=0.20)     # dự bị mùa trước

    assert abs(flat.xmins - estimate_minutes(
        **common, prior_start_rate=PRIOR_START_RATE).xmins) < 1e-9
    assert regular.xmins > flat.xmins > fringe.xmins
    # và khoảng cách phải đáng kể, không phải làm cảnh
    assert regular.xmins - fringe.xmins > 10


def test_history_prior_beats_the_price_tier_fallback():
    """Xếp hạng theo GIÁ là bằng chứng yếu nhất — chỉ dùng khi không còn gì khác.

    Một người 0 phút mùa NÀY vẫn có thể có 2217 phút mùa trước (Gyökeres). Dùng
    giá thay cho con số đó là vứt bằng chứng tốt để giữ bằng chứng tồi.
    """
    common = dict(element_type=4, status="a", chance_of_playing=None,
                  season_starts=0, season_minutes=0, team_matches_played=1,
                  recent_minutes=[0], role_rank=1)

    by_price = estimate_minutes(**common)
    by_history = estimate_minutes(**common, prior_start_rate=0.68)
    assert "vai trò/giá" in by_price.reason
    assert "vai trò/giá" not in by_history.reason
    assert by_price.xmins != by_history.xmins

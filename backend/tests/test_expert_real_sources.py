"""Trang Chuyên gia chạy bằng dữ liệu THẬT, không còn dàn diễn viên demo.

Test ở đây khoá hai thứ khác nhau: (1) dữ liệu bịa không được quay lại, kể cả từ
một DB cũ; (2) các adapter nguồn thật xử lý đúng những trạng thái mà dữ liệu thật
SẼ rơi vào — bảng xếp hạng rỗng, đồng hạng, vòng chưa đá.
"""
import pytest
from sqlalchemy import select

from app.providers import fpl_experts as fx
from app.providers.expert_provider import DEFAULT_SIGNALS, DEFAULT_SOURCES, ExpertProvider
from app.services.expert_scoring import (
    _is_correct,
    fetch_live_points,
    positional_benchmark,
)


# ================================================= không còn dữ liệu bịa =====
def test_no_hard_coded_signals_remain():
    """Dàn "Nguồn demo A–E" và mọi phát biểu [DEMO] đã bị bỏ hẳn.

    Một trang tên là "Chuyên gia" chạy bằng dữ liệu bịa tệ hơn một trang trống —
    nó dạy người đọc tin vào một thứ không có thật.
    """
    assert DEFAULT_SIGNALS == []
    assert ExpertProvider().get_signals() == []
    names = [s.name for s in DEFAULT_SOURCES]
    assert not [n for n in names if "demo" in n.lower()]
    # Mọi nguồn còn lại phải là thực thể có thật, tức có địa chỉ kiểm chứng được.
    assert all(s.url for s in DEFAULT_SOURCES), "nguồn không có URL là nguồn bịa"


def test_provider_exposes_the_real_fpl_sources():
    names = [s.name for s in ExpertProvider().get_sources()]
    assert any("ep_next" in n for n in names)
    assert any(n.startswith("Top ") for n in names)


def test_read_path_refuses_mock_rows_left_in_a_live_database(db):
    """Dữ liệu cũ sống lâu hơn code, nên đường ĐỌC phải tự bảo vệ.

    Một DB đang chạy vẫn còn hàng `is_mock=True` gắn vào nguồn thật cho tới lần
    đồng bộ kế tiếp. Không nguồn tổng hợp nào còn tồn tại, nên mọi hàng như vậy
    phải bị từ chối chứ không chờ đường ghi dọn hộ.
    """
    from app.models import ExpertSignal, ExpertSource, Player
    from app.services.experts import expert_consensus

    src = db.scalars(select(ExpertSource)).first()
    player = db.scalars(select(Player)).first()
    assert src and player
    db.add(ExpertSignal(
        source_id=src.id, player_id=player.id, signal_type="captain",
        confidence=0.9, summary="[DEMO] phát biểu bịa", signal_score=0.5,
        is_mock=True, origin_ref=None,
    ))
    db.flush()

    out = expert_consensus(db)
    leaked = [
        s for row in out["players"] for s in row["signals"]
        if s["is_mock"] or "[DEMO]" in (s["summary"] or "")
    ]
    db.rollback()
    assert leaked == [], f"dữ liệu mock lọt ra giao diện: {leaked}"


def test_source_status_explains_every_silent_source(db):
    """Ô trống phải là một câu trả lời, không phải một khoảng lặng."""
    from app.services.experts import expert_consensus

    status = expert_consensus(db)["source_status"]
    assert status
    for s in status:
        assert s["state"] in ("đang chạy", "chưa có dữ liệu", "chưa kết nối")
        assert s["why"].strip(), f"{s['name']} im lặng mà không nêu lý do"
        if s["state"] == "đang chạy":
            assert s["signals"] > 0


# ============================================ mô hình riêng của FPL ==========
def _el(pid, name, ep, pos=3, owned="10.0"):
    return {"id": pid, "web_name": name, "ep_next": ep, "element_type": pos,
            "selected_by_percent": owned}


def test_no_captain_signal_when_the_model_has_a_tie_at_the_top():
    """Đồng hạng nghĩa là FPL không chọn ai — không được bịa ra một lựa chọn.

    Regression đo được trên dữ liệu thật (2026-08-11): `ep_next` chỉ có 24 giá trị
    phân biệt cho 519 cầu thủ, chặn ở 4.0, và **4 người đồng hạng nhất** — Raya
    (GK), Gabriel (DEF), Haaland (FWD), B.Fernandes (MID). Bản đầu gán "đội trưởng"
    cho người đứng đầu danh sách đã sắp xếp, tức **khuyến nghị bắt băng đội trưởng
    một thủ môn** thuần tuý do thứ tự tie-break.
    """
    tied = [_el(1, "Raya", "4.0", 1), _el(2, "Gabriel", "4.0", 2),
            _el(3, "Haaland", "4.0", 4), _el(4, "Fernandes", "4.0", 3),
            _el(5, "Pickford", "3.3", 1)]
    sigs = fx.fpl_model_signals(tied)
    assert [s.signal_type for s in sigs].count("captain") == 0
    assert any("đồng hạng" in s.summary for s in sigs)

    solo = [_el(1, "Solo", "7.0", 4), *tied[1:]]
    out = fx.fpl_model_signals(solo)
    caps = [s for s in out if s.signal_type == "captain"]
    assert len(caps) == 1 and caps[0].web_name == "Solo"


def test_model_signals_are_deterministic():
    """Cùng dữ liệu vào phải cho cùng kết quả ra, kể cả khi đồng hạng dày đặc."""
    els = [_el(i, f"P{i}", "4.0") for i in range(10, 0, -1)]
    a = [s.web_name for s in fx.fpl_model_signals(els)]
    b = [s.web_name for s in fx.fpl_model_signals(list(reversed(els)))]
    assert a == b


def test_model_signals_empty_when_fpl_publishes_nothing():
    assert fx.fpl_model_signals([_el(1, "A", "0"), _el(2, "B", None)]) == []


# ======================================== đồng thuận nhóm dẫn đầu ============
def test_top_manager_consensus_reports_why_it_is_empty():
    """Bảng xếp hạng rỗng là trạng thái HỢP LỆ của dữ liệu, không phải lỗi.

    Đo ngày 2026-08-11: giải 314 trả về 0 người, và `entry/{id}/event/1/picks/`
    trả 404 — FPL giấu đội hình tới sau hạn chót.
    """
    empty = fx.TopManagerConsensus()
    assert not empty.available
    assert fx.top_manager_signals(empty, [], "Top 100 FPL toàn cầu") == []


def test_top_manager_signal_is_the_edge_over_the_crowd_not_raw_ownership():
    """Tín hiệu mạnh là CHÊNH LỆCH so với toàn giải, không phải sở hữu tuyệt đối."""
    els = [_el(1, "Differential", "3.0", owned="20.0"),
           _el(2, "Template", "3.0", owned="62.0"),
           _el(3, "Faded", "3.0", owned="55.0")]
    c = fx.TopManagerConsensus(
        gameweek=5, n_managers=100,
        owned={1: 0.60, 2: 0.63, 3: 0.20}, captained={1: 0.40},
    )
    out = {s.web_name: s.signal_type for s in
           fx.top_manager_signals(c, els, "Top 100 FPL toàn cầu")}
    assert out["Differential"] == "buy"     # 60% nhóm đầu vs 20% toàn giải
    assert out["Faded"] == "avoid"          # 20% nhóm đầu vs 55% toàn giải
    assert "Template" not in out or out["Template"] == "captain"  # 63% vs 62%: không nói gì

    caps = [s for s in fx.top_manager_signals(c, els, "Top 100 FPL toàn cầu")
            if s.signal_type == "captain"]
    assert [s.web_name for s in caps] == ["Differential"]


# ================================================ chấm điểm track record =====
def test_benchmark_ignores_players_who_did_not_play():
    """Gộp người ngồi ngoài sẽ kéo trung vị về 0 và biến mọi gợi ý thành "đúng"."""
    pts = {1: 12, 2: 2, 3: 8, 4: 0, 5: 6}
    mins = {1: 90, 2: 90, 3: 90, 4: 0, 5: 90}
    pos = dict.fromkeys(pts, 4)
    assert positional_benchmark(pts, mins, pos)[4] == pytest.approx(7.0)


def test_prediction_is_scored_against_the_positional_median():
    """Chuẩn là trung vị cùng vị trí, không phải một ngưỡng điểm ghi cứng.

    Ngưỡng cố định kiểu "trên 6 điểm là đúng" thưởng cho việc gợi ý tiền đạo và
    phạt việc gợi ý hậu vệ, ở mọi mùa giải, mãi mãi.
    """
    assert _is_correct("captain", 12, 90, 7.0) is True
    assert _is_correct("buy", 4, 90, 7.0) is False
    assert _is_correct("avoid", 4, 90, 7.0) is True
    assert _is_correct("sell", 12, 90, 7.0) is False
    # chấn thương chấm theo PHÚT, không theo điểm
    assert _is_correct("injury", 1, 20, 7.0) is True
    assert _is_correct("injury", 1, 90, 7.0) is False
    # `hold` không khẳng định điều gì kiểm chứng được
    assert _is_correct("hold", 12, 90, 7.0) is None


def test_scoring_is_a_no_op_before_any_gameweek_finishes(db):
    from app.services.expert_scoring import score_finished_gameweeks

    out = score_finished_gameweeks(db)
    assert isinstance(out.get("gameweeks"), list)
    assert out.get("note")


def test_live_points_returns_empty_for_an_unplayed_gameweek():
    """Chưa đá khác 0 điểm — chỗ gọi phải phân biệt được hai thứ đó."""
    points, minutes = fetch_live_points(99)
    assert points == {} and minutes == {}


# ==================== CSDL đang chạy còn sót hàng demo =======================
def test_read_path_hides_fabricated_sources_left_in_a_live_database(db):
    """Nguồn bịa còn sót trong CSDL production KHÔNG được lọt ra giao diện.

    Đo trên production: backend chạy `xp-0.4.0` và CSDL Neon vẫn còn nguyên 5 hàng
    "Nguồn demo A–E" cùng 7 tín hiệu `[DEMO]`. Sửa `DEFAULT_SOURCES` chỉ dọn được
    đường GHI — giữa lúc deploy và lúc đồng bộ chạy, trang vẫn sẽ hiện chúng nếu
    đường ĐỌC không tự nhận ra.
    """
    from app.models import ExpertSource
    from app.services.experts import expert_consensus, is_fabricated

    ghost = ExpertSource(
        name="Nguồn demo A", source_type="site", url=None, reliability=0.7,
        historical_accuracy=0.0, expertise="lineup", independence=1.0,
        verified_track_record=False,
    )
    db.add(ghost)
    db.flush()
    assert is_fabricated(ghost), "nguồn không có địa chỉ phải bị coi là bịa"

    out = expert_consensus(db)
    names = [s["name"] for s in out["sources"]]
    states = [s["name"] for s in out["source_status"]]
    db.rollback()

    assert "Nguồn demo A" not in names, f"lọt vào danh bạ: {names}"
    assert "Nguồn demo A" not in states, f"lọt vào bảng trạng thái: {states}"


def test_real_sources_all_have_a_verifiable_address(db):
    """Mọi nguồn hiển thị phải tra lại được — đó là ranh giới thật/bịa."""
    from app.services.experts import expert_consensus

    for s in expert_consensus(db)["sources"]:
        assert (s["url"] or "").strip(), f"{s['name']} không có địa chỉ kiểm chứng"


def test_seed_removes_sources_that_left_the_registry(db):
    """Đường ghi phải XOÁ nguồn không còn trong đăng bạ, không chỉ ghi đè theo tên.

    Vòng lặp upsert theo tên không bao giờ chạm tới hàng đã bị bỏ khỏi code, nên
    5 nguồn demo sẽ sống mãi trong CSDL nếu không có phép xoá tường minh.
    """
    from app.ingestion.fpl_sync import seed_experts
    from app.models import ExpertSource

    db.add(ExpertSource(
        name="Nguồn demo Z", source_type="site", url=None, reliability=0.5,
        historical_accuracy=0.0, expertise="lineup", independence=1.0,
        verified_track_record=False,
    ))
    db.commit()

    result = seed_experts(db)
    left = [s.name for s in db.scalars(select(ExpertSource)).all()]
    assert "Nguồn demo Z" not in left
    assert "Nguồn demo Z" in result["removed"]

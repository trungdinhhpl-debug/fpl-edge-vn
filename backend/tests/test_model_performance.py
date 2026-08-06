"""Model Performance — khoá phần toán và cơ chế chống data leakage.

Trang này khó test theo kiểu thường: hôm nay chưa vòng nào đá xong nên mọi ô đều
trống, và một bộ test chỉ kiểm "trống" sẽ vẫn xanh dù phép toán sai hoàn toàn. Nên
nhóm test dưới đây làm hai việc tách biệt:

  * kiểm PHÉP TOÁN trên dữ liệu dựng sẵn có đáp số biết trước;
  * kiểm CƠ CHẾ: snapshot bị khoá sau deadline, và lý do "chưa có số" phải đúng
    nguyên nhân.
"""
import math
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.services.model_performance import (
    MIN_SAMPLE,
    MIN_XMINS_FOR_SCORING,
    _is_degenerate,
    brier,
    calibration_error,
    capture_snapshots,
    decision_metrics,
    mae,
    model_performance,
    player_metrics,
    rmse,
    spearman,
    top_k_precision,
)


# ------------------------------------------------------------------ toán -------
def test_spearman_known_values():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    # đơn điệu nhưng không tuyến tính: Spearman vẫn phải là 1
    assert spearman([1, 2, 3, 4], [1, 10, 100, 1000]) == pytest.approx(1.0)
    assert spearman([1, 2], [1, 2]) is None          # quá ít
    assert spearman([1, 1, 1], [1, 2, 3]) is None    # không có phương sai


def test_spearman_handles_ties_by_average_rank():
    """Đồng hạng phải nhận hạng trung bình, không phải thứ tự đọc từ database.

    Rất nhiều cầu thủ cùng 2 điểm; nếu xử lý sai thì hệ số đổi theo thứ tự dòng.
    """
    pred = [1.0, 2.0, 3.0, 4.0]
    actual = [5.0, 5.0, 9.0, 9.0]
    got = spearman(pred, actual)

    # Tính chất then chốt: đảo thứ tự trong hai cặp đồng hạng thì kết quả y nguyên.
    got2 = spearman([2.0, 1.0, 4.0, 3.0], [5.0, 5.0, 9.0, 9.0])
    assert got == pytest.approx(got2)

    # Đồng hạng làm hệ số tối đa THẤP HƠN 1: hạng thực tế là [1.5, 1.5, 3.5, 3.5],
    # tương quan với [1,2,3,4] chỉ đạt 4/(√5·2) = 0.8944. Kỳ vọng 1.0 là sai —
    # xếp đúng thứ tự vẫn không thể đạt 1 khi kết quả có đồng hạng.
    assert got == pytest.approx(4 / (math.sqrt(5) * 2))
    assert 0.85 < got < 1.0


def test_mae_and_rmse_known_values():
    pred = [2.0, 4.0, 6.0]
    actual = [1.0, 4.0, 10.0]
    assert mae(pred, actual) == pytest.approx((1 + 0 + 4) / 3)
    assert rmse(pred, actual) == pytest.approx(math.sqrt((1 + 0 + 16) / 3))
    # RMSE luôn >= MAE, và lớn hơn hẳn khi có một sai số lớn
    assert rmse(pred, actual) > mae(pred, actual)
    assert mae([], []) is None


def test_top_k_precision_counts_ties_at_the_boundary():
    """Hai cầu thủ cùng điểm ở mép top-k phải được đối xử như nhau."""
    # mô hình xếp đúng 2 trong 2 người dẫn đầu
    pred = [10.0, 9.0, 1.0, 0.5]
    actual = [8.0, 7.0, 2.0, 1.0]
    assert top_k_precision(pred, actual, 2) == pytest.approx(1.0)

    # mô hình xếp sai hoàn toàn
    assert top_k_precision([1.0, 0.5, 10.0, 9.0], actual, 2) == pytest.approx(0.0)

    # đồng điểm ở mốc cắt: người thứ 3 bằng điểm người thứ 2 -> vẫn tính là top-2
    pred2 = [10.0, 1.0, 9.0, 0.5]
    actual2 = [8.0, 7.0, 7.0, 1.0]
    assert top_k_precision(pred2, actual2, 2) == pytest.approx(1.0)

    assert top_k_precision([1.0], [1.0], 10) is None      # ít hơn k


def test_brier_known_values():
    assert brier([1.0, 0.0], [True, False]) == pytest.approx(0.0)   # hoàn hảo
    assert brier([0.0, 1.0], [True, False]) == pytest.approx(1.0)   # sai hẳn
    assert brier([0.5, 0.5], [True, False]) == pytest.approx(0.25)  # luôn đoán 50%
    assert brier([], []) is None


def test_calibration_error_is_zero_when_perfectly_calibrated():
    """Nói 30% và đúng 30% số lần thì ECE phải bằng 0."""
    probs = [0.3] * 100
    outcomes = [True] * 30 + [False] * 70
    ece, detail = calibration_error(probs, outcomes)
    assert ece == pytest.approx(0.0, abs=1e-9)
    assert detail and detail[0]["n"] == 100

    # quá tự tin: nói 90% mà chỉ xảy ra 10%
    ece_bad, _ = calibration_error([0.9] * 100, [True] * 10 + [False] * 90)
    assert ece_bad == pytest.approx(0.8, abs=1e-9)
    assert calibration_error([], []) == (None, [])


def test_degenerate_predictor_detected():
    assert _is_degenerate([0.0] * 50) is True
    assert _is_degenerate([1.0]) is True
    assert _is_degenerate([0.0, 0.0, 0.1]) is False


# ----------------------------------------------------- chống data leakage ------
def test_snapshot_locks_after_deadline_and_never_reopens(db):
    """Cơ chế quan trọng nhất của trang này.

    Sau deadline, bản ghi phải bị khoá: một lần chạy engine muộn hơn — lúc đã biết
    đội hình ra sân, biết ai chấn thương — không được lặng lẽ sửa lại "dự báo" cho
    đẹp điểm. Không có khoá này thì mọi con số trên trang đều vô giá trị.
    """
    from app import scoring
    from app.models import Gameweek, PlayerProjection, ProjectionSnapshot

    gw = db.scalar(select(PlayerProjection.gameweek).order_by(PlayerProjection.gameweek))
    assert gw is not None, "cần có dự báo để chạy test này"
    row = db.get(Gameweek, gw)
    original_deadline = row.deadline_time

    try:
        # --- deadline còn ở tương lai: cập nhật được ---
        row.deadline_time = datetime.now(timezone.utc) + timedelta(days=3)
        db.flush()
        first = capture_snapshots(db, gw)
        assert first["ok"] and first["past_deadline"] is False
        assert first["written"] > 0 and first["locked"] == 0

        snap = db.scalars(
            select(ProjectionSnapshot).where(
                ProjectionSnapshot.season == scoring.SEASON,
                ProjectionSnapshot.gameweek == gw,
            )
        ).first()
        assert snap is not None and snap.is_locked is False
        pid, frozen_xp = snap.player_id, snap.xp

        # --- deadline đã qua: lượt chụp này khoá bản ghi ---
        row.deadline_time = datetime.now(timezone.utc) - timedelta(hours=1)
        db.flush()
        second = capture_snapshots(db, gw)
        assert second["past_deadline"] is True
        assert second["locked"] > 0

        # --- engine chạy lại và đổi dự báo: snapshot KHÔNG được đổi theo ---
        proj = db.scalar(
            select(PlayerProjection).where(
                PlayerProjection.gameweek == gw, PlayerProjection.player_id == pid
            )
        )
        proj.xp = frozen_xp + 99.0
        db.flush()
        third = capture_snapshots(db, gw)
        assert third["written"] == 0, "đã khoá mà vẫn ghi"
        assert third["skipped_already_locked"] > 0

        after = db.scalar(
            select(ProjectionSnapshot).where(
                ProjectionSnapshot.season == scoring.SEASON,
                ProjectionSnapshot.gameweek == gw,
                ProjectionSnapshot.player_id == pid,
            )
        )
        assert after.xp == pytest.approx(frozen_xp), "dự báo đã đóng băng bị sửa"
    finally:
        row.deadline_time = original_deadline
        db.flush()
        db.rollback()


# ------------------------------------------------------- trạng thái các ô ------
def test_metrics_report_a_reason_not_just_a_dash(db):
    """Ô trống phải kèm lý do cụ thể; ô 'không áp dụng' phải khác 'chưa có dữ liệu'."""
    m = player_metrics(db)
    assert len(m["rows"]) == 6

    for row in m["rows"]:
        for col in ("model", "baseline_form", "baseline_market"):
            cell = row[col]
            assert cell["status"] in ("ok", "no_data", "not_applicable")
            if cell["status"] != "ok":
                assert cell["unlock"], f"{row['metric']}/{col} trống mà không nói vì sao"
        assert row["explain"], row["metric"]

    # Brier và Calibration KHÔNG định nghĩa được cho hai cột baseline: form là một
    # con số điểm, kèo ra giá cho trận — cả hai không phát ra xác suất nào để chấm
    for name in ("Brier score P(start)", "Calibration P(10+)"):
        row = next(r for r in m["rows"] if r["metric"] == name)
        assert row["baseline_form"]["status"] == "not_applicable"
        assert row["baseline_market"]["status"] == "not_applicable"


def test_form_baseline_blank_reason_names_the_reset_not_the_sample_size(db):
    """Trước vòng 1, FPL đặt `form` về 0 cho mọi cầu thủ.

    Lý do cột đó trống phải là chuyện đặt lại đó, không phải "chưa đủ mẫu" — nói sai
    nguyên nhân sẽ khiến người đọc chờ thêm dữ liệu trong khi vấn đề là khác.
    """
    from app.models import Gameweek, PlayerProjection, ProjectionSnapshot

    gw = db.scalar(select(PlayerProjection.gameweek).order_by(PlayerProjection.gameweek))
    row = db.get(Gameweek, gw)
    original = row.deadline_time
    try:
        row.deadline_time = datetime.now(timezone.utc) + timedelta(days=3)
        db.flush()
        capture_snapshots(db, gw)

        snaps = db.scalars(
            select(ProjectionSnapshot).where(ProjectionSnapshot.gameweek == gw)
        ).all()
        scored = [s for s in snaps if (s.xmins or 0) >= MIN_XMINS_FOR_SCORING]
        if len(scored) < MIN_SAMPLE:
            pytest.skip("bộ dữ liệu demo không đủ mẫu để chạm nhánh này")

        for s in snaps:
            s.actual_points = 3
            s.actual_started = True
            s.baseline_form = 0.0       # hằng số, đúng như FPL trả về tiền mùa
        db.flush()

        m = player_metrics(db)
        spear = next(r for r in m["rows"] if r["metric"] == "Spearman rank correlation")
        why = spear["baseline_form"]["unlock"].lower()
        assert spear["baseline_form"]["status"] == "no_data"
        assert "form" in why and ("đặt lại" in why or "hằng số" in why)
        assert "cần ít nhất" not in why, "quy sai nguyên nhân sang cỡ mẫu"
    finally:
        row.deadline_time = original
        db.flush()
        db.rollback()


def test_decision_metrics_explain_what_is_missing(db):
    d = decision_metrics(db)
    assert len(d["rows"]) == 6
    for row in d["rows"]:
        assert row["explain"]
        r = row["result"]
        assert r["status"] in ("ok", "no_data", "not_applicable")
        if r["status"] != "ok":
            assert r["unlock"]

    # hai chỉ số bị chặn bởi chính hệ thống (chưa lưu khuyến nghị), phải nói ra
    cap = next(r for r in d["rows"] if r["metric"] == "Captain top pick hit rate")
    assert "không được lưu" in cap["result"]["unlock"].lower() or \
           "chưa" in cap["result"]["unlock"].lower()


def test_full_payload_declares_how_it_works(db):
    r = model_performance(db)
    assert r["season"]
    assert "snapshots" in r["state"]
    assert r["player_forecasting"]["rows"]
    assert r["decisions"]["rows"]
    assert len(r["how_it_works"]) >= 3
    # phải nói rõ vì sao cần bảng snapshot, đó là điều kiện nền của cả trang
    joined = " ".join(r["how_it_works"]).lower()
    assert "snapshot" in joined or "đóng băng" in joined


# ------------------------------------------------- baseline kèo & đội trưởng ----
def test_market_baseline_is_the_same_engine_with_only_team_strength_swapped(db):
    """Baseline kèo phải KHÁC dự báo chính, và không được ghi gì vào DB.

    Nếu nó giống hệt thì cờ `market_only` không có tác dụng gì; nếu nó ghi vào
    `player_projections` thì baseline vừa làm bẩn chính dữ liệu nó đi đo.
    """
    from sqlalchemy import func

    from app.engine.projections import build_projections
    from app.models import PlayerProjection

    before = db.scalar(select(func.count()).select_from(PlayerProjection))
    cutoffs_before = db.scalar(select(func.max(PlayerProjection.data_cutoff)))

    res = build_projections(db, horizon=1, market_only=True, persist=False)
    assert res.get("market_only") is True
    assert "xp" in res

    # không đụng vào dữ liệu chính
    assert db.scalar(select(func.count()).select_from(PlayerProjection)) == before
    assert db.scalar(select(func.max(PlayerProjection.data_cutoff))) == cutoffs_before

    if not res["xp"]:
        pytest.skip("bộ dữ liệu không có kèo nên baseline rỗng — đúng theo thiết kế")

    # phải khác dự báo chính ở ít nhất vài cầu thủ
    main = {
        (r.player_id, r.gameweek): r.xp
        for r in db.scalars(select(PlayerProjection)).all()
    }
    diffs = [
        abs(v - main[k]) for k, v in res["xp"].items() if k in main
    ]
    assert diffs, "không ghép được cặp nào để so"
    assert max(diffs) > 1e-9, "market_only không đổi gì — cờ vô tác dụng"


def test_market_baseline_skips_fixtures_without_odds(db):
    """Trận không có kèo phải để TRỐNG, không được lấy mô hình nội bộ điền vào.

    Nếu điền, 'baseline kèo' thật ra là chính mô hình đội lốt, và phép so trở thành
    so với chính mình.
    """
    from app.engine.projections import build_projections
    from app.models import MarketOdds

    gws_with_odds = {o.gameweek for o in db.scalars(select(MarketOdds)).all()}
    res = build_projections(db, horizon=8, market_only=True, persist=False)
    gws_computed = {gw for _, gw in res["xp"]}

    assert gws_computed <= gws_with_odds, (
        f"tính baseline cho vòng không có kèo: {gws_computed - gws_with_odds}"
    )


def test_market_baseline_declares_that_it_is_a_weak_discriminator():
    """Cảnh báo phải có sẵn trong payload, không để người đọc tự suy ra."""
    from app.services.model_performance import market_baseline_status

    st = market_baseline_status()
    assert st["wired"] is True
    assert st["definition"]
    assert st["caveat"], "thiếu cảnh báo về việc mô hình đã chứa kèo"
    assert "chứa" in st["caveat"].lower() or "đã pha" in st["caveat"].lower()


def test_captain_picks_archived_for_every_list_and_locked_after_deadline(db):
    """Lưu cả bốn bảng, và sau deadline thì khoá — cùng cơ chế với snapshot."""
    from app import scoring
    from app.models import CaptainPick, Gameweek, PlayerProjection
    from app.services.model_performance import capture_captain_picks

    gw = db.scalar(select(PlayerProjection.gameweek).order_by(PlayerProjection.gameweek))
    row = db.get(Gameweek, gw)
    original = row.deadline_time
    try:
        row.deadline_time = datetime.now(timezone.utc) + timedelta(days=3)
        db.flush()

        r = capture_captain_picks(db, gw)
        if not r.get("ok"):
            pytest.skip(f"không dựng được bảng đội trưởng: {r.get('reason')}")
        assert set(r["lists"]) == {"ev", "safe", "ceiling", "chase"}
        assert r["written"] > 0 and r["past_deadline"] is False

        picks = db.scalars(
            select(CaptainPick).where(
                CaptainPick.season == scoring.SEASON, CaptainPick.gameweek == gw
            )
        ).all()
        assert {p.list_kind for p in picks} == {"ev", "safe", "ceiling", "chase"}
        # mỗi bảng phải có một lựa chọn số 1
        for kind in ("ev", "safe", "ceiling", "chase"):
            assert any(p.list_kind == kind and p.rank == 1 for p in picks)

        top_ev = next(p for p in picks if p.list_kind == "ev" and p.rank == 1)
        frozen_player = top_ev.player_id

        # sau deadline: khoá, và lượt chụp sau không được đổi lựa chọn đã lưu
        row.deadline_time = datetime.now(timezone.utc) - timedelta(hours=1)
        db.flush()
        capture_captain_picks(db, gw)
        again = capture_captain_picks(db, gw)
        assert again["written"] == 0
        assert again["skipped_already_locked"] > 0

        still = db.scalar(
            select(CaptainPick).where(
                CaptainPick.season == scoring.SEASON,
                CaptainPick.gameweek == gw,
                CaptainPick.list_kind == "ev",
                CaptainPick.rank == 1,
            )
        )
        assert still.player_id == frozen_player
    finally:
        row.deadline_time = original
        db.flush()
        db.rollback()


def test_captain_hit_rate_computes_per_list_when_outcomes_exist(db):
    """Chấm được từng bảng riêng, và định nghĩa 'đúng' là so trong nhóm ứng viên."""
    from app import scoring
    from app.models import CaptainPick, Gameweek, PlayerProjection, ProjectionSnapshot
    from app.services.model_performance import (
        capture_captain_picks,
        capture_snapshots,
        captain_hit_rate,
    )

    gw = db.scalar(select(PlayerProjection.gameweek).order_by(PlayerProjection.gameweek))
    row = db.get(Gameweek, gw)
    original = row.deadline_time
    try:
        row.deadline_time = datetime.now(timezone.utc) + timedelta(days=3)
        db.flush()
        capture_snapshots(db, gw)
        r = capture_captain_picks(db, gw)
        if not r.get("ok"):
            pytest.skip("không dựng được bảng đội trưởng")

        picks = db.scalars(
            select(CaptainPick).where(CaptainPick.gameweek == gw)
        ).all()
        ev_top = next(p for p in picks if p.list_kind == "ev" and p.rank == 1)

        # cho lựa chọn số 1 của bảng EV ghi điểm cao nhất -> hit_rate phải là 1.0
        for s in db.scalars(
            select(ProjectionSnapshot).where(ProjectionSnapshot.gameweek == gw)
        ).all():
            s.actual_points = 20 if s.player_id == ev_top.player_id else 1
            s.actual_started = True
        db.flush()

        hr = captain_hit_rate(db)
        assert hr["n_gameweeks"] >= 1
        assert hr["by_list"]["ev"]["hit_rate"] == pytest.approx(1.0)
        assert hr["by_list"]["ev"]["top_n_hit_rate"] == pytest.approx(1.0)

        # giờ cho một người KHÔNG được đề xuất ghi cao nhất -> EV phải trượt
        # phải chọn người NẰM TRONG mẫu so sánh (xMins ≥ ngưỡng), nếu không họ bị
        # loại khỏi phép tính và test sẽ không chạm được nhánh "trượt"
        others = [
            s for s in db.scalars(
                select(ProjectionSnapshot).where(ProjectionSnapshot.gameweek == gw)
            ).all()
            if s.player_id not in {p.player_id for p in picks}
            and (s.xmins or 0) >= MIN_XMINS_FOR_SCORING
        ]
        if others:
            others[0].actual_points = 50
            db.flush()
            hr2 = captain_hit_rate(db)
            assert hr2["by_list"]["ev"]["hit_rate"] == pytest.approx(0.0)
    finally:
        row.deadline_time = original
        db.flush()
        db.rollback()

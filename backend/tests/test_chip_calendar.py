"""Chip Calendar — khoá lại đúng những chỗ dễ trở thành số bịa.

Ba thứ được bảo vệ bằng test vì chúng là loại lỗi mà giao diện sẽ che mất: vòng
ngoài tầm dự báo phải TRỐNG chứ không phải 0, xác suất blank/double phải là None
chứ không phải một con số suy diễn, và gain của Wildcard phải so trên khoảng cố
định chứ không giảm dần chỉ vì tầm dự báo hết.
"""
import pytest
from sqlalchemy import select

from app.models import Player
from app.services.chip_calendar import (
    WILDCARD_WINDOW,
    chip_calendar,
    chip_windows,
    fixture_outlook,
    projection_horizon,
)
from app.services.common import planning_start_gw, projections_for_gw, team_lookup


def _legal_squad(db) -> list[int]:
    """15 người xP cao nhất nhưng vẫn đúng cơ cấu vị trí và tối đa 3 người/CLB."""
    gw = planning_start_gw(db)
    projs = projections_for_gw(db, gw)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    need = {1: 2, 2: 5, 3: 5, 4: 3}
    per_club: dict[int, int] = {}
    squad: list[int] = []
    for pid, pr in sorted(projs.items(), key=lambda kv: -kv[1].xp):
        p = players.get(pid)
        if not p or need.get(p.element_type, 0) <= 0:
            continue
        if per_club.get(p.team_id, 0) >= 3:
            continue
        squad.append(pid)
        need[p.element_type] -= 1
        per_club[p.team_id] = per_club.get(p.team_id, 0) + 1
    return squad


# Payload chip đúng như FPL trả về cho 2026/27 (đã kiểm chứng từ bootstrap-static
# ngày 2026-08-05). Cài thẳng vào DB test để các test dưới đây không phụ thuộc vào
# hình dạng của bộ dữ liệu demo — demo chỉ có 10 vòng nên cửa sổ chip của nó khác.
REAL_CHIPS_2026_27 = [
    {"name": "wildcard", "chip_type": "transfer", "start_event": 2, "stop_event": 19},
    {"name": "wildcard", "chip_type": "transfer", "start_event": 20, "stop_event": 38},
    {"name": "freehit", "chip_type": "transfer", "start_event": 2, "stop_event": 19},
    {"name": "freehit", "chip_type": "transfer", "start_event": 20, "stop_event": 38},
    {"name": "bboost", "chip_type": "team", "start_event": 1, "stop_event": 19},
    {"name": "bboost", "chip_type": "team", "start_event": 20, "stop_event": 38},
    {"name": "3xc", "chip_type": "team", "start_event": 1, "stop_event": 19},
    {"name": "3xc", "chip_type": "team", "start_event": 20, "stop_event": 38},
]


@pytest.fixture
def real_chips(db):
    """Tạm cài khung chip thật của 2026/27 vào mùa hiện tại, rồi trả nguyên trạng."""
    import json as _json

    from app.models import Season

    season = db.scalar(select(Season).where(Season.is_current.is_(True)))
    assert season is not None
    original = season.chips_json
    season.chips_json = _json.dumps(REAL_CHIPS_2026_27, ensure_ascii=False)
    db.flush()
    try:
        yield db
    finally:
        season.chips_json = original
        db.flush()
        db.rollback()


def test_chip_windows_structure_holds_on_any_season(db):
    """Bất kể mùa nào: 4 loại chip, mỗi loại 2 bộ, hai bộ không chồng nhau."""
    windows = chip_windows(db)
    assert windows, "không đọc được cửa sổ chip"

    by_chip: dict[str, list[dict]] = {}
    for w in windows:
        by_chip.setdefault(w["chip"], []).append(w)

    assert set(by_chip) == {"wildcard", "freehit", "bboost", "3xc"}
    assert all(len(v) == 2 for v in by_chip.values())
    assert len(windows) == 8

    for chip, ws in by_chip.items():
        first = next(w for w in ws if w["set_index"] == 0)
        second = next(w for w in ws if w["set_index"] == 1)
        # bộ nửa đầu kết thúc TRƯỚC khi bộ nửa sau mở: chip không chuyển nửa mùa
        assert first["stop_event"] < second["start_event"], chip
        assert first["start_event"] <= first["stop_event"], chip


def test_chip_windows_parse_the_real_2026_27_payload(real_chips):
    """Khung thật của 2026/27, gồm chi tiết chỉ có trong API."""
    windows = chip_windows(real_chips)
    by_chip: dict[str, list[dict]] = {}
    for w in windows:
        by_chip.setdefault(w["chip"], []).append(w)

    for chip in ("wildcard", "freehit", "bboost", "3xc"):
        first = next(w for w in by_chip[chip] if w["set_index"] == 0)
        second = next(w for w in by_chip[chip] if w["set_index"] == 1)
        assert first["stop_event"] == 19, chip     # bộ đầu hết hạn sau GW19
        assert second["start_event"] == 20, chip
        assert second["stop_event"] == 38, chip

    # Wildcard/Free Hit mở từ GW2, Bench Boost/Triple Captain từ GW1 — dễ ghi cứng sai
    assert next(w for w in by_chip["wildcard"] if w["set_index"] == 0)["start_event"] == 2
    assert next(w for w in by_chip["freehit"] if w["set_index"] == 0)["start_event"] == 2
    assert next(w for w in by_chip["bboost"] if w["set_index"] == 0)["start_event"] == 1
    assert next(w for w in by_chip["3xc"] if w["set_index"] == 0)["start_event"] == 1


def test_blank_double_probability_is_never_invented(db):
    """Không được trả về xác suất blank/double: dữ liệu không cho phép suy ra."""
    teams = team_lookup(db)
    for gw in (1, 5, 19, 34):
        out = fixture_outlook(db, gw, teams)
        assert out["probability"] is None, f"GW{gw} bịa xác suất"
        assert out["note"]
        if out["known"]:
            # lịch trước khi đá luôn là tạm thời, phải nói rõ
            assert out["provisional"] is True


def test_gameweeks_beyond_projection_horizon_have_no_number(real_chips):
    """Vòng ngoài tầm dự báo phải là None + no_projection, tuyệt đối không phải 0.

    Chạy trên khung chip thật (tới GW38) nên chắc chắn có vòng vượt tầm dự báo —
    đó là tình huống của người dùng thật ở đầu mùa.
    """
    db = real_chips
    horizon = projection_horizon(db)
    assert horizon, "cần có dự báo để chạy test này"
    hi = horizon[1]

    r = chip_calendar(db, squad_ids=_legal_squad(db), bank=0, free_transfers=1)
    seen_beyond = 0
    for c in r["chips"]:
        for o in c["options"]:
            if o["gameweek"] > hi and not c["used"]:
                assert o["gain"] is None, f"{c['chip']} GW{o['gameweek']} có số nhưng ngoài tầm"
                assert o["status"] == "no_projection"
                assert o["detail"]
                seen_beyond += 1
    assert seen_beyond > 0, "test không chạm được vòng nào ngoài tầm dự báo"

    # và giới hạn đó phải được nêu ra ngoài payload, không chỉ ẩn trong từng ô
    assert any("dự báo" in lim.lower() for lim in r["limits"])


def test_wildcard_gain_uses_a_fixed_window(real_chips):
    """Gain Wildcard không được giảm dần chỉ vì khoảng đo ngắn lại.

    Nếu đo "từ vòng này tới hết tầm dự báo" thì vòng sớm nhất luôn thắng — bảng sẽ
    luôn khuyên dùng ngay, vì lý do kỹ thuật chứ không phải lý do bóng đá.
    """
    db = real_chips
    horizon = projection_horizon(db)
    hi = horizon[1]
    r = chip_calendar(db, squad_ids=_legal_squad(db), bank=0, free_transfers=1)
    wc = next(c for c in r["chips"] if c["chip"] == "wildcard" and c["set_index"] == 0)

    scored = [o for o in wc["options"] if o["gain"] is not None]
    assert scored, "không tính được vòng nào cho wildcard"

    # mọi vòng tính được đều phải còn đủ WILDCARD_WINDOW vòng dự báo phía sau
    for o in scored:
        assert o["gameweek"] + WILDCARD_WINDOW - 1 <= hi

    # vòng không đủ tầm phải nói rõ là thiếu dự báo, không phải thiếu đội hình
    short = [o for o in wc["options"] if o["gain"] is None and o["gameweek"] <= hi]
    assert short, "test không chạm được vòng thiếu tầm dự báo"
    assert all(o["status"] == "no_projection" for o in short)


def test_used_chip_is_excluded_from_recommendations(db):
    r = chip_calendar(
        db, squad_ids=_legal_squad(db), bank=0, free_transfers=1,
        chips_used=["bboost"],
    )
    bb = next(c for c in r["chips"] if c["chip"] == "bboost" and c["set_index"] == 0)
    assert bb["used"] is True
    assert bb["best"] is None
    assert bb["recommendation"]["action"] == "Đã dùng"
    # chip đã tiêu không được kéo theo ghi chú về dự báo — vô nghĩa
    assert bb["hold_note"] == ""

    # chip khác vẫn tính bình thường
    tc = next(c for c in r["chips"] if c["chip"] == "3xc" and c["set_index"] == 0)
    assert tc["used"] is False


def test_calendar_works_without_a_squad(db):
    """Không có đội vẫn phải trả về cửa sổ chip và giới hạn, chỉ thiếu điểm."""
    r = chip_calendar(db, squad_ids=[], bank=0, free_transfers=1)
    assert r["squad"]["provided"] is False
    assert len(r["chips"]) == 8
    for c in r["chips"]:
        assert c["best"] is None
        for o in c["options"]:
            assert o["gain"] is None
            assert o["status"] in ("needs_squad", "no_projection")
    assert r["limits"]


def test_bench_boost_gain_equals_bench_xp(db):
    """Bench Boost = tổng xP băng ghế, không phải một con số mô hình riêng."""
    from app.services.chip_calendar import bench_boost_gain
    from app.services.chip_calendar import _xi_and_bench

    squad = _legal_squad(db)
    gw = planning_start_gw(db)
    gain, detail, status = bench_boost_gain(db, gw, squad)
    assert status == "ok"

    res, values = _xi_and_bench(db, gw, squad)
    assert gain == pytest.approx(sum(values[pid] for pid in res.bench), abs=1e-6)
    assert len(res.bench) == 4


def test_conflicts_flag_two_chips_peaking_in_one_gameweek(db):
    """FPL chỉ cho dùng một chip mỗi vòng — bảng phải nói ra khi đỉnh trùng nhau."""
    r = chip_calendar(db, squad_ids=_legal_squad(db), bank=0, free_transfers=1)
    peaks: dict[int, list[str]] = {}
    for c in r["chips"]:
        if c["used"] or not c["best"]:
            continue
        peaks.setdefault(c["best"]["gameweek"], []).append(c["label"])

    expected = {gw for gw, labels in peaks.items() if len(labels) > 1}
    reported = {x["gameweek"] for x in r["conflicts"]}
    assert reported == expected
    for x in r["conflicts"]:
        assert x["keep"] in x["chips"]
        assert len(x["chips"]) >= 2


def test_missing_squad_is_not_reported_as_missing_projections(real_chips):
    """Sai lý do cũng là sai: thiếu đội hình không được báo là thiếu dự báo.

    Bản đầu luôn nói "không vòng nào có dự báo" dù dự báo GW1–8 có đủ, khiến người
    đọc đi chạy lại dự báo trong khi việc cần làm là nhập Team ID.
    """
    db = real_chips
    horizon = projection_horizon(db)
    assert horizon, "cần có dự báo để test này có nghĩa"

    r = chip_calendar(db, squad_ids=[], bank=0, free_transfers=1)
    assert r["squad"]["provided"] is False
    for c in r["chips"]:
        reason = c["recommendation"]["reason"].lower()
        assert "đội hình" in reason or "team id" in reason, reason
        # và KHÔNG được quy cho dự báo, vì dự báo đang có
        assert "không vòng nào trong cửa sổ có dự báo" not in reason

    # ngược lại: có đội hình thì lý do chặn phải là chuyện dự báo, không phải đội
    r2 = chip_calendar(db, squad_ids=_legal_squad(db), bank=0, free_transfers=1)
    second_half = [c for c in r2["chips"] if c["set_index"] == 1]
    for c in second_half:
        reason = c["recommendation"]["reason"].lower()
        assert "dự báo" in reason, reason
        assert "team id" not in reason

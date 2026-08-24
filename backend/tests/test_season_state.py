"""Pre-season mode: the phase label, and down-weighting stale prior data."""
import pytest

from app.engine.xmins import estimate_minutes
from app.services.season_state import (
    EARLY_SEASON_MATCHES, classify_phase, season_state,
)


REGULAR = dict(element_type=3, status="a", chance_of_playing=None,
               season_starts=30, season_minutes=2600, team_matches_played=0,
               recent_minutes=None)
FRINGE = {**REGULAR, "season_starts": 6, "season_minutes": 700}


def test_phase_classification_covers_all_three():
    """Pure so every branch is tested — the demo DB sits mid-season while
    production is pre-season, so a DB-driven test only ever sees one."""
    assert classify_phase(0, 0) == ("preseason", "PRE-SEASON PROJECTION", "Low")
    assert classify_phase(4, 1) == ("early", "EARLY-SEASON PROJECTION", "Medium")
    assert classify_phase(40, EARLY_SEASON_MATCHES - 1)[0] == "early"
    assert classify_phase(60, EARLY_SEASON_MATCHES) == ("established", None, "High")
    # a label only exists while the numbers need a caveat
    assert classify_phase(60, 20)[1] is None


def test_prior_share_is_the_complement_of_what_was_observed(db):
    """Counted by what is ABSENT, so nothing prior-based is missed.

    Regression: an earlier version matched the phrase "season averages" and
    reported 64% in a week when nothing had been played, silently excluding
    promoted-club role estimates and availability-flagged players, which are
    every bit as much priors.
    """
    s = season_state(db)
    assert s["prior_based_count"] + _observed(db) == s["projection_count"]
    assert 0 <= s["prior_based_share_pct"] <= 100


def _observed(db):
    from sqlalchemy import func, select

    from app.models import ExpectedMinutes

    return db.scalar(
        select(func.count()).select_from(ExpectedMinutes)
        .where(ExpectedMinutes.reason.like("%recent games%"))
    ) or 0


def test_empty_downweight_lists_read_as_unknown_not_as_none(db, monkeypatch):
    """An unfilled list means nobody told us, never 'nothing changed'."""
    from app.config import settings

    monkeypatch.setattr(settings, "new_manager_clubs", "")
    monkeypatch.setattr(settings, "new_signing_players", "")
    dw = season_state(db)["downweighting"]
    assert dw["configured"] is False
    assert "CHƯA AI KHAI" in dw["note"]


def test_configured_lists_are_reported_back(db):
    """What is actually being down-weighted must be visible, not implicit."""
    dw = season_state(db)["downweighting"]
    assert dw["configured"] is True
    assert dw["new_manager_clubs"], "2026/27 manager list should be populated"
    assert dw["new_signing_players"], "2026/27 signing list should be populated"


def test_configured_entries_are_well_formed(db):
    """A typo silently down-weights nobody, so at least check the shape."""
    dw = season_state(db)["downweighting"]
    for code in dw["new_manager_clubs"]:
        assert code.isalpha() and 2 <= len(code) <= 4, code
    for pid in dw["new_signing_players"]:
        assert pid.isdigit(), pid


def test_configured_entries_match_a_real_dataset(db):
    """Only meaningful against real FPL data — the demo DB has 6 invented clubs.

    Skipped rather than weakened: an assertion that passes on demo data would
    give false assurance about the production config, which is the only place
    a typo actually costs anything.
    """
    from sqlalchemy import select

    from app.models import Player, Team

    clubs = {t.short_name.upper() for t in db.scalars(select(Team)).all()}
    if len(clubs) < 20:
        pytest.skip("demo dataset — run against a synced database to check config")

    dw = season_state(db)["downweighting"]
    assert not [c for c in dw["new_manager_clubs"] if c not in clubs]
    known = {str(p.id) for p in db.scalars(select(Player)).all()}
    assert not [p for p in dw["new_signing_players"] if p not in known]


def test_thresholds_are_exposed_for_the_ui(db):
    s = season_state(db)
    assert s["matches_until_established"] == max(
        0, EARLY_SEASON_MATCHES - s["max_team_matches"]
    )


# --------------------------------------------------- prior down-weighting ----
def test_downweighting_pulls_toward_the_baseline_in_both_directions():
    """Reducing trust in last season means moving toward the neutral prior.

    A regular who moved club drops; a fringe player who moved RISES. Anything
    that only ever lowered the estimate would be a penalty, not a re-weighting.
    """
    reg_full = estimate_minutes(prior_reliability=1.0, **REGULAR).p_start
    reg_moved = estimate_minutes(prior_reliability=0.4, **REGULAR).p_start
    assert reg_moved < reg_full

    fringe_full = estimate_minutes(prior_reliability=1.0, **FRINGE).p_start
    fringe_moved = estimate_minutes(prior_reliability=0.4, **FRINGE).p_start
    assert fringe_moved > fringe_full

    # both converge toward the middle, not past each other
    assert fringe_moved < reg_moved


def test_downweighting_is_strong_enough_to_matter():
    """Regression: scaling PRIOR_GAMES alone moved p_start by ~2pp on a
    38-game sample, which is not reducing the weight in any real sense."""
    full = estimate_minutes(prior_reliability=1.0, **REGULAR).p_start
    moved = estimate_minutes(prior_reliability=0.4, **REGULAR).p_start
    assert full - moved > 0.08, f"only moved {full - moved:.3f}"


def test_full_reliability_changes_nothing():
    """A player with no flags must project exactly as before."""
    a = estimate_minutes(**REGULAR)
    b = estimate_minutes(prior_reliability=1.0, **REGULAR)
    assert a.p_start == b.p_start
    assert a.xmins == b.xmins


def test_reliability_is_clamped():
    """Absurd config values must not produce absurd projections."""
    for bad in (0.0, -5.0, 99.0):
        e = estimate_minutes(prior_reliability=bad, **REGULAR)
        assert 0.0 <= e.p_start <= 1.0
        assert e.xmins >= 0


def test_version_endpoint_carries_the_phase(db):
    """The label has to travel with every page, not sit in a methodology note."""
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/api/meta/version").json()
    st = body["season_state"]
    assert st["phase"] in ("preseason", "early", "established")
    assert st["system_confidence"] in ("Low", "Medium", "High")
    assert "downweighting" in st


def test_stats_season_is_last_season_until_a_match_finishes(db):
    """Trước vòng 1, tổng cả-mùa của FPL vẫn là số của mùa trước.

    Đây là điều kiện để quy đổi BPS: nếu hàm này trả về mùa đang chơi thì engine
    sẽ dùng BPS kiếm theo luật cũ như thể nó đã theo luật mới.
    """
    from sqlalchemy import select

    from app.models import Fixture
    from app.services.season_state import stats_season

    # không sửa dữ liệu chung: truyền tên mùa vào thẳng
    fixtures = db.scalars(select(Fixture)).all()
    original = [(f.id, f.finished, f.finished_provisional) for f in fixtures]
    try:
        for f in fixtures:
            f.finished = False
            f.finished_provisional = False
        db.flush()
        assert stats_season(db, "2026/27") == "2025/26"

        if fixtures:
            fixtures[0].finished = True
            db.flush()
            assert stats_season(db, "2026/27") == "2026/27"
    finally:
        by_id = {f.id: f for f in fixtures}
        for fid, was, was_prov in original:
            by_id[fid].finished = was
            by_id[fid].finished_provisional = was_prov
        db.flush()
        db.rollback()


def test_a_played_match_counts_before_fpl_data_checks_the_gameweek(db):
    """Trận đã đá xong nhưng FPL chưa data-check vẫn phải được tính.

    Hồi quy 2026-08-22 (vòng 1 mùa 2026/27): trận Arsenal 3-0 tối 21/08 có
    `minutes=90`, tỷ số cuối và bonus 3/2/1 đã chốt, nhưng FPL vẫn để
    `finished=false` / `finished_provisional=true` cho tới khi cả vòng được
    data-check — ba ngày sau. Trong ba ngày đó trang dán nhãn PRE-SEASON cho một
    mùa đang đá, và `stats_season()` quy đổi BPS thêm một lần thừa vì FPL đã
    reset thống kê ngay tại hạn vòng 1.
    """
    from sqlalchemy import select

    from app.models import Fixture
    from app.services.season_state import season_state, stats_season

    fixtures = db.scalars(select(Fixture)).all()
    assert fixtures, "demo DB phải có fixture thì phép thử này mới nói lên điều gì"
    original = [(f.id, f.finished, f.finished_provisional) for f in fixtures]
    try:
        for f in fixtures:
            f.finished = False
            f.finished_provisional = False
        db.flush()
        assert season_state(db)["phase"] == "preseason"
        assert stats_season(db, "2026/27") == "2025/26"

        # chỉ bật cờ tạm — cờ `finished` vẫn false, đúng như FPL đang trả về
        fixtures[0].finished_provisional = True
        db.flush()
        assert fixtures[0].finished is False
        assert season_state(db)["matches_played"] == 1
        assert season_state(db)["phase"] == "early"
        assert stats_season(db, "2026/27") == "2026/27"
    finally:
        by_id = {f.id: f for f in fixtures}
        for fid, was, was_prov in original:
            by_id[fid].finished = was
            by_id[fid].finished_provisional = was_prov
        db.flush()
        db.rollback()


def test_null_provisional_on_old_rows_does_not_erase_a_finished_match(db):
    """NULL = "chưa đồng bộ trường này", không phải "chưa đá".

    Cột `finished_provisional` được thêm vào một cơ sở dữ liệu đang chạy, nên mọi
    hàng ghi trước đó mang NULL. Nếu phía đọc chỉ nhìn cột mới thì toàn bộ lịch sử
    trận đấu biến mất trong đúng một lần deploy.
    """
    from sqlalchemy import select

    from app.models import Fixture
    from app.services.season_state import season_state

    fixtures = db.scalars(select(Fixture)).all()
    original = [(f.id, f.finished, f.finished_provisional) for f in fixtures]
    try:
        for f in fixtures:
            f.finished = False
            f.finished_provisional = None
        fixtures[0].finished = True
        db.flush()
        assert season_state(db)["matches_played"] == 1
    finally:
        by_id = {f.id: f for f in fixtures}
        for fid, was, was_prov in original:
            by_id[fid].finished = was
            by_id[fid].finished_provisional = was_prov
        db.flush()
        db.rollback()


def test_not_played_condition_keeps_null_rows(db):
    """`~played_condition()` không dùng được: NOT (false OR NULL) ra NULL.

    Nếu viết phủ định bằng toán tử `~`, mọi trận CHƯA đá thuộc hàng cũ (nơi
    `finished_provisional` còn NULL) sẽ rơi khỏi truy vấn mà không báo lỗi — và
    `sync_odds` sẽ lặng lẽ không tìm được trận nào để gắn kèo nhà cái.
    """
    from sqlalchemy import func, select

    from app.models import Fixture
    from app.services.season_state import not_played_condition, played_condition

    fixtures = db.scalars(select(Fixture)).all()
    original = [(f.id, f.finished, f.finished_provisional) for f in fixtures]
    try:
        for f in fixtures:
            f.finished = False
            f.finished_provisional = None      # hàng ghi trước khi có cột này
        fixtures[0].finished_provisional = True
        db.flush()

        played = db.scalar(
            select(func.count()).select_from(Fixture).where(played_condition())
        )
        not_played = db.scalar(
            select(func.count()).select_from(Fixture).where(not_played_condition())
        )
        assert played == 1
        assert not_played == len(fixtures) - 1   # NULL được đọc là "chưa đá"
        assert played + not_played == len(fixtures)
    finally:
        by_id = {f.id: f for f in fixtures}
        for fid, was, was_prov in original:
            by_id[fid].finished = was
            by_id[fid].finished_provisional = was_prov
        db.flush()
        db.rollback()


def test_last_completed_gw_waits_for_the_whole_round(db):
    """Vòng đã đá 9/10 trận là vòng CHƯA xong.

    Vòng 1 mùa 2026/27 trải từ 21/08 đến 24/08. Nếu trường này trả 1 ngay khi trận
    đầu tiên kết thúc, nó nói rằng số liệu vòng 1 đã chốt trong khi còn trận chưa
    đá — đúng loại nhầm lẫn mà `matches_played` (đo tiến độ) sinh ra khi bị dùng
    thay cho "vòng nào đã đóng".
    """
    from sqlalchemy import select

    from app.models import Fixture
    from app.services.season_state import last_completed_gameweek, season_state

    fixtures = db.scalars(select(Fixture).where(Fixture.event.isnot(None))).all()
    original = [(f.id, f.finished, f.finished_provisional) for f in fixtures]
    gw = min(f.event for f in fixtures)
    in_gw = [f for f in fixtures if f.event == gw]
    assert len(in_gw) >= 2, "cần ít nhất 2 trận trong một vòng thì phép thử mới nói gì"
    try:
        for f in fixtures:
            f.finished, f.finished_provisional = False, False
        for f in in_gw[:-1]:                  # đá hết trừ một trận
            f.finished_provisional = True
        db.flush()
        assert last_completed_gameweek(db) is None
        assert season_state(db)["matches_played"] == len(in_gw) - 1

        in_gw[-1].finished_provisional = True   # trận cuối cùng khép vòng lại
        db.flush()
        assert last_completed_gameweek(db) == gw
        assert season_state(db)["last_completed_gw"] == gw
    finally:
        by_id = {f.id: f for f in fixtures}
        for fid, was, prov in original:
            by_id[fid].finished, by_id[fid].finished_provisional = was, prov
        db.flush()
        db.rollback()

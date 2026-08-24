"""Ingestion tests — focus on idempotency (jobs re-run on a schedule)."""
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models import Fixture, MarketOdds, SourceFetchLog, Team


def _stub_provider(monkeypatch, db, lam=(2.0, 1.0)):
    """Point sync_odds at a fake bookmaker feed for one real unfinished fixture."""
    import app.providers.probability as prob

    fx = db.scalars(select(Fixture).where(Fixture.finished.is_(False))).first()
    teams = {t.id: t.name for t in db.scalars(select(Team)).all()}

    match = prob.MarketMatch(
        home_name=teams[fx.team_h], away_name=teams[fx.team_a],
        commence_time="2026-08-21T17:30:00Z",
        lam_home=lam[0], lam_away=lam[1],
        p_home=0.6, p_draw=0.2, p_away=0.2, total_goals=sum(lam),
        n_bookmakers=12,
    )

    class Fake(prob.ProbabilityProvider):
        name = "odds_api"

        def get_matches(self):
            return [match]

    monkeypatch.setattr(prob, "get_probability_provider", lambda: Fake())
    return fx


def test_sync_odds_is_idempotent(db, monkeypatch):
    """Re-running the scheduled sync must update rows, not duplicate/crash.

    Regression: db.merge() matched on the primary key (id) instead of
    fixture_id, so every run after the first hit a UniqueViolation.
    """
    from app.ingestion.fpl_sync import sync_odds

    fx = _stub_provider(monkeypatch, db, lam=(2.0, 1.0))

    first = sync_odds(db)
    assert first["matched"] == 1
    count_after_first = db.scalar(select(func.count()).select_from(MarketOdds))

    # second run — same fixture, refreshed prices
    _stub_provider(monkeypatch, db, lam=(2.6, 0.7))
    second = sync_odds(db)
    assert second["matched"] == 1
    assert db.scalar(select(func.count()).select_from(MarketOdds)) == count_after_first

    row = db.scalar(select(MarketOdds).where(MarketOdds.fixture_id == fx.id))
    assert row.lam_home == 2.6      # updated in place
    assert row.lam_away == 0.7


def test_import_same_team_id_twice(db):
    """Importing the same FPL Team ID again must update, not duplicate/crash.

    Regression: db.merge(UserProfile(...)) matched on the primary key (id), so
    the second import violated the unique constraint on fpl_team_id and the
    Long-term Planner returned a 502.
    """
    from app.ingestion.team_import import upsert_profile
    from app.models import UserProfile

    p1 = upsert_profile(db, 454370, player_name="Trung Đinh", team_name="Donald Trung",
                        bank=0, team_value=1000, free_transfers=1)
    assert p1.fpl_team_id == 454370

    p2 = upsert_profile(db, 454370, player_name="Trung Đinh", team_name="Donald Trung",
                        bank=15, team_value=1012, free_transfers=2)
    assert p2.id == p1.id                 # same row, updated in place
    assert p2.bank == 15
    assert p2.free_transfers == 2
    assert db.scalar(
        select(func.count()).select_from(UserProfile).where(
            UserProfile.fpl_team_id == 454370
        )
    ) == 1


def test_sync_odds_without_key_is_safe(db, monkeypatch):
    """No API key => log it, change nothing, never raise."""
    import app.providers.probability as prob

    from app.ingestion.fpl_sync import sync_odds

    monkeypatch.setattr(prob, "get_probability_provider", lambda: None)
    before = db.scalar(select(func.count()).select_from(MarketOdds))
    res = sync_odds(db)
    assert res["enabled"] is False
    assert db.scalar(select(func.count()).select_from(MarketOdds)) == before


# ------------------------------------------- thống kê thật theo từng trận ----
class _FakeLive:
    """Client giả trả về đúng hình dạng của /event/{gw}/live/.

    Điểm cần bắt chước cho đúng: FPL phát `explain` cho MỌI cầu thủ có trận trong
    vòng đó, kể cả khi câu lạc bộ chưa ra sân (đo vòng 1 mùa 2026/27: 609/609
    phần tử có mục explain lúc mới xong 9/10 trận).
    """

    def __init__(self, rows):
        self.rows = rows            # [(player_id, fixture_id, minutes)]
        self.calls = 0

    def event_live(self, gameweek):
        self.calls += 1
        return {"elements": [
            {
                "id": pid,
                "stats": {"minutes": m, "total_points": 2 if m else 0,
                          "starts": 1 if m >= 60 else 0, "bps": m // 3,
                          "expected_goals": "0.12", "expected_assists": "0.03"},
                "explain": [{"fixture": fid, "stats": []}],
            }
            for pid, fid, m in self.rows
        ]}

    def element_summaries(self, ids):
        return {}


@contextmanager
def _restorable(db):
    """Chụp và trả nguyên trạng những bảng hai phép thử dưới đây động vào.

    `sync_player_gameweek_stats` có `db.commit()` bên trong, nên nó ghi thật vào
    cơ sở dữ liệu dùng chung cho cả phiên test — `db.rollback()` ở cuối là vô
    nghĩa, và các phép thử chạy sau sẽ đọc phải lịch thi đấu đã bị bẻ cờ.
    """
    from app.models import Gameweek, PlayerGameweekStat, SourceFetchLog

    cols = [c.name for c in PlayerGameweekStat.__table__.columns]
    max_log_id = db.scalar(select(func.max(SourceFetchLog.id))) or 0
    snapshot = {
        "fixtures": [(f.id, f.finished, f.finished_provisional)
                     for f in db.scalars(select(Fixture)).all()],
        "gameweeks": [(g.id, g.data_checked)
                      for g in db.scalars(select(Gameweek)).all()],
        "stats": [{c: getattr(r, c) for c in cols}
                  for r in db.scalars(select(PlayerGameweekStat)).all()],
    }
    try:
        yield
    finally:
        db.rollback()
        db.query(PlayerGameweekStat).delete()
        db.flush()
        for row in snapshot["stats"]:
            db.add(PlayerGameweekStat(**row))
        fx = {f.id: f for f in db.scalars(select(Fixture)).all()}
        for fid, fin, prov in snapshot["fixtures"]:
            fx[fid].finished, fx[fid].finished_provisional = fin, prov
        gws = {g.id: g for g in db.scalars(select(Gameweek)).all()}
        for gid, checked in snapshot["gameweeks"]:
            gws[gid].data_checked = checked
        # `_log` cũng được commit theo — để lại thì `source_freshness` thấy dữ liệu
        # vừa mới đồng bộ, và phép thử về độ cũ của nguồn sẽ hỏng.
        db.query(SourceFetchLog).filter(SourceFetchLog.id > max_log_id).delete()
        db.commit()


def _one_played_one_not(db):
    """Trong cùng một vòng: một trận bật cờ tạm (đã đá), một trận chưa đá."""
    fixtures = db.scalars(select(Fixture).where(Fixture.event.isnot(None))).all()
    gw = fixtures[0].event
    same_gw = [f for f in fixtures if f.event == gw][:2]
    assert len(same_gw) == 2
    for f in db.scalars(select(Fixture)).all():
        f.finished, f.finished_provisional = False, False
    same_gw[0].finished_provisional = True      # đã đá, FPL chưa data-check
    db.query(__import__("app.models", fromlist=["x"]).PlayerGameweekStat).delete()
    db.flush()
    return gw, same_gw[0], same_gw[1]


def test_match_stats_skip_fixtures_that_have_not_been_played(db):
    """`explain` có mặt cả khi CLB chưa ra sân — ghi thẳng là bịa bằng chứng.

    Nếu không lọc theo trận đã đá, "0 phút vì trận chưa diễn ra" sẽ vào bảng y hệt
    "0 phút vì bị gạt khỏi đội hình", và mô hình phút thi đấu đọc nó như bằng
    chứng xoay tua.
    """
    from app.ingestion.fpl_sync import sync_player_gameweek_stats
    from app.models import Player, PlayerGameweekStat

    with _restorable(db):
        _gw, played_fx, unplayed_fx = _one_played_one_not(db)
        pids = [p.id for p in db.scalars(select(Player).limit(2)).all()]

        out = sync_player_gameweek_stats(db, _FakeLive([
            (pids[0], played_fx.id, 0), (pids[1], unplayed_fx.id, 0),
        ]))

        rows = db.scalars(select(PlayerGameweekStat)).all()
        assert out["rows"] == 1
        assert [r.fixture_id for r in rows] == [played_fx.id]
        assert rows[0].minutes == 0             # 0 phút THẬT vẫn phải được ghi
        assert rows[0].was_home is not None     # suy ra từ bảng fixtures
        assert rows[0].opponent_team is not None


def test_match_stats_sync_is_idempotent(db):
    """Chạy lại 4 lần một ngày: lần hai phải CẬP NHẬT, không nhân đôi.

    `db.merge()` khớp theo khoá chính (`id`) vốn là None với đối tượng mới, nên
    dùng nó ở đây sẽ INSERT mọi lần và vi phạm unique (player_id, fixture_id).
    """
    from app.ingestion.fpl_sync import sync_player_gameweek_stats
    from app.models import Gameweek, Player, PlayerGameweekStat

    with _restorable(db):
        gw, played_fx, _ = _one_played_one_not(db)
        pid = db.scalars(select(Player)).first().id
        gw_row = db.get(Gameweek, gw)
        gw_row.data_checked = False             # vòng đang diễn ra: số còn đổi
        db.flush()

        first = sync_player_gameweek_stats(db, _FakeLive([(pid, played_fx.id, 45)]))
        second = sync_player_gameweek_stats(db, _FakeLive([(pid, played_fx.id, 90)]))

        rows = db.scalars(select(PlayerGameweekStat)).all()
        assert first["rows"] == 1 and second["rows"] == 0
        assert len(rows) == 1
        assert rows[0].minutes == 90            # cập nhật, không phải dòng thứ hai

        # Vòng đã data-check thì số không đổi nữa -> không tốn lệnh nào.
        gw_row.data_checked = True
        db.flush()
        client = _FakeLive([(pid, played_fx.id, 90)])
        assert sync_player_gameweek_stats(db, client)["gameweeks"] == 0
        assert client.calls == 0


def test_history_sync_asks_each_player_once(db):
    """Nền lịch sử là việc chạy MỘT lần, không phải mỗi 6 tiếng.

    Mùa đã khép lại thì `history_past` không đổi nữa. Nếu đường này không nhớ đã
    hỏi ai, nó sẽ ngốn ~609 lệnh element-summary mỗi lần đồng bộ, bốn lần một
    ngày — đúng cái chi phí mà việc chuyển sang `/event/{gw}/live/` đã tránh được.

    Và dấu "đã hỏi" phải TÁCH khỏi "có dòng lịch sử": tân binh chưa từng đá Ngoại
    hạng trả về rỗng, đó là câu trả lời hợp lệ chứ không phải một lần hỏi hỏng.
    """
    from app.ingestion.fpl_sync import sync_player_history_past
    from app.models import Player, PlayerSeasonHistory

    class _FakeSummaries:
        def __init__(self, payload):
            self.payload = payload
            self.asked: list[list[int]] = []

        def element_summaries(self, ids):
            self.asked.append(list(ids))
            return {pid: self.payload.get(pid, {"history_past": []}) for pid in ids}

    players = db.scalars(select(Player).limit(2)).all()
    veteran, rookie = players[0].id, players[1].id
    saved = [(p.id, p.history_fetched_at) for p in db.scalars(select(Player)).all()]
    try:
        # Chỉ để đúng hai người ở trạng thái "chưa hỏi": DB demo có hàng trăm cầu
        # thủ, nên nếu để tất cả là None thì lô thứ hai sẽ nhặt hai người KHÁC và
        # phép thử đo nhầm thứ nó định đo.
        marker = datetime(2026, 8, 1, tzinfo=timezone.utc)
        for p in db.scalars(select(Player)).all():
            p.history_fetched_at = marker
        for pid in (veteran, rookie):
            db.get(Player, pid).history_fetched_at = None
        db.query(PlayerSeasonHistory).delete()
        db.flush()

        client = _FakeSummaries({veteran: {"history_past": [
            {"season_name": "2025/26", "minutes": 3000, "starts": 34,
             "expected_goals": "18.5", "expected_assists": "6.0"},
        ]}})
        first = sync_player_history_past(db, client, batch=2)
        assert first["fetched"] == 2 and first["rows"] == 1

        # lần hai: cả hai đã được đánh dấu, kể cả người KHÔNG có mùa nào
        second = sync_player_history_past(db, client, batch=2)
        assert second["fetched"] == 0
        assert len(client.asked) == 1          # không hỏi lại lần nữa

        rows = db.scalars(select(PlayerSeasonHistory)).all()
        assert [r.player_id for r in rows] == [veteran]
        assert rows[0].minutes == 3000 and rows[0].starts == 34
        assert db.get(Player, rookie).history_fetched_at is not None
    finally:
        db.rollback()
        db.query(PlayerSeasonHistory).delete()
        by_id = {p.id: p for p in db.scalars(select(Player)).all()}
        for pid, when in saved:
            by_id[pid].history_fetched_at = when
        db.query(SourceFetchLog).filter(
            SourceFetchLog.source_name == "FPL element history"
        ).delete()
        db.commit()

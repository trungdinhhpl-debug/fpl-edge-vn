"""Ingestion tests — focus on idempotency (jobs re-run on a schedule)."""
from sqlalchemy import func, select

from app.models import Fixture, MarketOdds, Team


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


def test_sync_odds_without_key_is_safe(db, monkeypatch):
    """No API key => log it, change nothing, never raise."""
    import app.providers.probability as prob

    from app.ingestion.fpl_sync import sync_odds

    monkeypatch.setattr(prob, "get_probability_provider", lambda: None)
    before = db.scalar(select(func.count()).select_from(MarketOdds))
    res = sync_odds(db)
    assert res["enabled"] is False
    assert db.scalar(select(func.count()).select_from(MarketOdds)) == before

"""News centre tests — provenance, the xMins counterfactual, and honesty about
what we do NOT have a feed for."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import InjuryReport, Player
from app.services.news import news_centre, news_feed
from app.services.news_tiers import (
    BY_KEY, TIERS, classify_origin, recommend, xmins_before_after,
)


# ----------------------------------------------------------------- tiers ----
def test_tier_order_and_unknown_source_defaults_down():
    """An unrecognised feed must not inherit trust it has not earned."""
    assert [t.key for t in TIERS][:2] == ["club_official", "manager_presser"]
    assert [t.rank for t in TIERS] == sorted(t.rank for t in TIERS)

    assert classify_origin("FPL Official") == "club_official"
    assert classify_origin("fpl official") == "club_official"     # case-insensitive
    assert classify_origin("FPL Edge model") == "model_inference"
    assert classify_origin("Some Bloke On Twitter") == "rumour"
    assert classify_origin(None) == "rumour"


def test_coverage_is_declared_not_implied(db):
    """Tiers with no feed must say so, not just render empty."""
    centre = news_centre(db, limit=5)
    by_key = {t["key"]: t for t in centre["tiers"]}
    assert len(by_key) == len(TIERS)

    assert by_key["club_official"]["configured"] is True
    assert by_key["model_inference"]["configured"] is True
    for key in ("manager_presser", "club_reporter", "predicted_lineup", "rumour"):
        assert by_key[key]["configured"] is False
        assert by_key[key]["needs"], f"{key} must say what would fill it"
        assert by_key[key]["count"] == 0


# ------------------------------------------------------ xMins counterfactual --
def test_xmins_before_after_isolates_the_news():
    """Only availability moves between the two runs, so the gap is the news."""
    common = dict(element_type=3, season_starts=30, season_minutes=2600,
                  team_matches_played=10, recent_minutes=[90, 90, 85, 90, 88])

    before, after = xmins_before_after(status="i", chance_of_playing=0, **common)
    assert before > 60, "a regular starter should project high when fit"
    assert after == 0.0, "ruled out means no minutes"
    assert before - after > 60

    # a fit player has nothing to explain: both sides must agree exactly
    b2, a2 = xmins_before_after(status="a", chance_of_playing=None, **common)
    assert b2 == a2

    # a 75% doubt sits between the two
    b3, a3 = xmins_before_after(status="d", chance_of_playing=75, **common)
    assert 0 < a3 < b3


def test_recommendation_scales_with_the_drop_not_the_level():
    """A fringe player losing 20' is noise; a starter losing 20' is a story."""
    assert recommend(80, 0, "i", 10)["to"] == "Bán"
    assert recommend(80, 30, "d", 10)["to"] == "Bán"            # -62%
    assert recommend(80, 55, "d", 10)["to"] == "Cân nhắc bán"   # -31%
    assert recommend(80, 68, "d", 10)["to"] == "Theo dõi"       # -15%
    assert recommend(80, 76, "d", 10)["to"] == "Giữ"            # -5%
    assert recommend(80, 76, "d", 10)["label"] == "Giữ"

    # ownership is context on the call, never the trigger for it
    high_own = recommend(80, 30, "d", 40)
    assert "40%" in high_own["why"]
    assert recommend(80, 76, "d", 40)["to"] == "Giữ"


# ------------------------------------------------------------- feed shape ----
def test_every_item_carries_the_promised_fields(db):
    items = news_centre(db, limit=20)["items"]
    assert items, "demo data should contain injury news"
    for it in items:
        for field in ("tier", "tier_label", "source_name", "published_at",
                      "fetched_at", "affected_gameweek", "xmins_before",
                      "xmins_after", "independent_sources", "action"):
            assert field in it, f"missing {field}"
        assert it["action"]["label"]
        assert it["independent_sources"] == len(it["independent_source_names"])


def test_model_inferred_items_do_not_invent_a_before(db):
    """No event happened, so there is no 'before' — it must be null, not equal."""
    items = news_centre(db, tier="model_inference", limit=20)["items"]
    for it in items:
        assert it["tier"] == "model_inference"
        assert it["xmins_before"] is None
        assert it["xmins_delta"] is None
        assert it["xmins_after"] is not None
        assert it["independent_sources"] == 0


def test_repeat_fetch_collapses_but_a_development_becomes_history(db):
    """Re-syncing an unchanged story must not spawn a second card.

    Regression: ingestion inserted a row every run, so one suspension showed up
    three times in the feed and inflated the per-tier counts.
    """
    pid = db.scalars(select(Player).where(Player.status == "a")).first().id
    now = datetime.now(timezone.utc)
    made = []
    try:
        for offset, news, chance in (
            (2, "Knock - 75% chance of playing", 75),   # oldest
            (1, "Knock - 75% chance of playing", 75),   # identical re-fetch
            (0, "Hamstring injury - Unknown return date", 0),  # a real change
        ):
            r = InjuryReport(
                player_id=pid, status="d" if chance else "i",
                chance_of_playing=chance, impact="High", confirmed=True,
                news=news, source_name="FPL Official",
                published_at=now - timedelta(days=offset + 1),
                fetched_at=now - timedelta(days=offset),
            )
            db.add(r)
            made.append(r)
        db.flush()

        mine = [i for i in news_feed(db, limit=500) if i["player_id"] == pid]
        assert len(mine) == 1, "one card per player, showing the current state"
        card = mine[0]
        assert card["news"] == "Hamstring injury - Unknown return date"
        # the identical re-fetch is dropped, the genuine earlier step is kept
        assert len(card["history"]) == 1
        assert card["history"][0]["chance_of_playing"] == 75
    finally:
        for r in made:
            db.delete(r)
        db.flush()

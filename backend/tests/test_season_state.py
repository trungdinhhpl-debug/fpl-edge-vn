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


def test_empty_downweight_lists_read_as_unknown_not_as_none(db):
    """An unfilled list means nobody told us, never 'nothing changed'."""
    dw = season_state(db)["downweighting"]
    assert dw["configured"] is False
    assert "CHƯA AI KHAI" in dw["note"]


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

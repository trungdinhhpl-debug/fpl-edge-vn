"""Expert-signal tests.

The failure this page must not have is treating volume as agreement, and the
harm it must not cause is publishing performance claims about real, named people
that nobody measured. Both are asserted here.
"""
import pytest
from sqlalchemy import select

from app.models import ExpertSource, ExpertTrackRecord
from app.services.experts import (
    MIN_SCORED_SAMPLE, analyse_player, expert_consensus, source_weight,
    track_records,
)


def _sig(source, signal_type, weight, origin_ref=None):
    return {"source": source, "signal_type": signal_type, "weight": weight,
            "origin_ref": origin_ref}


# ------------------------------------------------------- echo collapsing ----
def test_echoes_of_one_statement_count_once():
    """8 posts, 3 origins -> the two relays do not multiply the evidence."""
    signals = [
        _sig("A", "start", 0.8, "presser:ARS:gw3"),
        _sig("B", "start", 0.6, "presser:ARS:gw3"),
        _sig("C", "start", 0.5, "presser:ARS:gw3"),
        _sig("D", "start", 0.7, "presser:ARS:gw3"),
        _sig("E", "start", 0.4, "presser:ARS:gw3"),
        _sig("F", "start", 0.5, "scout-report:gw3"),
        _sig("G", "start", 0.5, "scout-report:gw3"),
        _sig("H", "avoid", 0.6),                       # an original dissent
    ]
    a = analyse_player(signals)

    assert a["posts"] == 8
    assert a["independent_sources"] == 3        # presser, scout report, dissent
    assert a["echo_accounts"] == 5
    assert "presser:ARS:gw3" in a["echoed_origins"]

    # naive counting says 7/8 = 87.5% agree; the evidence says much less
    assert a["naive_consensus_pct"] == 87.5
    assert a["consensus_pct"] < a["naive_consensus_pct"]

    # the collapsed group votes once, carrying its strongest member's weight
    presser = next(v for v in a["votes"] if v["origin_ref"] == "presser:ARS:gw3")
    assert presser["n_posts"] == 5
    assert presser["weight"] == 0.8
    assert presser["lead_source"] == "A"


def test_original_takes_are_never_merged():
    """Two independent takes with no upstream must stay two sources."""
    a = analyse_player([_sig("A", "buy", 0.7), _sig("B", "buy", 0.6)])
    assert a["independent_sources"] == 2
    assert a["echo_accounts"] == 0
    assert a["consensus_pct"] == 100.0


def test_dissent_is_surfaced_not_averaged_away():
    a = analyse_player([
        _sig("A", "start", 0.8), _sig("B", "start", 0.8), _sig("C", "sell", 0.6),
    ])
    assert a["has_dissent"] is True
    assert [d["lead_source"] for d in a["dissent"]] == ["C"]
    assert 0 < a["consensus_pct"] < 100


def test_hold_is_not_counted_as_agreement():
    """`hold` is neither for nor against and must not inflate consensus."""
    a = analyse_player([_sig("A", "start", 0.8), _sig("B", "hold", 0.8)])
    assert a["consensus_pct"] == 100.0        # only the decided vote counts
    directions = {v["direction"] for v in a["votes"]}
    assert "neutral" in directions


# ------------------------------------------------- track record integrity ----
def test_accuracy_is_absent_until_it_is_earned(db):
    """A real named person must never carry an accuracy figure we never measured."""
    payload = expert_consensus(db)
    for s in payload["sources"]:
        assert s["verified_track_record"] is False
        assert s["fpl_rank_verified"] is None
        for e in s["expertise"]:
            assert e["accuracy"] is None
            assert "chưa đủ dữ liệu" in e["status"]


def test_seeded_sources_carry_no_historical_accuracy(db):
    """Regression: the old seed shipped 0.72–0.80 accuracy for named analysts."""
    for s in db.scalars(select(ExpertSource)).all():
        assert s.historical_accuracy == 0.0, s.name
        assert s.verified_track_record is False, s.name


def test_accuracy_appears_only_past_the_sample_floor(db):
    src = db.scalars(select(ExpertSource)).first()
    made = []
    try:
        # one short of the floor: still no number
        for i in range(MIN_SCORED_SAMPLE - 1):
            r = ExpertTrackRecord(source_id=src.id, domain="lineup", gameweek=1,
                                  claim=f"c{i}", correct=True)
            db.add(r)
            made.append(r)
        db.flush()
        assert track_records(db)[src.id]["lineup"]["accuracy"] is None

        r = ExpertTrackRecord(source_id=src.id, domain="lineup", gameweek=1,
                              claim="last", correct=False)
        db.add(r)
        made.append(r)
        db.flush()
        cell = track_records(db)[src.id]["lineup"]
        assert cell["resolved"] == MIN_SCORED_SAMPLE
        assert cell["accuracy"] == pytest.approx(0.9)

        # pending predictions never count as either right or wrong
        p = ExpertTrackRecord(source_id=src.id, domain="lineup", gameweek=2,
                              claim="pending", correct=None)
        db.add(p)
        made.append(p)
        db.flush()
        cell = track_records(db)[src.id]["lineup"]
        assert cell["resolved"] == MIN_SCORED_SAMPLE
        assert cell["pending"] == 1
    finally:
        for r in made:
            db.delete(r)
        db.flush()


def test_verified_flag_is_derived_not_trusted_from_the_row(db):
    """A stale `verified=True` in the database must not reach the page.

    Live databases still carry that flag for real named people from the previous
    version, so the payload computes it from scored evidence instead of reading
    the column.
    """
    src = db.scalar(select(ExpertSource).where(ExpertSource.name == "Ben Crellin"))
    original = src.verified_track_record
    src.verified_track_record = True          # simulate the stale production row
    db.flush()
    try:
        payload = expert_consensus(db)
        row = next(s for s in payload["sources"] if s["name"] == "Ben Crellin")
        assert row["verified_track_record"] is False, (
            "verified must follow the evidence, not a leftover flag"
        )
    finally:
        src.verified_track_record = original
        db.flush()


def test_unmeasured_accuracy_does_not_zero_signal_scores():
    """Regression: 0.0 accuracy multiplied every signal score down to zero.

    "Not measured" and "always wrong" must not produce the same number.
    """
    from app.providers.expert_provider import compute_signal_score

    unmeasured = compute_signal_score(0.75, 0.0, 1.0, 0.85, 8)
    assert unmeasured > 0
    measured_bad = compute_signal_score(0.75, 0.1, 1.0, 0.85, 8)
    assert measured_bad < unmeasured


def test_weight_uses_domain_not_volume(db):
    """Opining outside your declared domain counts for less."""
    src = db.scalar(select(ExpertSource).where(ExpertSource.name == "Fantasy Football Scout"))
    assert src is not None
    in_domain = source_weight(src, "lineup", None)
    out_domain = source_weight(src, "chip_planning", None)
    assert in_domain > out_domain


def test_demo_signals_are_not_attributed_to_real_people(db):
    """Invented statements must never carry a real analyst's name."""
    payload = expert_consensus(db)
    real_names = {"Ben Crellin", "Lateriser (Pranil Sheth)",
                  "Fantasy Football Scout", "FPL Review", "r/FantasyPL"}
    for p in payload["players"]:
        for s in p["signals"]:
            if s["is_mock"]:
                assert s["source"] not in real_names, (
                    f"demo signal attributed to real source {s['source']}"
                )

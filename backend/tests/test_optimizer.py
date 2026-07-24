"""Optimizer legality + behaviour tests (spec §24 acceptance criteria)."""
from sqlalchemy import select

from app.models import Player
from app.optimizer.constraints import (
    MAX_PER_CLUB,
    SQUAD_BY_TYPE,
    validate_squad,
    validate_xi,
)
from app.services import team as team_svc


def _players(db):
    return {p.id: p for p in db.scalars(select(Player)).all()}


def test_free_hit_is_a_legal_squad(db):
    players = _players(db)
    res = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep")
    ids = res["squad_ids"]
    assert len(ids) == 15
    v = validate_squad(
        ids, lambda i: players[i].element_type,
        lambda i: players[i].now_cost, lambda i: players[i].team_id,
    )
    assert v.valid, v.errors
    # budget respected
    assert sum(players[i].now_cost for i in ids) <= 1000


def test_free_hit_starting_xi_valid(db):
    players = _players(db)
    res = team_svc.optimize_free_hit(db, budget=1000, mode="balanced")
    xi_types = [players[s["id"]].element_type for s in res["starting"]]
    assert validate_xi(xi_types)
    assert res["captain"] in [s["id"] for s in res["starting"]]


def test_max_per_club_respected(db):
    players = _players(db)
    res = team_svc.optimize_free_hit(db, budget=1000, mode="aggressive")
    clubs = {}
    for i in res["squad_ids"]:
        clubs[players[i].team_id] = clubs.get(players[i].team_id, 0) + 1
    assert max(clubs.values()) <= MAX_PER_CLUB


def test_next_gw_no_free_transfer_no_hit(db):
    """With a fresh optimal squad and 1 FT, a 0-transfer solution has no hit."""
    fh = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep")
    squad = fh["squad_ids"]
    res = team_svc.optimize_next_gw(db, squad, bank=0, free_transfers=1, max_transfers=2)
    assert res["hits"] == 0 or res["n_transfers"] <= res["hits"] + 1


def test_long_term_returns_three_plans(db):
    fh = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep")
    squad = fh["squad_ids"]
    res = team_svc.optimize_long_term(db, squad, bank=0, free_transfers=1, horizon=4)
    assert set(res["plans"].keys()) == {"safe", "balanced", "aggressive"}
    for plan in res["plans"].values():
        assert plan["status"] in ("Optimal", "Not Solved", "Feasible")
        assert "net_xp" in plan

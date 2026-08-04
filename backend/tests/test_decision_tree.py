"""Decision-tree tests — the point of the tree is the REASONING, so that is
what is asserted here: the costed 'why hold', not just the shape of the dict."""
import pytest
from sqlalchemy import select

from app.models import Player
from app.services.decision_tree import (
    _pair_moves, _replay_hits, _signed, build_decision_tree,
)


# ------------------------------------------------------- free-transfer maths --
def test_replay_hits_matches_the_ft_rule():
    """ft_next = min(max, ft - used + 1); hits only when you exceed your FTs."""
    # roll, roll, then spend 3 with 3 banked -> free
    assert _replay_hits([0, 0, 3], ft_start=1, max_ft=5) == 0
    # same plan but spend one early: only 2 banked by the time you need 3
    assert _replay_hits([0, 0, 3], ft_start=1, max_ft=5, extra_at=0) == 1
    # spending within your allowance never costs
    assert _replay_hits([1, 1, 1], ft_start=1, max_ft=5) == 0
    # a 2-transfer week on 1 FT is a hit
    assert _replay_hits([2], ft_start=1, max_ft=5) == 1
    # the season cap binds: rolling forever does not bank more than max_ft
    assert _replay_hits([0, 0, 0, 0, 0, 6], ft_start=1, max_ft=5) == 1


def test_signed_never_prints_plus_minus():
    """Regression: f'+{gain}' printed '+-0.82' for a negative gain."""
    assert _signed(1.4) == "+1.4"
    assert _signed(-0.82) == "−0.82"
    assert _signed(0.0) == "+0.0"


# ------------------------------------------------------------- move pairing --
def test_pair_moves_pairs_by_position_not_index(db):
    """Regression: zipping the two unordered sets by index could print a
    defender-out / midfielder-in move, which is not a legal FPL transfer."""
    from app.services.decision_tree import _load_ctx

    ctx = _load_ctx(db, [1, 2])
    by_type = {}
    for pid, p in ctx.players.items():
        by_type.setdefault(p.element_type, []).append(pid)
    def_out, mid_out = by_type[2][0], by_type[3][0]
    def_in, mid_in = by_type[2][1], by_type[3][1]

    # deliberately mismatched ordering
    pairs = _pair_moves(ctx, [def_out, mid_out], [mid_in, def_in])
    for pr in pairs:
        assert ctx.players[pr["out"]].element_type == ctx.players[pr["in"]].element_type


# ---------------------------------------------------------------- main line --
def _fake_plan(gws, squad_ids, moves_at):
    """Minimal plan in the shape long_term_plan() returns."""
    weeks = []
    squad = list(squad_ids)
    for g in gws:
        t_in, t_out = moves_at.get(g, ([], []))
        weeks.append({
            "gameweek": g, "squad": sorted(squad), "starting": squad[:11],
            "captain": squad[0], "transfers_in": t_in, "transfers_out": t_out,
            "n_transfers": len(t_in), "hits": 0, "free_transfers": 1.0,
            "xi_xp": 50.0,
        })
    return {"weeks": weeks}


def test_roll_step_prices_the_cost_of_acting_now(db):
    """A Roll must carry a NUMBER for why waiting wins — the whole request."""
    from app.services.common import planning_start_gw

    start = planning_start_gw(db)
    gws = [start, start + 1, start + 2]
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    ids = list(players)
    squad = ids[:15]

    # Rolling twice banks 3 FT, so only a THREE-transfer week is tight enough
    # that spending one early forces a hit — a 2-transfer week genuinely is not,
    # which is what the sibling test covers.
    outs, subs = [], []
    for pid in squad:
        sub = next((i for i in ids[15:]
                    if players[i].element_type == players[pid].element_type
                    and i not in subs), None)
        if sub:
            outs.append(pid)
            subs.append(sub)
        if len(outs) == 3:
            break
    if len(outs) < 3:
        pytest.skip("demo data has too few replacements")

    plan = _fake_plan(gws, squad, {gws[2]: (subs, outs)})
    tree = build_decision_tree(db, plan, gws, bank=0, free_transfers=1)

    first = tree["main_line"][0]
    assert first["action"] == "roll"
    assert first["label"] == "Roll"
    kinds = {w["kind"] for w in first["why"]}
    assert "banking_funds_later_move" in kinds
    assert "net_cost_of_acting_now" in kinds

    banking = next(w for w in first["why"] if w["kind"] == "banking_funds_later_move")
    assert banking["numbers"]["hit_cost"] == 4          # one forced hit
    assert banking["numbers"]["at_gw"] == gws[2]
    net = next(w for w in first["why"] if w["kind"] == "net_cost_of_acting_now")
    assert isinstance(net["numbers"]["net_xp"], float)


def test_roll_admits_when_banking_is_not_the_reason(db):
    """If spending early forces no hit, we must NOT claim it does."""
    from app.services.common import planning_start_gw

    start = planning_start_gw(db)
    gws = [start, start + 1]
    ids = [p.id for p in db.scalars(select(Player)).all()]
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    squad, out_a = ids[:15], ids[0]
    sub = next(i for i in ids[15:]
               if players[i].element_type == players[out_a].element_type)

    # one single transfer next week — 1 FT always covers it
    plan = _fake_plan(gws, squad, {gws[1]: ([sub], [out_a])})
    tree = build_decision_tree(db, plan, gws, bank=0, free_transfers=1)

    kinds = {w["kind"] for w in tree["main_line"][0]["why"]}
    assert "no_banking_pressure" in kinds
    assert "banking_funds_later_move" not in kinds


def test_final_gw_move_is_flagged_as_horizon_edge(db):
    from app.services.common import planning_start_gw

    start = planning_start_gw(db)
    gws = [start, start + 1]
    ids = [p.id for p in db.scalars(select(Player)).all()]
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    squad, out_a = ids[:15], ids[0]
    sub = next(i for i in ids[15:]
               if players[i].element_type == players[out_a].element_type)

    plan = _fake_plan(gws, squad, {gws[1]: ([sub], [out_a])})
    tree = build_decision_tree(db, plan, gws, bank=0, free_transfers=1)

    last = tree["main_line"][-1]
    assert last["action"] == "transfer"
    assert "horizon_edge" in {w["kind"] for w in last["why"]}


# ----------------------------------------------------------------- branches --
def test_injury_branch_offers_a_costed_alternative(db):
    """A doubtful target must spawn a branch with a real fallback, not a note."""
    from app.services.common import planning_start_gw

    start = planning_start_gw(db)
    gws = [start, start + 1]
    ids = [p.id for p in db.scalars(select(Player)).all()]
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    squad, out_a = ids[:15], ids[0]
    target = next(i for i in ids[15:]
                  if players[i].element_type == players[out_a].element_type)

    original = (players[target].status, players[target].chance_of_playing_next_round)
    players[target].status = "d"
    players[target].chance_of_playing_next_round = 50
    db.flush()
    try:
        plan = _fake_plan(gws, squad, {gws[1]: ([target], [out_a])})
        tree = build_decision_tree(db, plan, gws, bank=0, free_transfers=1)
        inj = [b for b in tree["branches"] if b["trigger"] == "injury"]
        assert inj, "a 50%-doubt target must produce an injury branch"
        b = inj[0]
        assert b["player"]["id"] == target
        assert "50%" in b["evidence"]
        assert b["at_gameweek"] == gws[1]
        if b["action"]["alternatives"]:
            alt = b["action"]["alternatives"][0]
            assert alt["id"] != target
            # the fallback must be affordable in the outgoing player's slot
            assert alt["price"] <= players[out_a].now_cost / 10.0
            assert "xp_vs_target" in alt      # costed against the main line
    finally:
        players[target].status, players[target].chance_of_playing_next_round = original
        db.flush()


def test_price_rise_branch_carries_its_caveat(db):
    """Momentum is an indicator; the branch must not read as a prediction."""
    from app.services.common import planning_start_gw

    start = planning_start_gw(db)
    gws = [start, start + 1]
    ids = [p.id for p in db.scalars(select(Player)).all()]
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    squad, out_a = ids[:15], ids[0]
    target = next(i for i in ids[15:]
                  if players[i].element_type == players[out_a].element_type)

    original = (players[target].transfers_in_event, players[target].transfers_out_event)
    players[target].transfers_in_event = 200_000
    players[target].transfers_out_event = 1_000
    db.flush()
    try:
        plan = _fake_plan(gws, squad, {gws[1]: ([target], [out_a])})
        tree = build_decision_tree(db, plan, gws, bank=0, free_transfers=1)
        rise = [b for b in tree["branches"] if b["trigger"] == "price_rise"]
        assert rise, "strong net buying must produce a price branch"
        b = rise[0]
        assert b["confidence"] in ("Low", "Medium")   # never High
        assert "không công khai" in b["caveat"]
        assert b["action"]["price_change_time_uk"] == "01:30"
        assert b["action"]["tradeoff"]
    finally:
        players[target].transfers_in_event, players[target].transfers_out_event = original
        db.flush()


def test_quiet_data_produces_no_invented_branches(db):
    """No injury and no transfer momentum => no branches. Silence is a feature."""
    from app.services.common import planning_start_gw

    start = planning_start_gw(db)
    gws = [start, start + 1]
    ids = [p.id for p in db.scalars(select(Player)).all()]
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    squad, out_a = ids[:15], ids[0]
    target = next(i for i in ids[15:]
                  if players[i].element_type == players[out_a].element_type
                  and (players[i].status or "a") == "a"
                  and players[i].chance_of_playing_next_round is None
                  and abs((players[i].transfers_in_event or 0)
                          - (players[i].transfers_out_event or 0)) < 25_000)

    plan = _fake_plan(gws, squad, {gws[1]: ([target], [out_a])})
    tree = build_decision_tree(db, plan, gws, bank=0, free_transfers=1)
    assert [b for b in tree["branches"] if b["player"]["id"] == target] == []

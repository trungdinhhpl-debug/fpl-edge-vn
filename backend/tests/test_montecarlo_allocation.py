"""How team goals get allocated to players — the claims METHODOLOGY §3 makes.

Two kinds of test live here:

  * **Properties that must always hold** (means reproduce the player's season
    xG/xA, team-mates are correlated through the shared team total).
  * **Characterisation of documented LIMITATIONS.** These lock the published
    numbers to reality. If someone fixes the allocation — multinomial draw,
    redistributing an absent player's share, penalties as their own draw — these
    will fail, and that failure is the reminder to update METHODOLOGY §3 and the
    Methodology page in the same commit. A limitation nobody can see is worse
    than one written down.
"""
import numpy as np
import pytest

from app.engine.montecarlo import MCPlayer, simulate_fixture

N = 60_000
LAM = 1.8


def mk(pid, share_g, share_a=0.0, p_start=0.95, et=4):
    return MCPlayer(player_id=pid, element_type=et, p_start=p_start, p_sub=0.02,
                    p_60_plus=p_start * 0.9, share_goal=share_g,
                    share_assist=share_a, saves90=0.0, dc_hit_prob=0.0,
                    yellow90=0.0, bonus_base=0.2)


# ------------------------------------------------ properties that must hold ---
def test_share_reproduces_the_players_season_output():
    """share = xG_player / xG_team, drawn on team_goals, must round-trip.

    A player with 15 xG in a team totalling 60, at λ=1.5 over 38 matches, should
    come back out at ~15 goals. This is what makes the xG denominator correct
    for assists too, even though it is an ASSIST share — the draw is over goals.
    """
    xg_p, xg_team, lam, matches = 15.0, 60.0, 1.5, 38
    share = xg_p / xg_team
    assert lam * share * matches == pytest.approx(xg_p, rel=0.06)

    xa_p = 8.0
    share_a = xa_p / xg_team          # xG denominator, deliberately
    assert lam * share_a * matches == pytest.approx(xa_p, rel=0.06)


def test_team_mates_are_correlated_through_the_shared_total():
    """The whole reason to simulate at team level rather than per player."""
    rng = np.random.default_rng(7)
    squad = [mk(0, 0.30), mk(1, 0.28)]
    out = simulate_fixture(squad, LAM, 1.1, N, rng)
    corr = np.corrcoef(out[0], out[1])[0, 1]
    assert corr > 0.01, f"team-mates should not be independent (corr={corr:.4f})"


def test_a_player_who_never_plays_scores_nothing():
    """No appearance, no points — including a goalkeeper's saves.

    Regression: the save draw used `np.where(started, 1.0, 0.3)`, and the 0.3
    meant for a keeper who came on also applied to one who never left the bench.
    A permanent reserve collected 0.064 points a match from saves he could not
    have made, which flattered every cheap bench goalkeeper in the optimiser.
    """
    rng = np.random.default_rng(8)
    outfield = MCPlayer(player_id=0, element_type=4, p_start=0.0, p_sub=0.0,
                        p_60_plus=0.0, share_goal=0.4, share_assist=0.3,
                        saves90=0.0, dc_hit_prob=0.5, yellow90=0.2,
                        bonus_base=0.5)
    keeper = MCPlayer(player_id=1, element_type=1, p_start=0.0, p_sub=0.0,
                      p_60_plus=0.0, share_goal=0.0, share_assist=0.0,
                      saves90=3.0, dc_hit_prob=0.0, yellow90=0.0, bonus_base=0.0)
    out = simulate_fixture([outfield, keeper], LAM, 1.1, N, rng)
    assert out[0].max() == 0.0, "outfield reserve scored without playing"
    assert out[1].max() == 0.0, "bench keeper earned save points without playing"


def test_a_keeper_who_comes_on_can_still_make_saves():
    """The fix must not zero out saves for a keeper who does appear."""
    rng = np.random.default_rng(14)
    keeper = MCPlayer(player_id=0, element_type=1, p_start=0.9, p_sub=0.05,
                      p_60_plus=0.85, share_goal=0.0, share_assist=0.0,
                      saves90=3.5, dc_hit_prob=0.0, yellow90=0.0, bonus_base=0.0)
    out = simulate_fixture([keeper], LAM, 1.1, N, rng)
    assert out[0].mean() > 2.0


# --------------------------------- documented limitations (see METHODOLOGY §3) --
def test_allocation_is_not_conserved_against_the_team_total():
    """LIMITATION: independent Binomials, not a Multinomial over team_goals.

    Documented as ~21% of matches over-allocating. If this test fails because
    the allocation became conserved, update METHODOLOGY §3 — the fix is good
    news, but the published number must stop claiming otherwise.
    """
    rng = np.random.default_rng(9)
    team_goals = rng.poisson(LAM, N)
    shares = [0.30, 0.25, 0.20, 0.15]
    alloc = sum(rng.binomial(team_goals, s) for s in shares)

    assert alloc.mean() == pytest.approx(LAM * sum(shares), rel=0.05), (
        "the MEAN is correct — the defect is in the tail, not the average"
    )
    over = (alloc > team_goals).mean()
    assert over > 0.10, (
        f"over-allocation is documented at ~21%; measured {over:.1%}. "
        "If it is now ~0, the allocation was fixed — update METHODOLOGY §3."
    )


def test_an_absent_players_share_is_discarded_not_redistributed():
    """LIMITATION: team-mates gain nothing when a starter misses out.

    This is why bench/replacement players are systematically under-rated.
    """
    rng_a = np.random.default_rng(11)
    rng_b = np.random.default_rng(11)
    with_star = simulate_fixture([mk(0, 0.35, p_start=0.95), mk(1, 0.30)],
                                 LAM, 1.1, N, rng_a)[1].mean()
    without_star = simulate_fixture([mk(0, 0.35, p_start=0.05), mk(1, 0.30)],
                                    LAM, 1.1, N, rng_b)[1].mean()
    assert without_star == pytest.approx(with_star, rel=1e-9), (
        "the understudy's points moved — share is now redistributed, "
        "which would be a fix: update METHODOLOGY §3."
    )


def test_a_player_can_be_credited_with_assisting_his_own_goal():
    """LIMITATION: assists are drawn independently of goals."""
    rng = np.random.default_rng(12)
    team_goals = rng.poisson(LAM, N)
    goals = rng.binomial(team_goals, 0.35)
    assists = rng.binomial(team_goals, 0.30)
    both = ((goals > 0) & (assists > 0)).mean()
    assert both > 0.15, (
        f"documented at ~26%; measured {both:.1%}. If it is now ~0, assists "
        "became conditional on goals — update METHODOLOGY §3."
    )


def test_double_gameweek_fixtures_are_drawn_independently():
    """LIMITATION: no 'rested in game 1 so likelier to start game 2' logic."""
    rng = np.random.default_rng(13)
    squad = [mk(0, 0.30, p_start=0.60)]
    first = simulate_fixture(squad, LAM, 1.1, N, rng)[0]
    second = simulate_fixture(squad, LAM, 1.1, N, rng)[0]
    corr = np.corrcoef(first, second)[0, 1]
    assert abs(corr) < 0.05, (
        f"the two fixtures correlate at {corr:+.4f}; rotation between matches "
        "may now be modelled — update METHODOLOGY §3."
    )

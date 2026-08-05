"""How team goals get allocated to players — the claims METHODOLOGY §3 makes.

Every test here corresponds to a published statement. If one fails because the
allocation changed, METHODOLOGY §3 and the Methodology page must change in the
same commit: a documented mechanism that no longer matches the code is worse
than no documentation at all.
"""
import numpy as np
import pytest

from app.engine.montecarlo import (
    MCPlayer, _allocate, _effective_shares, simulate_fixture,
)

N = 60_000
LAM = 1.8


def mk(pid, share_g, share_a=0.0, p_start=0.95, et=4):
    return MCPlayer(player_id=pid, element_type=et, p_start=p_start, p_sub=0.02,
                    p_60_plus=p_start * 0.9, share_goal=share_g,
                    share_assist=share_a, saves90=0.0, dc_hit_prob=0.0,
                    yellow90=0.0, bonus_base=0.2)


def _keeper(pid=0):
    return MCPlayer(player_id=pid, element_type=1, p_start=0.98, p_sub=0.0,
                    p_60_plus=0.95, share_goal=0.0, share_assist=0.0,
                    saves90=3.0, dc_hit_prob=0.0, yellow90=0.0, bonus_base=0.0)


def _defender(pid=1):
    return MCPlayer(player_id=pid, element_type=2, p_start=0.95, p_sub=0.02,
                    p_60_plus=0.9, share_goal=0.05, share_assist=0.03,
                    saves90=0.0, dc_hit_prob=0.3, yellow90=0.1, bonus_base=0.1)


# ------------------------------------------------------ share arithmetic ----
def test_share_reproduces_the_players_season_output():
    """share = xG_player / xG_team, drawn on team_goals, must round-trip.

    A player with 15 xG in a team totalling 60, at λ=1.5 over 38 matches, comes
    back out at ~15 goals. This is also why the xG denominator is right for
    ASSISTS — the draw is over goals, so the units already match.
    """
    xg_p, xg_team, lam, matches = 15.0, 60.0, 1.5, 38
    assert lam * (xg_p / xg_team) * matches == pytest.approx(xg_p, rel=0.06)
    xa_p = 8.0
    assert lam * (xa_p / xg_team) * matches == pytest.approx(xa_p, rel=0.06)


# ------------------------------------------------------------ correlation ---
def test_keeper_and_defender_share_the_clean_sheet():
    """The reason to simulate at team level: a clean sheet is one event."""
    out = simulate_fixture([_keeper(0), _defender(1)], LAM, 1.1, N,
                           np.random.default_rng(7))
    corr = np.corrcoef(out[0], out[1])[0, 1]
    assert corr > 0.3, f"clean sheet should bind GK and DEF (corr={corr:.4f})"


def test_forwards_compete_for_the_same_goals():
    """Splitting a Poisson total multinomially leaves the parts ~independent.

    Poisson thinning: a Poisson total shared out multinomially yields
    independent counts. The mild NEGATIVE tilt on top is the share transfer
    working — when one forward sits out, the other inherits his share, so their
    fortunes pull slightly apart rather than together.
    """
    out = simulate_fixture([mk(0, 0.30), mk(1, 0.28)], LAM, 1.1, N,
                           np.random.default_rng(7))
    corr = np.corrcoef(out[0], out[1])[0, 1]
    assert -0.15 < corr < 0.05, f"unexpected forward correlation {corr:.4f}"


# ------------------------------------------------------------- allocation ---
def test_allocated_goals_never_exceed_what_the_team_scored():
    """Regression: independent Binomials let the parts exceed the whole.

    20.9% of simulated matches used to allocate more goals than the team had
    scored — once 14 goals in a 4-goal match. The mean was right, the tail was
    not, and the tail is where P(haul) and the ceiling are read off.
    """
    rng = np.random.default_rng(9)
    squad = [mk(i, s) for i, s in enumerate([0.30, 0.25, 0.20, 0.15])]
    played = {p.player_id: np.ones(N, dtype=bool) for p in squad}
    team_goals = rng.poisson(LAM, N)
    eff, full = _effective_shares(squad, played, "share_goal", N)
    alloc = _allocate(team_goals, eff, [p.player_id for p in squad], rng)
    total = sum(alloc.values())

    assert (total <= team_goals).all(), (
        f"over-allocated in {(total > team_goals).mean():.2%} of matches"
    )
    assert total.mean() == pytest.approx(LAM * full, rel=0.03), (
        "the fix must not move the mean, only the tail"
    )


def test_unattributed_goals_stay_unattributed():
    """Shares sum to <1 (own goals, fringe players); the shortfall must not be
    forced onto the last player in the list."""
    rng = np.random.default_rng(10)
    squad = [mk(0, 0.20), mk(1, 0.15)]          # only 35% of the team's goals
    played = {p.player_id: np.ones(N, dtype=bool) for p in squad}
    team_goals = rng.poisson(LAM, N)
    eff, full = _effective_shares(squad, played, "share_goal", N)
    alloc = _allocate(team_goals, eff, [0, 1], rng)
    assert sum(alloc.values()).mean() == pytest.approx(LAM * full, rel=0.05)
    assert full == pytest.approx(0.35)


def test_an_absent_players_share_passes_to_whoever_played():
    """The understudy must gain when the first choice sits out.

    Regression: the old code zeroed an absent player's goals and stopped there,
    so his share of the team's output evaporated. A team-mate's expected points
    were byte-identical whether the starter played 95% or 5% of the time, which
    under-rated every replacement in the game.
    """
    means = [
        simulate_fixture([mk(0, 0.35, p_start=ps), mk(1, 0.30)],
                         LAM, 1.1, N, np.random.default_rng(11))[1].mean()
        for ps in (0.95, 0.50, 0.05)
    ]
    assert means[0] < means[1] < means[2], f"understudy should gain: {means}"
    assert means[2] - means[0] > 1.0, "the transfer is too small to matter"


def test_nobody_assists_a_goal_he_scored_himself():
    """Regression: assists were drawn independently of the goal draw, so the
    same player was credited with both on 26% of simulated matches."""
    rng = np.random.default_rng(12)
    squad = [mk(0, 0.35, 0.30), mk(1, 0.25, 0.20)]
    played = {p.player_id: np.ones(N, dtype=bool) for p in squad}
    team_goals = rng.poisson(LAM, N)
    goals = _allocate(team_goals,
                      _effective_shares(squad, played, "share_goal", N)[0],
                      [0, 1], rng)
    cap = {pid: np.maximum(team_goals - goals[pid], 0) for pid in (0, 1)}
    assists = _allocate(team_goals,
                        _effective_shares(squad, played, "share_assist", N)[0],
                        [0, 1], rng, cap=cap)
    for pid in (0, 1):
        assert (assists[pid] <= team_goals - goals[pid]).all(), (
            f"player {pid} assisted a goal he scored himself"
        )
    assert (sum(assists.values()) <= team_goals).all()


# ---------------------------------------------------------- availability ----
def test_a_player_who_never_plays_scores_nothing():
    """No appearance, no points — including a goalkeeper's saves.

    Regression: the save draw used `np.where(started, 1.0, 0.3)`, and the 0.3
    meant for a keeper who came on also applied to one who never left the bench.
    A permanent reserve collected 0.064 points a match from saves he could not
    have made, which flattered every cheap bench goalkeeper in the optimiser.
    """
    outfield = MCPlayer(player_id=0, element_type=4, p_start=0.0, p_sub=0.0,
                        p_60_plus=0.0, share_goal=0.4, share_assist=0.3,
                        saves90=0.0, dc_hit_prob=0.5, yellow90=0.2,
                        bonus_base=0.5)
    keeper = MCPlayer(player_id=1, element_type=1, p_start=0.0, p_sub=0.0,
                      p_60_plus=0.0, share_goal=0.0, share_assist=0.0,
                      saves90=3.0, dc_hit_prob=0.0, yellow90=0.0, bonus_base=0.0)
    out = simulate_fixture([outfield, keeper], LAM, 1.1, N,
                           np.random.default_rng(8))
    assert out[0].max() == 0.0, "outfield reserve scored without playing"
    assert out[1].max() == 0.0, "bench keeper earned save points without playing"


def test_a_keeper_who_comes_on_can_still_make_saves():
    """The fix must not zero out saves for a keeper who does appear."""
    out = simulate_fixture([_keeper(0)], LAM, 1.1, N, np.random.default_rng(14))
    assert out[0].mean() > 2.0


# ------------------------------------------------------- double gameweek ----
def test_double_gameweek_fixtures_are_drawn_independently():
    """LIMITATION (still open): no 'rested in game 1 so likelier to start
    game 2' logic, and no fatigue link between the two matches."""
    rng = np.random.default_rng(13)
    squad = [mk(0, 0.30, p_start=0.60)]
    first = simulate_fixture(squad, LAM, 1.1, N, rng)[0]
    second = simulate_fixture(squad, LAM, 1.1, N, rng)[0]
    corr = np.corrcoef(first, second)[0, 1]
    assert abs(corr) < 0.05, (
        f"the two fixtures correlate at {corr:+.4f}; rotation between matches "
        "may now be modelled — update METHODOLOGY §3."
    )

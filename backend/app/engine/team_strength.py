"""Team attacking / defensive strength -> expected goals per fixture.

Combines two signals with Bayesian shrinkage (spec §7):
  1. FPL strength ratings (strength_attack/defence_home/away), venue-aware.
  2. Empirical team xG / xGA per game, derived from players' season expected
     stats and finished fixtures.

The blend weight favours empirical data as more matches are played:
    w = matches / (matches + K)
so early-season noise is regularised toward the ratings-based prior.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# league-average goals scored by one team in one game
LEAGUE_AVG_GOALS = 1.42
HOME_ADV = 1.12          # multiply home team's attack
AWAY_ADJ = 0.90          # multiply away team's attack
SHRINK_K = 6.0           # matches before empirical dominates
MIN_LAMBDA = 0.15
MAX_LAMBDA = 4.5


@dataclass
class TeamRates:
    team_id: int
    # normalized (league mean = 1.0)
    attack_home: float
    attack_away: float
    defence_home: float
    defence_away: float
    emp_xg_per_game: float      # empirical attack
    emp_xga_per_game: float     # empirical defence (goals conceded)
    matches: int


class TeamStrength:
    def __init__(self, teams: list, players: list, finished_fixtures: list) -> None:
        self._rates: dict[int, TeamRates] = {}
        self._build(teams, players, finished_fixtures)

    # ---------------------------------------------------------------- build ---
    def _build(self, teams, players, finished_fixtures) -> None:
        if not teams:
            return
        # league means for normalisation
        def _mean(attr: str) -> float:
            vals = [getattr(t, attr) or 0 for t in teams]
            vals = [v for v in vals if v]
            return sum(vals) / len(vals) if vals else 1.0

        m_att_h = _mean("strength_attack_home")
        m_att_a = _mean("strength_attack_away")
        m_def_h = _mean("strength_defence_home")
        m_def_a = _mean("strength_defence_away")

        # matches played per team (from finished fixtures)
        matches: dict[int, int] = {t.id: 0 for t in teams}
        goals_for: dict[int, int] = {t.id: 0 for t in teams}
        goals_against: dict[int, int] = {t.id: 0 for t in teams}
        for f in finished_fixtures:
            if f.team_h_score is None or f.team_a_score is None:
                continue
            matches[f.team_h] = matches.get(f.team_h, 0) + 1
            matches[f.team_a] = matches.get(f.team_a, 0) + 1
            goals_for[f.team_h] = goals_for.get(f.team_h, 0) + f.team_h_score
            goals_for[f.team_a] = goals_for.get(f.team_a, 0) + f.team_a_score
            goals_against[f.team_h] = goals_against.get(f.team_h, 0) + f.team_a_score
            goals_against[f.team_a] = goals_against.get(f.team_a, 0) + f.team_h_score

        # empirical team xG from players' season expected_goals
        team_xg: dict[int, float] = {t.id: 0.0 for t in teams}
        for p in players:
            team_xg[p.team_id] = team_xg.get(p.team_id, 0.0) + (p.expected_goals or 0.0)

        for t in teams:
            mp = max(matches.get(t.id, 0), 0)
            emp_xg = (team_xg.get(t.id, 0.0) / mp) if mp else LEAGUE_AVG_GOALS
            # blend actual goals + xG for empirical attack; goals-against for defence
            emp_gf = (goals_for.get(t.id, 0) / mp) if mp else LEAGUE_AVG_GOALS
            emp_ga = (goals_against.get(t.id, 0) / mp) if mp else LEAGUE_AVG_GOALS
            emp_attack = 0.5 * emp_xg + 0.5 * emp_gf if mp else LEAGUE_AVG_GOALS

            self._rates[t.id] = TeamRates(
                team_id=t.id,
                attack_home=(t.strength_attack_home or m_att_h) / m_att_h,
                attack_away=(t.strength_attack_away or m_att_a) / m_att_a,
                defence_home=(t.strength_defence_home or m_def_h) / m_def_h,
                defence_away=(t.strength_defence_away or m_def_a) / m_def_a,
                emp_xg_per_game=emp_attack,
                emp_xga_per_game=emp_ga,
                matches=mp,
            )

    # ------------------------------------------------------------ expected ----
    def expected_goals(self, team_id: int, opp_id: int, is_home: bool) -> tuple[float, float]:
        """Return (lambda_for, lambda_against) for `team_id` in this fixture."""
        t = self._rates.get(team_id)
        o = self._rates.get(opp_id)
        if not t or not o:
            return LEAGUE_AVG_GOALS, LEAGUE_AVG_GOALS

        if is_home:
            att = t.attack_home
            opp_def = o.defence_away
            opp_att = o.attack_away
            own_def = t.defence_home
            venue_for, venue_against = HOME_ADV, AWAY_ADJ
        else:
            att = t.attack_away
            opp_def = o.defence_home
            opp_att = o.attack_home
            own_def = t.defence_away
            venue_for, venue_against = AWAY_ADJ, HOME_ADV

        # ratings-based prior (higher opp defence rating => fewer goals)
        prior_for = LEAGUE_AVG_GOALS * venue_for * att / max(opp_def, 0.3)
        prior_against = LEAGUE_AVG_GOALS * venue_against * opp_att / max(own_def, 0.3)

        # shrink toward empirical as matches accumulate
        w = t.matches / (t.matches + SHRINK_K)
        lam_for = w * t.emp_xg_per_game * (venue_for) + (1 - w) * prior_for
        lam_against = w * t.emp_xga_per_game * (venue_against) + (1 - w) * prior_against

        return (
            min(max(lam_for, MIN_LAMBDA), MAX_LAMBDA),
            min(max(lam_against, MIN_LAMBDA), MAX_LAMBDA),
        )

    def clean_sheet_prob(self, team_id: int, opp_id: int, is_home: bool) -> float:
        _, lam_against = self.expected_goals(team_id, opp_id, is_home)
        return math.exp(-lam_against)  # Poisson P(0 conceded)

    def season_avg_gf(self, team_id: int) -> float:
        t = self._rates.get(team_id)
        return t.emp_xg_per_game if t else LEAGUE_AVG_GOALS

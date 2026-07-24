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


def _clamp(v: float, lo: float = 0.55, hi: float = 1.7) -> float:
    return max(lo, min(hi, v))


def load_market_map(db) -> dict[tuple[int, int], tuple[float, float]]:
    """(home_team_id, away_team_id) -> (lam_home, lam_away) from stored odds.

    Read from the DB, never from the odds API, so page loads cost no quota.
    """
    from sqlalchemy import select

    from app.models import MarketOdds

    rows = db.scalars(select(MarketOdds)).all()
    return {(r.team_h, r.team_a): (r.lam_home, r.lam_away) for r in rows}


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
    def __init__(
        self,
        teams: list,
        players: list,
        finished_fixtures: list,
        market: dict[tuple[int, int], tuple[float, float]] | None = None,
        market_weight: float = 0.7,
    ) -> None:
        """`market` maps (home_team_id, away_team_id) -> (lam_home, lam_away)
        from bookmaker prices. Where present it is blended over the internal
        model (spec §3: licensed market data outranks a model estimate)."""
        self._rates: dict[int, TeamRates] = {}
        self._market = market or {}
        self._market_weight = market_weight
        self._build(teams, players, finished_fixtures)

    def has_market(self, team_id: int, opp_id: int, is_home: bool) -> bool:
        key = (team_id, opp_id) if is_home else (opp_id, team_id)
        return key in self._market

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

        # team xGA proxy = the highest per-player expected_goals_conceded on the
        # team (a full-season nailed player ≈ team's season xGA). Used to derive a
        # DEFENCE index when FPL's strength ratings are absent (pre-season = all 0).
        team_xga_proxy: dict[int, float] = {t.id: 0.0 for t in teams}
        for p in players:
            team_xga_proxy[p.team_id] = max(
                team_xga_proxy.get(p.team_id, 0.0), p.expected_goals_conceded or 0.0
            )
        _xg_vals = [v for v in team_xg.values() if v > 0]
        _xga_vals = [v for v in team_xga_proxy.values() if v > 0]
        mean_team_xg = sum(_xg_vals) / len(_xg_vals) if _xg_vals else 1.0
        mean_team_xga = sum(_xga_vals) / len(_xga_vals) if _xga_vals else 1.0

        for t in teams:
            mp = max(matches.get(t.id, 0), 0)
            emp_xg = (team_xg.get(t.id, 0.0) / mp) if mp else LEAGUE_AVG_GOALS
            # blend actual goals + xG for empirical attack; goals-against for defence
            emp_gf = (goals_for.get(t.id, 0) / mp) if mp else LEAGUE_AVG_GOALS
            emp_ga = (goals_against.get(t.id, 0) / mp) if mp else LEAGUE_AVG_GOALS
            emp_attack = 0.5 * emp_xg + 0.5 * emp_gf if mp else LEAGUE_AVG_GOALS

            # fallback indices from last-season player xG/xGA when FPL ratings
            # aren't set yet (pre-season). Attack: higher = stronger. Defence:
            # higher = concedes fewer = stronger (matches how expected_goals uses it).
            derived_att = _clamp(team_xg.get(t.id, 0.0) / mean_team_xg) if mean_team_xg else 1.0
            _xga = team_xga_proxy.get(t.id, 0.0)
            derived_def = _clamp(mean_team_xga / _xga) if _xga else 1.0
            has_fpl = bool(t.strength_attack_home)

            self._rates[t.id] = TeamRates(
                team_id=t.id,
                attack_home=(t.strength_attack_home / m_att_h) if has_fpl else derived_att,
                attack_away=(t.strength_attack_away / m_att_a) if has_fpl else derived_att,
                defence_home=(t.strength_defence_home / m_def_h) if has_fpl else derived_def,
                defence_away=(t.strength_defence_away / m_def_a) if has_fpl else derived_def,
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

        # bookmaker consensus for this exact fixture, if we have it
        key = (team_id, opp_id) if is_home else (opp_id, team_id)
        mk = self._market.get(key)
        if mk:
            mk_for, mk_against = (mk[0], mk[1]) if is_home else (mk[1], mk[0])
            w = self._market_weight
            lam_for = w * mk_for + (1 - w) * lam_for
            lam_against = w * mk_against + (1 - w) * lam_against

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

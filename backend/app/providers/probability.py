"""Probability provider abstraction (spec §3 Tier-2).

Converts bookmaker prices into per-team expected goals (lambda) so the
projection engine can use market consensus instead of — or blended with — its
own strength model.

Method (standard double-Poisson inversion):
  1. Totals market (Over/Under L)  -> total goals  T = lam_home + lam_away
     solved so Poisson(T) reproduces the market's P(over L).
  2. 1X2 market                    -> supremacy    S = lam_home - lam_away
     solved so the double-Poisson reproduces the market's P(home win).
  -> lam_home = (T + S) / 2,  lam_away = (T - S) / 2

Prices are de-vigged by normalising implied probabilities across outcomes, and
averaged over all bookmakers offering the market.

If no licensed key is configured we return nothing and the engine falls back to
its internal model, clearly labelled `model_estimate` — we never present a model
number as a real market price.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import httpx

from app.config import settings

ODDS_BASE = "https://api.the-odds-api.com/v4"
DEFAULT_TOTAL_GOALS = 2.7      # league-average total when no totals market
MAX_GOALS = 10                 # truncation for the Poisson grid


@dataclass
class MarketMatch:
    home_name: str
    away_name: str
    commence_time: str
    lam_home: float
    lam_away: float
    p_home: float
    p_draw: float
    p_away: float
    total_goals: float
    n_bookmakers: int
    source: str = "odds_api"
    is_market: bool = True


# ---------------------------------------------------------------- maths -----
def _pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _outcome_probs(lam_h: float, lam_a: float) -> tuple[float, float, float]:
    """P(home win), P(draw), P(away win) under independent double-Poisson."""
    ph = pd = pa = 0.0
    hp = [_pmf(i, lam_h) for i in range(MAX_GOALS + 1)]
    ap = [_pmf(j, lam_a) for j in range(MAX_GOALS + 1)]
    for i, phi in enumerate(hp):
        for j, paj in enumerate(ap):
            p = phi * paj
            if i > j:
                ph += p
            elif i == j:
                pd += p
            else:
                pa += p
    return ph, pd, pa


def _p_over(total: float, line: float) -> float:
    """P(goals > line) for Poisson(total). Lines are .5-based, so no push."""
    k = int(math.floor(line))
    cdf = sum(_pmf(i, total) for i in range(0, k + 1))
    return max(0.0, min(1.0, 1.0 - cdf))


def _solve_total(p_over: float, line: float) -> float:
    """Find T so that P(over line | Poisson(T)) == p_over (bisection)."""
    lo, hi = 0.3, 7.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _p_over(mid, line) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _solve_supremacy(p_home: float, total: float) -> float:
    """Find S so the double-Poisson reproduces P(home win) (bisection)."""
    lo, hi = -min(total, 4.0) + 0.05, min(total, 4.0) - 0.05
    for _ in range(50):
        mid = (lo + hi) / 2
        lam_h = max((total + mid) / 2, 0.02)
        lam_a = max((total - mid) / 2, 0.02)
        ph, _, _ = _outcome_probs(lam_h, lam_a)
        if ph < p_home:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ------------------------------------------------------------ name match ----
_ALIASES = {
    "manchester united": "man utd",
    "manchester city": "man city",
    "tottenham hotspur": "spurs",
    "brighton and hove albion": "brighton",
    "nottingham forest": "nott'm forest",
    "newcastle united": "newcastle",
    "leeds united": "leeds",
    "wolverhampton wanderers": "wolves",
    "west ham united": "west ham",
    "afc bournemouth": "bournemouth",
    "sheffield united": "sheffield utd",
}


def normalise_team(name: str) -> str:
    n = (name or "").strip().lower()
    n = _ALIASES.get(n, n)
    n = re.sub(r"\b(fc|afc|association football club)\b", "", n)
    n = re.sub(r"[^a-z' ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def match_team_id(odds_name: str, fpl_teams: dict[int, str]) -> int | None:
    """Map a bookmaker team name onto an FPL team id (None if unsure)."""
    target = normalise_team(odds_name)
    if not target:
        return None
    best_id, best_score = None, 0.0
    for tid, fpl_name in fpl_teams.items():
        cand = normalise_team(fpl_name)
        if cand == target:
            return tid
        # token containment: "hull city" vs "hull", "coventry city" vs "coventry"
        a, b = set(target.split()), set(cand.split())
        if not a or not b:
            continue
        score = len(a & b) / len(a | b)
        if a <= b or b <= a:
            score = max(score, 0.9)
        if score > best_score:
            best_id, best_score = tid, score
    return best_id if best_score >= 0.5 else None


# ------------------------------------------------------------- provider -----
class ProbabilityProvider:
    name = "base"

    def get_matches(self) -> list[MarketMatch]:  # pragma: no cover
        raise NotImplementedError


class OddsAPIProvider(ProbabilityProvider):
    """the-odds-api.com — soccer_epl h2h + totals."""

    name = "odds_api"

    def __init__(self, api_key: str, region: str = "uk") -> None:
        self.api_key = api_key
        self.region = region

    def _fetch(self) -> list[dict]:
        params = {
            "apiKey": self.api_key,
            "regions": self.region,
            "markets": "h2h,totals",
            "oddsFormat": "decimal",
        }
        r = httpx.get(
            f"{ODDS_BASE}/sports/soccer_epl/odds",
            params=params,
            timeout=settings.http_timeout_seconds,
        )
        r.raise_for_status()
        return r.json()

    def get_matches(self) -> list[MarketMatch]:
        try:
            raw = self._fetch()
        except Exception:
            return []

        out: list[MarketMatch] = []
        for game in raw:
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            h2h_probs: list[tuple[float, float, float]] = []
            totals: list[tuple[float, float]] = []  # (line, p_over)
            books = game.get("bookmakers", []) or []

            for bk in books:
                for m in bk.get("markets", []):
                    if m.get("key") == "h2h":
                        imp = {}
                        for o in m.get("outcomes", []):
                            price = o.get("price") or 0
                            if price > 1:
                                imp[o["name"]] = 1.0 / price
                        s = sum(imp.values())
                        if s > 0 and home in imp and away in imp:
                            h2h_probs.append(
                                (imp[home] / s, imp.get("Draw", 0.0) / s, imp[away] / s)
                            )
                    elif m.get("key") == "totals":
                        over = under = None
                        line = None
                        for o in m.get("outcomes", []):
                            price = o.get("price") or 0
                            if price <= 1:
                                continue
                            if o.get("name") == "Over":
                                over, line = 1.0 / price, o.get("point")
                            elif o.get("name") == "Under":
                                under = 1.0 / price
                        if over and under and line:
                            totals.append((float(line), over / (over + under)))

            if not h2h_probs:
                continue

            n = len(h2h_probs)
            p_home = sum(p[0] for p in h2h_probs) / n
            p_draw = sum(p[1] for p in h2h_probs) / n
            p_away = sum(p[2] for p in h2h_probs) / n

            if totals:
                # use the most common line, average its P(over)
                line = max({l for l, _ in totals}, key=lambda x: sum(1 for l, _ in totals if l == x))
                sel = [p for l, p in totals if l == line]
                total_goals = _solve_total(sum(sel) / len(sel), line)
            else:
                total_goals = DEFAULT_TOTAL_GOALS

            supremacy = _solve_supremacy(p_home, total_goals)
            lam_h = max((total_goals + supremacy) / 2, 0.05)
            lam_a = max((total_goals - supremacy) / 2, 0.05)

            out.append(
                MarketMatch(
                    home_name=home,
                    away_name=away,
                    commence_time=game.get("commence_time", ""),
                    lam_home=round(lam_h, 3),
                    lam_away=round(lam_a, 3),
                    p_home=round(p_home, 4),
                    p_draw=round(p_draw, 4),
                    p_away=round(p_away, 4),
                    total_goals=round(total_goals, 3),
                    n_bookmakers=len(books),
                )
            )
        return out


def get_probability_provider() -> ProbabilityProvider | None:
    """Configured market provider, or None => engine uses its internal model."""
    if settings.odds_api_key:
        return OddsAPIProvider(settings.odds_api_key)
    return None

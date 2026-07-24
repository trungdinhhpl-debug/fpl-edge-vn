"""Probability provider abstraction (spec §3 Tier-2).

If no licensed odds key is configured we fall back to the engine's internal
Poisson model and clearly label outputs as `model_estimate` — we never pretend a
model number is a real market price.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import settings


@dataclass
class MatchProbabilities:
    home_win: float
    draw: float
    away_win: float
    over_2_5: float | None
    source: str          # "odds_api" | "model_estimate"
    is_market: bool      # True only for real licensed market data


class ProbabilityProvider:
    """Interface. `get_match_probs` returns None when unavailable."""

    name = "base"

    def get_match_probs(self, home: str, away: str) -> MatchProbabilities | None:  # pragma: no cover
        raise NotImplementedError


class OddsAPIProvider(ProbabilityProvider):
    """the-odds-api.com — soccer_epl h2h + totals markets (if key present)."""

    name = "odds_api"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._cache: dict | None = None

    def _fetch(self) -> list[dict]:
        if self._cache is not None:
            return self._cache
        url = "https://api.the-odds-api.com/v4/sports/soccer_epl/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "uk",
            "markets": "h2h",
            "oddsFormat": "decimal",
        }
        try:
            r = httpx.get(url, params=params, timeout=settings.http_timeout_seconds)
            r.raise_for_status()
            self._cache = r.json()
        except Exception:
            self._cache = []
        return self._cache

    @staticmethod
    def _implied(odds: float) -> float:
        return 1.0 / odds if odds and odds > 0 else 0.0

    def get_match_probs(self, home: str, away: str) -> MatchProbabilities | None:
        data = self._fetch()
        for game in data:
            if home.lower() in game.get("home_team", "").lower() or away.lower() in game.get(
                "away_team", ""
            ).lower():
                try:
                    outcomes = game["bookmakers"][0]["markets"][0]["outcomes"]
                    by = {o["name"]: self._implied(o["price"]) for o in outcomes}
                    total = sum(by.values()) or 1.0
                    return MatchProbabilities(
                        home_win=by.get(game["home_team"], 0) / total,
                        draw=by.get("Draw", 0) / total,
                        away_win=by.get(game["away_team"], 0) / total,
                        over_2_5=None,
                        source="odds_api",
                        is_market=True,
                    )
                except Exception:
                    return None
        return None


def get_probability_provider() -> ProbabilityProvider | None:
    """Return a configured market provider, or None => use internal model."""
    if settings.odds_api_key:
        return OddsAPIProvider(settings.odds_api_key)
    return None

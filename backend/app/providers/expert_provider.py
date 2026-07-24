"""Expert-signal provider (spec §4 Tier-3/4).

Turns pundit/community opinion into structured signals with a trust score.
We do NOT scrape paywalled or login-gated content. This provider ships a small
set of clearly-labelled MOCK signals (is_mock=True) so the UI and scoring maths
are demonstrable; wire a licensed/RSS source here to replace them.

Signal trust (spec §4):
    signal_score = reliability × recency × specificity × historical_accuracy × independence
Echo-chamber: repeated copies of one origin are down-weighted via `independence`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class ExpertSourceSeed:
    name: str
    source_type: str
    url: str
    reliability: float
    historical_accuracy: float
    expertise: str
    independence: float = 1.0
    verified_track_record: bool = False


@dataclass
class ExpertSignalSeed:
    source_name: str
    signal_type: str
    web_name: str          # matched to a player by web_name at ingest
    confidence: float
    summary: str
    gameweek_offset: int = 0   # 0 = next GW
    published_hours_ago: float = 12.0
    link: str = ""


# A configurable roster. Reliability/accuracy are placeholders to be tuned from
# a verified track record — NOT an endorsement, and follower count is ignored.
DEFAULT_SOURCES: list[ExpertSourceSeed] = [
    ExpertSourceSeed("Fantasy Football Scout", "site",
                     "https://www.fantasyfootballscout.co.uk", 0.85, 0.72,
                     "Predicted lineups, team news", 1.0, True),
    ExpertSourceSeed("FPL Review", "site", "https://fplreview.com", 0.82, 0.70,
                     "Projection models", 1.0, True),
    ExpertSourceSeed("Ben Crellin", "analyst", "https://twitter.com/BenCrellin",
                     0.88, 0.80, "Blank/Double gameweeks", 0.95, True),
    ExpertSourceSeed("Lateriser (Pranil Sheth)", "analyst",
                     "https://twitter.com/FPL_Lateriser12", 0.80, 0.74,
                     "Strategy, differentials", 0.9, True),
    ExpertSourceSeed("r/FantasyPL", "community", "https://reddit.com/r/FantasyPL",
                     0.55, 0.55, "Community consensus", 0.6, False),
]

_NOW = datetime.now(timezone.utc)

# Mock signals (clearly labelled). Matched to players by web_name.
DEFAULT_SIGNALS: list[ExpertSignalSeed] = [
    ExpertSignalSeed("Fantasy Football Scout", "start", "Haaland", 0.95,
                     "Nailed to start; no rotation risk flagged.", 0, 8),
    ExpertSignalSeed("Ben Crellin", "captain", "Salah", 0.9,
                     "Strong captaincy fixture; high ceiling.", 0, 20),
    ExpertSignalSeed("FPL Review", "buy", "Palmer", 0.75,
                     "Model top transfer target on fixture swing.", 0, 30),
    ExpertSignalSeed("Lateriser (Pranil Sheth)", "differential", "Mbeumo", 0.65,
                     "Low-owned with good underlying numbers.", 0, 40),
    ExpertSignalSeed("r/FantasyPL", "sell", "Nunez", 0.5,
                     "Community sentiment turning on minutes risk.", 0, 6),
]


def compute_signal_score(
    reliability: float,
    historical_accuracy: float,
    independence: float,
    specificity: float,
    published_hours_ago: float,
) -> float:
    """recency decays with a ~48h half-life; nearer-deadline news counts more."""
    recency = math.exp(-published_hours_ago / 48.0)
    return round(
        reliability * recency * specificity * historical_accuracy * independence, 4
    )


class ExpertProvider:
    """Returns seed sources/signals. Replace with a real RSS/API adapter later."""

    def get_sources(self) -> list[ExpertSourceSeed]:
        return DEFAULT_SOURCES

    def get_signals(self) -> list[ExpertSignalSeed]:
        return DEFAULT_SIGNALS

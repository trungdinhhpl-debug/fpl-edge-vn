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
    # Identifier of the PRIMARY statement this traces back to (a presser, a club
    # post). Signals sharing one are echoes of a single source, not agreement.
    origin_ref: str | None = None


# Registry of real, publicly-followed sources — WHO exists and what they cover.
#
# `historical_accuracy` is deliberately 0.0 and `verified_track_record` False for
# every one of them. These are real, identifiable people and organisations, and
# asserting an accuracy figure we have not measured would attach an invented
# performance claim to a named person. Accuracy is EARNED here: it is computed
# from predictions this project recorded and later scored (ExpertTrackRecord).
# Until a source has a scored sample, the UI shows "chưa đủ dữ liệu", not a number.
#
# `reliability` is a prior on the KIND of outlet (an editorial site with a
# corrections process vs an anonymous forum), not a claim about any individual's
# hit rate. Follower count is ignored entirely.
DEFAULT_SOURCES: list[ExpertSourceSeed] = [
    ExpertSourceSeed("Fantasy Football Scout", "site",
                     "https://www.fantasyfootballscout.co.uk", 0.75, 0.0,
                     "lineup,injury", 1.0, False),
    ExpertSourceSeed("FPL Review", "site", "https://fplreview.com", 0.75, 0.0,
                     "statistics,captaincy", 1.0, False),
    ExpertSourceSeed("Ben Crellin", "analyst", "https://twitter.com/BenCrellin",
                     0.70, 0.0, "chip_planning", 0.95, False),
    ExpertSourceSeed("Lateriser (Pranil Sheth)", "analyst",
                     "https://twitter.com/FPL_Lateriser12", 0.70, 0.0,
                     "chip_planning,captaincy", 0.9, False),
    ExpertSourceSeed("r/FantasyPL", "community", "https://reddit.com/r/FantasyPL",
                     0.45, 0.0, "captaincy", 0.6, False),
    # Synthetic sources for the demo signals below. Real people never get
    # invented statements attached to them, so the demo needs its own cast.
    ExpertSourceSeed("Nguồn demo A", "site", None, 0.70, 0.0, "lineup", 1.0, False),
    ExpertSourceSeed("Nguồn demo B", "analyst", None, 0.65, 0.0, "lineup,statistics", 1.0, False),
    ExpertSourceSeed("Nguồn demo C", "community", None, 0.45, 0.0, "captaincy", 0.7, False),
    ExpertSourceSeed("Nguồn demo D", "analyst", None, 0.68, 0.0, "captaincy", 1.0, False),
    ExpertSourceSeed("Nguồn demo E", "community", None, 0.42, 0.0, "statistics", 0.8, False),
]

_NOW = datetime.now(timezone.utc)

# Demo signals for the UI and the echo-detection maths.
#
# Attributed to obviously-synthetic names on purpose: the previous seed put
# invented quotes in the mouths of real, named analysts ("Ben Crellin: captain
# Salah"), which is a fabricated statement attributed to a real person. Demo
# data must never do that. Wire a licensed/RSS adapter to replace these.
#
# `origin_ref` is what makes echo detection possible: signals tracing back to the
# same primary statement share one, so eight posts about one presser quote count
# as one independent source, not eight.
DEFAULT_SIGNALS: list[ExpertSignalSeed] = [
    ExpertSignalSeed("Nguồn demo A", "start", "Haaland", 0.95,
                     "[DEMO] Chắc suất đá chính, không thấy dấu hiệu xoay tua.",
                     0, 8, origin_ref="demo:presser:MCI:gw1"),
    ExpertSignalSeed("Nguồn demo B", "start", "Haaland", 0.9,
                     "[DEMO] Dẫn lại phát biểu họp báo của HLV.",
                     0, 7, origin_ref="demo:presser:MCI:gw1"),
    ExpertSignalSeed("Nguồn demo C", "start", "Haaland", 0.88,
                     "[DEMO] Cũng dẫn lại đúng phát biểu đó.",
                     0, 6, origin_ref="demo:presser:MCI:gw1"),
    ExpertSignalSeed("Nguồn demo D", "captain", "Haaland", 0.8,
                     "[DEMO] Phân tích riêng, không dẫn nguồn khác.", 0, 20),
    ExpertSignalSeed("Nguồn demo E", "avoid", "Haaland", 0.55,
                     "[DEMO] Ý kiến trái chiều: lịch khó, giá quá cao.", 0, 10),
    ExpertSignalSeed("Nguồn demo B", "buy", "Palmer", 0.75,
                     "[DEMO] Mục tiêu chuyển nhượng theo mô hình.", 0, 30),
    ExpertSignalSeed("Nguồn demo C", "sell", "Palmer", 0.6,
                     "[DEMO] Ý kiến trái chiều về Palmer.", 0, 12),
]


def compute_signal_score(
    reliability: float,
    historical_accuracy: float,
    independence: float,
    specificity: float,
    published_hours_ago: float,
) -> float:
    """recency decays with a ~48h half-life; nearer-deadline news counts more.

    An UNMEASURED accuracy (0.0) is treated as neutral, not as a measured zero.
    Sources now start at 0.0 until they have earned a score, and multiplying by
    that would drive every signal score to 0 — silently collapsing the ordering
    in the player detail view. "We have not measured this" and "this source is
    always wrong" must not produce the same number.
    """
    recency = math.exp(-published_hours_ago / 48.0)
    accuracy = historical_accuracy if historical_accuracy > 0 else 1.0
    return round(reliability * recency * specificity * accuracy * independence, 4)


class ExpertProvider:
    """Returns seed sources/signals. Replace with a real RSS/API adapter later."""

    def get_sources(self) -> list[ExpertSourceSeed]:
        return DEFAULT_SOURCES

    def get_signals(self) -> list[ExpertSignalSeed]:
        return DEFAULT_SIGNALS

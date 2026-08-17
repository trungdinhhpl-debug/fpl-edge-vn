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
]

# Không còn tín hiệu nào được ghi cứng ở đây.
#
# Bản trước có một dàn "Nguồn demo A–E" với các phát biểu đóng dấu `[DEMO]`. Chúng
# tồn tại để phần toán đồng thuận có gì mà chạy, nhưng chúng không nói được điều gì
# về bóng đá, và một trang tên là "Chuyên gia" chạy bằng dữ liệu bịa thì tệ hơn là
# một trang trống — nó dạy người đọc tin vào một thứ không có thật.
#
# Tín hiệu giờ đến từ `app/providers/fpl_experts.py`, lấy từ chính API công khai
# của FPL. Danh sách các toà soạn/nhà phân tích có thật ở trên được giữ lại như một
# ĐĂNG BẠ — họ tồn tại, và chỗ cắm nguồn có bản quyền nằm ở đó — nhưng chừng nào
# chưa nối được nguồn thì họ không phát ra tín hiệu nào.
DEFAULT_SIGNALS: list[ExpertSignalSeed] = []


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
    """Đăng bạ nguồn + tín hiệu THẬT từ API công khai của FPL.

    `DEFAULT_SOURCES` là danh sách các toà soạn/nhà phân tích có thật — họ tồn tại
    và đây là chỗ cắm một nguồn có bản quyền — nhưng chừng nào chưa nối được thì họ
    không phát tín hiệu nào. Tín hiệu đang chạy đến từ `providers/fpl_experts.py`.
    """

    def get_sources(self) -> list[ExpertSourceSeed]:
        from app.providers.fpl_experts import real_sources

        return DEFAULT_SOURCES + real_sources()

    def get_signals(self) -> list[ExpertSignalSeed]:
        """Rỗng — tín hiệu thật cần dữ liệu trực tiếp, xem `fpl_sync.sync_experts`."""
        return DEFAULT_SIGNALS

"""Số quả 11m THỰC TẾ từng cầu thủ đã đá, từ API công khai của Premier League.

**Vì sao cần.** FPL phát `expected_goals` là xG TỔNG — gồm cả 11m — và không phát
số quả 11m đã đá. `app/engine/penalties.py` vì thế chỉ trừ được một TỶ LỆ trung
bình khỏi xG của người được xếp đá 11m. Tỷ lệ trung bình sai nặng ở cấp cá nhân,
và sai theo một hướng: người đá NHIỀU hơn mức đó bị thổi phồng xG bóng sống. Đo
được ngày 2026-10-20:

    Le Fée (SUN, được FPL xếp đá 11m SỐ 2): 2 quả mùa này = 1.58/2.09 xG,
    4 quả mùa trước = 3.16/5.22 xG. Engine trừ ~0.05/90 nên coi ~0.20/90 là bóng
    sống, trong khi thực tế là ~0.07/90 — gấp gần 3 lần.

**Đếm thế nào.** Bốn chỉ số xếp hạng, mỗi quả 11m rơi vào đúng một:
`att_pen_goal` (vào), `att_pen_target` (trúng đích nhưng bị cứu), `att_pen_miss`
(ra ngoài), `att_pen_post` (dội cột). Tổng bốn cái = số quả đã đá. Đối chiếu với
FPL mùa 2026/27 tới vòng 7: FPL đếm 3 quả hỏng, nguồn này 2 cứu + 1 dội cột = 3.

**Khớp cầu thủ bằng mã Opta, không bằng tên.** `altIds.opta` của nguồn là
`"p" + Player.code` của FPL (Le Fée: p484420 ↔ 484420). Khớp tên là thứ đã phải
né ở `pl_positions` vì trùng họ trong giải; ở đây không cần.

Điều khoản và nguồn: xem `app/providers/pl_positions.py` — cùng API, cùng điều
kiện. Bật/tắt riêng bằng `PL_PENALTIES_ENABLED`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.config import settings
from app.providers.pl_positions import (
    _HEADERS,
    BASE,
    PL_COMPETITION_ID,
    SOURCE_NAME,
    SOURCE_URL,
    season_label,
)

__all__ = [
    "PENALTY_OUTCOME_STATS",
    "PenaltyFetchReport",
    "PLPenaltiesProvider",
    "SOURCE_NAME",
    "SOURCE_URL",
    "fetch_penalties",
    "get_pl_penalties_provider",
]

# Mỗi quả 11m rơi vào đúng MỘT trong bốn chỉ số này — cộng lại là số quả đã đá.
# Chỉ số đầu là số bàn, giữ riêng để báo cáo tỷ lệ vào.
PENALTY_OUTCOME_STATS = ("att_pen_goal", "att_pen_target", "att_pen_miss", "att_pen_post")

# Bảng xếp hạng phân trang; một mùa có ~40 người đá 11m nên một trang là đủ, vòng
# lặp chỉ để không âm thầm cắt cụt nếu có mùa nhiều hơn.
_PAGE_SIZE = 100
_MAX_PAGES = 10


@dataclass
class PenaltyFetchReport:
    """Kết quả một lần lấy. `by_season[mùa][mã opta] = (số quả đã đá, số bàn)`.

    Một mùa CÓ mặt trong `by_season` nghĩa là đã lấy trọn cả bốn chỉ số của mùa đó —
    nên cầu thủ vắng mặt trong đó là đá 0 quả, không phải "chưa biết". Mùa lấy
    hỏng thì KHÔNG có mặt, để phía ghi không biến "không biết" thành số 0.
    """

    by_season: dict[str, dict[int, tuple[int, int]]] = field(default_factory=dict)
    season_ids: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    detail: str = ""


class PLPenaltiesProvider:
    name = "pl_penalties"

    def __init__(self, timeout: float | None = None) -> None:
        self.timeout = timeout or settings.http_timeout_seconds

    def _get(self, path: str, params: dict) -> dict:
        r = httpx.get(
            f"{BASE}/{path}", params=params, headers=_HEADERS, timeout=self.timeout
        )
        r.raise_for_status()
        return r.json()

    def seasons(self) -> dict[str, int]:
        """{'2026/27': 841, '2025/26': 777, …} — nhãn đã rút về quy ước của dự án."""
        data = self._get(
            f"competitions/{PL_COMPETITION_ID}/compseasons",
            {"page": 0, "pageSize": 10},
        )
        out: dict[str, int] = {}
        for s in data.get("content") or []:
            label = season_label(s.get("label"))
            try:
                sid = int(float(s.get("id")))
            except (TypeError, ValueError):
                continue
            if label and label not in out:
                out[label] = sid
        return out

    def ranked(self, stat: str, season_id: int) -> dict[int, int]:
        """{mã opta (số): giá trị} của một chỉ số xếp hạng trong một mùa."""
        out: dict[int, int] = {}
        for page in range(_MAX_PAGES):
            data = self._get(
                f"stats/ranked/players/{stat}",
                {
                    "compSeasons": season_id, "comps": PL_COMPETITION_ID,
                    "page": page, "pageSize": _PAGE_SIZE, "altIds": "true",
                },
            )
            block = data.get("stats") or {}
            for row in block.get("content") or []:
                opta = ((row.get("owner") or {}).get("altIds") or {}).get("opta")
                code = _opta_to_code(opta)
                if code is None:
                    continue
                out[code] = out.get(code, 0) + int(round(float(row.get("value") or 0)))
            info = block.get("pageInfo") or {}
            if page >= int(info.get("numPages", 1) or 1) - 1:
                break
        return out


def _opta_to_code(opta: str | None) -> int | None:
    """`"p484420"` -> 484420 (đúng bằng `Player.code` của FPL)."""
    if not opta or not isinstance(opta, str):
        return None
    digits = opta[1:] if opta[:1].lower() == "p" else opta
    return int(digits) if digits.isdigit() else None


def fetch_penalties(
    seasons: list[str], provider: PLPenaltiesProvider | None = None
) -> PenaltyFetchReport:
    """Số quả 11m đã đá của MỌI cầu thủ trong từng mùa được hỏi. Lỗi -> báo cáo.

    Mỗi mùa thành công hay thất bại TRỌN VẸN: thiếu một trong bốn chỉ số thì tổng
    sẽ hụt (vd mất `att_pen_goal` là mất phần lớn số quả), nên mùa đó bị bỏ hẳn
    thay vì ghi một con số thiếu trông như thật.
    """
    provider = provider or PLPenaltiesProvider()
    report = PenaltyFetchReport()
    try:
        ids = provider.seasons()
    except Exception as exc:                      # noqa: BLE001
        report.errors.append(f"danh sách mùa: {str(exc)[:160]}")
        report.detail = "Không lấy được danh sách mùa từ API Premier League"
        return report

    parts: list[str] = []
    for season in seasons:
        sid = ids.get(season)
        if sid is None:
            report.errors.append(f"{season}: API không có mùa này")
            continue
        try:
            per_stat = {stat: provider.ranked(stat, sid) for stat in PENALTY_OUTCOME_STATS}
        except Exception as exc:                  # noqa: BLE001
            report.errors.append(f"{season}: {str(exc)[:160]}")
            continue
        merged: dict[int, tuple[int, int]] = {}
        for stat, values in per_stat.items():
            for code, n in values.items():
                taken, scored = merged.get(code, (0, 0))
                merged[code] = (
                    taken + n,
                    scored + (n if stat == "att_pen_goal" else 0),
                )
        report.by_season[season] = merged
        report.season_ids[season] = sid
        n_taken = sum(t for t, _ in merged.values())
        n_scored = sum(g for _, g in merged.values())
        parts.append(
            f"{season}: {n_taken} quả 11m ({n_scored} vào) của {len(merged)} cầu thủ"
        )
    report.detail = " · ".join(parts) if parts else "Không lấy được mùa nào"
    return report


def get_pl_penalties_provider() -> PLPenaltiesProvider | None:
    """Provider đã bật, hoặc None => engine trừ 11m theo tỷ lệ trung bình như cũ."""
    if not settings.pl_penalties_enabled:
        return None
    return PLPenaltiesProvider()

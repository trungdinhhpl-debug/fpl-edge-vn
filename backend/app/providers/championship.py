"""Dữ liệu Championship mùa trước cho các đội mới lên hạng (tuỳ chọn, miễn phí).

Nguồn: football-data.co.uk — cung cấp file CSV kết quả các giải Anh để tải tự do
(robots.txt cho phép mọi crawler). Chỉ lấy đúng một file/mùa, có cache trong DB.

MỤC ĐÍCH GIỚI HẠN: KHÔNG quy đổi bàn thắng Championship thành bàn thắng Ngoại
hạng (điều đó cần một hệ số quy đổi tự nó cũng là phỏng đoán). Ở đây chỉ dùng để
**xếp hạng ba đội mới lên hạng so với nhau**, rồi neo vào mức nền dành cho đội
mới lên hạng. Nhờ vậy đội vô địch Championship không bị chấm ngang đội thắng
play-off, nhưng cả ba vẫn nằm dưới mức trung bình Ngoại hạng.

Tắt bằng CHAMPIONSHIP_ENABLED=false.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass

import httpx

from app.config import settings

BASE_URL = "https://www.football-data.co.uk/mmz4281"
SOURCE_NAME = "football-data.co.uk (Championship)"
DIVISION = "E1"  # E0 = Premier League, E1 = Championship


@dataclass
class ChampTeamStats:
    name: str
    played: int
    goals_for: int
    goals_against: int

    @property
    def gf_per_game(self) -> float:
        return self.goals_for / self.played if self.played else 0.0

    @property
    def ga_per_game(self) -> float:
        return self.goals_against / self.played if self.played else 0.0


def season_code(pl_start_year: int) -> str:
    """Mùa Championship ngay trước mùa Ngoại hạng bắt đầu năm `pl_start_year`.

    Ví dụ Ngoại hạng 2026/27 -> Championship 2025/26 -> mã "2526".
    """
    prev_start = pl_start_year - 1
    return f"{prev_start % 100:02d}{pl_start_year % 100:02d}"


def fetch_championship_table(pl_start_year: int) -> tuple[list[ChampTeamStats], str]:
    """Tải kết quả cả mùa rồi tổng hợp thành bảng bàn thắng/bàn thua mỗi đội."""
    code = season_code(pl_start_year)
    url = f"{BASE_URL}/{code}/{DIVISION}.csv"
    resp = httpx.get(
        url,
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": "FPL-Edge-VN/0.1 (independent fan project)"},
        follow_redirects=True,
    )
    resp.raise_for_status()

    agg: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # played, gf, ga
    for row in csv.DictReader(io.StringIO(resp.text)):
        home, away = (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip()
        hg, ag = row.get("FTHG"), row.get("FTAG")
        if not home or not away or hg in (None, "") or ag in (None, ""):
            continue
        try:
            hg_i, ag_i = int(hg), int(ag)
        except ValueError:
            continue
        agg[home][0] += 1
        agg[home][1] += hg_i
        agg[home][2] += ag_i
        agg[away][0] += 1
        agg[away][1] += ag_i
        agg[away][2] += hg_i

    stats = [
        ChampTeamStats(name=n, played=v[0], goals_for=v[1], goals_against=v[2])
        for n, v in agg.items()
        if v[0] >= 10  # bỏ dữ liệu quá mỏng
    ]
    return stats, url


def league_averages(stats: list[ChampTeamStats]) -> tuple[float, float]:
    """(bàn thắng TB mỗi trận, bàn thua TB mỗi trận) của cả giải."""
    total_games = sum(s.played for s in stats)
    if not total_games:
        return 1.3, 1.3
    gf = sum(s.goals_for for s in stats) / total_games
    ga = sum(s.goals_against for s in stats) / total_games
    return gf, ga

"""Vai trò chiến thuật từ **API công khai của chính Premier League**.

Đây là nguồn vai trò duy nhất tìm được vừa đủ chi tiết vừa lấy được bằng máy (khảo
sát 2026-09-09, xem ghi chú cuối tệp). Endpoint không cần khoá, chỉ cần header
`Origin`::

    GET /football/competitions/1/compseasons            -> id mùa
    GET /football/teams?compSeasons=<id>&comps=1        -> 20 CLB Ngoại hạng
    GET /football/players?compSeasons=<id>&page=N       -> info.positionInfo

**Phần khó nằm ở nhãn, không ở HTTP.** Premier League phát ra những chuỗi kiểu
`"Left Full Back"`, `"Centre Central Defender"`, nhưng cũng có `"Left/Right Winger"`
và `"Left/Centre/Right Attacking Midfielder"`. Quy tắc ở đây:

* Tách nhãn thành **(các cánh, vai trò gốc)**: `"Left/Centre Central Defender"` ->
  cánh {Left, Centre}, gốc "Central Defender".
* Vai trò gốc **không phụ thuộc cánh** (trung vệ, tiền vệ, tiền đạo) thì cánh ghép
  chẳng sao cả — trục của nó là trung lộ/bóng chết dù đá bên nào.
* Vai trò gốc **phụ thuộc cánh** (winger, full back, wing back) mà cánh lại **mơ hồ**
  (`Left/Right`, hoặc không có cánh nào) thì trả về **`None`** kèm nhãn gốc. Ép một
  người đá cả hai biên vào một biên là đúng thứ đặc tả cấm: bịa ra dữ liệu không có.
* Bốn nhãn thô `Goalkeeper`/`Defender`/`Midfielder`/`Forward` (khi đứng một mình)
  cũng trả `None` — chúng **đúng bằng** `element_type` của FPL, nên dùng chúng chính
  là "suy vai trò từ nhãn GK/DEF/MID/FWD", điều `app/engine/roles.py` cấm.

Số đo trên 452 cầu thủ của 20 CLB mùa 2026/27: 86% có nhãn chi tiết hơn bốn nhãn
thô, **32% phân biệt được trái/phải**. Con số 32% nghe thấp nhưng nó tập trung đúng
vào nhóm cần: hậu vệ biên và tiền vệ cánh. Trung vệ, tiền đạo, tiền vệ trung tâm
không cần biết trái/phải vì trục của họ không theo biên.

**Điều khoản:** `robots.txt` của premierleague.com cho phép crawl. Điều khoản sử
dụng giới hạn ở "private and personal use" và nói phát hành lại cần chấp thuận bằng
văn bản. Người vận hành đã quyết định BẬT nguồn này (`PL_POSITIONS_ENABLED=true`,
2026-09-09); đổi lại, mọi chỗ hiển thị vai trò đều kèm tên nguồn — `source_name` được
ghi xuống từng hàng `player_roles` chính là để phần ghi nguồn đó không thể rơi rụng.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from app.config import settings

BASE = "https://footballapi.pulselive.com/football"
SOURCE_NAME = "Premier League (footballapi.pulselive.com)"
SOURCE_URL = "https://www.premierleague.com/"
PL_COMPETITION_ID = 1

_HEADERS = {
    # Endpoint từ chối request không có Origin của chính trang họ.
    "Origin": "https://www.premierleague.com",
    "Referer": "https://www.premierleague.com/",
    "Accept": "application/json",
}

# --------------------------------------------------------- phân tích nhãn ----
# Vai trò gốc -> (mã khi ở cánh trái, mã khi ở cánh phải, mã khi không theo cánh).
# `None` ở ô "không theo cánh" nghĩa là vai trò này BẮT BUỘC phải biết cánh.
_BASE_ROLES: list[tuple[str, tuple[str | None, str | None, str | None]]] = [
    # Chuỗi dài phải đứng trước chuỗi ngắn: "Central Defender" trước "Defender",
    # "Attacking Midfielder" trước "Midfielder", nếu không sẽ khớp nhầm phần đuôi.
    ("goalkeeper", (None, None, "GK")),
    ("central defender", (None, None, "CB")),
    ("centre back", (None, None, "CB")),
    ("centre-back", (None, None, "CB")),
    ("wing back", ("LWB", "RWB", None)),
    ("full back", ("LB", "RB", None)),
    ("defensive midfielder", (None, None, "DM")),
    ("attacking midfielder", (None, None, "AM")),
    ("central midfielder", (None, None, "CM")),
    ("wide midfielder", ("LW", "RW", None)),
    # "Second Striker" đá lùi và thiên về kiến tạo hơn trung phong cắm, nên xếp vào
    # AM (phần dứt điểm 0.70) chứ không phải ST (1.00). Chọn phía THẬN TRỌNG: đoán
    # thấp làm hệ số gần 1.0 hơn, đoán cao thì thổi phồng.
    ("second striker", (None, None, "AM")),
    ("striker", (None, None, "ST")),
    ("winger", ("LW", "RW", None)),
]

# Bốn nhãn thô — đúng bằng element_type của FPL, nên KHÔNG được dùng.
_COARSE = {"goalkeeper", "defender", "midfielder", "forward", "attacker", "defence"}

_SIDE_WORDS = {"left", "right", "centre", "center", "central"}


@dataclass
class ParsedRole:
    """Kết quả đọc một nhãn `positionInfo`."""

    role: str | None
    raw_label: str
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.role is not None


def parse_position_info(label: str | None) -> ParsedRole:
    """`"Left Full Back"` -> LB. `"Left/Right Winger"` -> None (đá cả hai biên).

    Trả `role=None` kèm lý do đọc được, thay vì im lặng bỏ qua — giao diện cần nói
    được VÌ SAO một cầu thủ không có vai trò.
    """
    raw = (label or "").strip()
    if not raw:
        return ParsedRole(None, raw, "Premier League không công bố vị trí chi tiết")

    norm = re.sub(r"\s+", " ", raw.lower())
    if norm in _COARSE:
        return ParsedRole(
            None, raw,
            f"“{raw}” chỉ là nhãn thô, không hơn gì nhãn vị trí sẵn có của FPL",
        )

    base_key = base_codes = None
    for key, codes in _BASE_ROLES:
        if norm.endswith(key):
            base_key, base_codes = key, codes
            break
    if base_codes is None:
        return ParsedRole(None, raw, f"Chưa biết cách đọc nhãn “{raw}”")

    prefix = norm[: len(norm) - len(base_key)].strip()
    # "Left/Centre/Right" -> {"left","centre","right"}; bỏ những từ không phải cánh.
    sides = {
        w for w in re.split(r"[/\s]+", prefix) if w in _SIDE_WORDS
    }
    left_code, right_code, neutral_code = base_codes

    if neutral_code is not None:
        # Vai trò không phụ thuộc cánh: trục của nó là trung lộ hoặc bóng chết, nên
        # nhãn ghép nhiều cánh vẫn dùng được nguyên vẹn.
        return ParsedRole(neutral_code, raw)

    has_left = "left" in sides
    has_right = "right" in sides
    if has_left and not has_right:
        return ParsedRole(left_code, raw)
    if has_right and not has_left:
        return ParsedRole(right_code, raw)
    if has_left and has_right:
        return ParsedRole(
            None, raw,
            f"“{raw}” đá cả hai biên — không xác định được cánh nào, để trống thay "
            "vì chọn bừa một bên",
        )
    return ParsedRole(
        None, raw, f"“{raw}” không nói rõ biên trái hay phải",
    )


# Độ dài tối đa của `player_roles.season_label`. Ghi ra đây vì Postgres SIẾT
# `varchar(n)` còn SQLite thì bỏ qua — nghĩa là một nhãn quá dài chạy ngon trên máy
# và làm hỏng job đồng bộ trên Neon, đúng như đã xảy ra: nhãn thật của API là
# "English Premier League Season 2026/2027" (39 ký tự) và cột chỉ có 32.
SEASON_LABEL_MAX = 32


def season_label(raw: str | None) -> str | None:
    """Rút nhãn mùa của API về dạng ngắn, đúng quy ước dự án: `2026/27`.

    Tên giải trong nhãn là thừa (module này chỉ hỏi Ngoại hạng), và bỏ nó đi vừa cho
    dữ liệu gọn hơn vừa loại luôn khả năng tràn cột. Không rút được thì cắt cứng —
    một nhãn xấu vẫn tốt hơn một job đồng bộ chết.
    """
    if not raw:
        return None
    m = re.search(r"(\d{4})\s*/\s*(\d{2,4})", raw)
    if m:
        return f"{m.group(1)}/{m.group(2)[-2:]}"
    return raw.strip()[:SEASON_LABEL_MAX]


# ------------------------------------------------------------- provider ------
@dataclass
class PlayerRoleRecord:
    """Một cầu thủ Premier League kèm vai trò đã đọc được."""

    display_name: str
    club_name: str
    role: str | None
    raw_label: str
    reason: str = ""


@dataclass
class RoleFetchReport:
    records: list[PlayerRoleRecord] = field(default_factory=list)
    season_id: int | None = None
    season_label: str | None = None
    n_clubs: int = 0
    n_api_players: int = 0
    errors: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def usable(self) -> list[PlayerRoleRecord]:
        return [r for r in self.records if r.role]


class PLPositionsProvider:
    """Đọc `info.positionInfo` của API Premier League."""

    name = "pl_positions"

    def __init__(self, timeout: float | None = None) -> None:
        self.timeout = timeout or settings.http_timeout_seconds

    def _get(self, path: str, params: dict) -> dict:
        r = httpx.get(
            f"{BASE}/{path}", params=params, headers=_HEADERS, timeout=self.timeout
        )
        r.raise_for_status()
        return r.json()

    def current_season(self) -> tuple[int | None, str | None]:
        """(id, nhãn) của mùa mới nhất. API xếp mùa mới nhất lên đầu."""
        if settings.pl_positions_season_id:
            return settings.pl_positions_season_id, "đặt tay qua cấu hình"
        data = self._get(
            f"competitions/{PL_COMPETITION_ID}/compseasons",
            {"page": 0, "pageSize": 10},
        )
        content = data.get("content") or []
        if not content:
            return None, None
        top = content[0]
        label = season_label(top.get("label"))
        try:
            return int(float(top.get("id"))), label
        except (TypeError, ValueError):
            return None, label

    def clubs(self, season_id: int) -> set[str]:
        """Tên 20 CLB đang đá Ngoại hạng ở mùa này.

        Bắt buộc phải có: danh sách cầu thủ của endpoint kia gồm cả người đã sang
        giải khác (Rangers, Union Berlin…) và các đội U21.
        """
        data = self._get(
            "teams",
            {
                "pageSize": 100, "compSeasons": season_id,
                "comps": PL_COMPETITION_ID, "altIds": "true", "page": 0,
            },
        )
        return {t.get("name") for t in (data.get("content") or []) if t.get("name")}

    def players(self, season_id: int, max_pages: int = 30) -> list[dict]:
        out: list[dict] = []
        page = 0
        while page < max_pages:
            data = self._get(
                "players",
                {
                    "pageSize": 100, "compSeasons": season_id, "altIds": "true",
                    "page": page, "type": "player",
                },
            )
            content = data.get("content") or []
            out.extend(content)
            info = data.get("pageInfo") or {}
            if not content or page >= int(info.get("numPages", 1)) - 1:
                break
            page += 1
        return out


def fetch_roles(provider: PLPositionsProvider | None = None) -> RoleFetchReport:
    """Lấy vai trò cho cầu thủ của 20 CLB Ngoại hạng. Mọi lỗi -> báo cáo rỗng."""
    provider = provider or PLPositionsProvider()
    report = RoleFetchReport()
    try:
        season_id, season_label = provider.current_season()
    except Exception as exc:                      # noqa: BLE001
        report.errors.append(f"mùa giải: {str(exc)[:160]}")
        report.detail = "Không lấy được danh sách mùa từ API Premier League"
        return report
    if season_id is None:
        report.detail = "API Premier League không trả về mùa nào"
        return report
    report.season_id, report.season_label = season_id, season_label

    try:
        clubs = provider.clubs(season_id)
        raw_players = provider.players(season_id)
    except Exception as exc:                      # noqa: BLE001
        report.errors.append(str(exc)[:160])
        report.detail = "Không lấy được đội/cầu thủ từ API Premier League"
        return report

    report.n_clubs = len(clubs)
    report.n_api_players = len(raw_players)

    for p in raw_players:
        club = (p.get("currentTeam") or {}).get("name")
        # Lọc theo 20 CLB: danh sách gốc có cả cầu thủ đã rời giải và đội U21.
        if club not in clubs:
            continue
        name = (p.get("name") or {}).get("display") or ""
        if not name:
            continue
        parsed = parse_position_info((p.get("info") or {}).get("positionInfo"))
        report.records.append(PlayerRoleRecord(
            display_name=name, club_name=club, role=parsed.role,
            raw_label=parsed.raw_label, reason=parsed.reason,
        ))

    n_usable = len(report.usable)
    n_sided = sum(1 for r in report.usable if r.role in ("LW", "RW", "LB", "RB", "LWB", "RWB"))
    report.detail = (
        f"mùa {season_label} ({season_id}) · {len(clubs)} CLB · "
        f"{len(report.records)} cầu thủ thuộc Ngoại hạng · {n_usable} đọc được vai "
        f"trò ({n_sided} phân biệt trái/phải)"
    )
    return report


def get_pl_positions_provider() -> PLPositionsProvider | None:
    """Provider đã bật, hoặc None => không gọi mạng, vai trò giữ nguyên như cũ."""
    if not settings.pl_positions_enabled:
        return None
    return PLPositionsProvider()

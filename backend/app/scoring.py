"""Luật FPL của mùa hiện tại — ĐỌC TỪ API, không ghi cứng.

FPL công bố toàn bộ luật trong `bootstrap-static.game_config`:
  - `scoring`  : điểm cho từng hạng mục, tách theo vị trí
  - `rules`    : cỡ đội, giới hạn mỗi CLB, ngân sách, số free transfer tối đa
  - `settings` : `static_content_url` chứa mã mùa (vd .../2026_27/)

Ingestion lưu nguyên văn vào bảng `seasons`, rồi `load_rules()` nạp vào hai
singleton `RULES` (điểm) và `GAME` (luật đội hình). Các module engine giữ tham
chiếu tới chính hai đối tượng này nên chỉ cần cập nhật tại chỗ là toàn hệ thống
dùng luật mới — không phải sửa code khi FPL đổi luật.

Giá trị mặc định bên dưới CHỈ là phương án dự phòng khi chưa đồng bộ được (chạy
offline / demo), và được đánh dấu `source = "fallback"`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

SCORING_SOURCE = "FPL bootstrap-static.game_config"
SCORING_HELP_URL = "https://fantasy.premierleague.com/help/rules"

# FPL dùng mã vị trí này trong game_config.scoring
_POS_KEY = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


@dataclass
class ScoringRules:
    """Điểm cho từng hạng mục. Không frozen: được cập nhật tại chỗ khi đồng bộ."""

    # ra sân
    points_play_under_60: int = 1
    points_play_60_plus: int = 2

    # theo vị trí (element_type: 1 GK, 2 DEF, 3 MID, 4 FWD)
    goal_points: dict[int, int] = field(default_factory=lambda: {1: 10, 2: 6, 3: 5, 4: 4})
    assist_points: int = 3
    clean_sheet_points: dict[int, int] = field(
        default_factory=lambda: {1: 4, 2: 4, 3: 1, 4: 0}
    )
    conceded_points: dict[int, int] = field(
        default_factory=lambda: {1: -1, 2: -1, 3: 0, 4: 0}
    )

    # thủ môn: FPL trả về điểm mỗi lần cứu thua = 1, nhưng SỐ LẦN cứu thua cần
    # cho 1 điểm (3) không có trong API -> giữ ở đây, xem SCORING_HELP_URL.
    saves_per_point: int = 3

    yellow_card_points: int = -1
    red_card_points: int = -3
    own_goal_points: int = -2
    penalty_miss_points: int = -2
    penalty_save_points: int = 5
    max_bonus: int = 3

    # Defensive Contribution: điểm lấy từ API; NGƯỠNG hành động thì không có
    # trong API nên giữ ở đây (hậu vệ 10 CBIT; tiền vệ/tiền đạo 12 CBIT + thu hồi)
    defcon_points_by_pos: dict[int, int] = field(
        default_factory=lambda: {1: 0, 2: 2, 3: 2, 4: 2}
    )
    defcon_threshold_def: int = 10
    defcon_threshold_att: int = 12

    source: str = "fallback"

    # ---- các thuộc tính tiện dụng, giữ tương thích với code engine ----
    @property
    def conceded_penalty_positions(self) -> tuple[int, ...]:
        return tuple(p for p, v in self.conceded_points.items() if v)

    @property
    def points_per_two_conceded(self) -> int:
        vals = [v for v in self.conceded_points.values() if v]
        return vals[0] if vals else -1

    @property
    def defcon_positions(self) -> tuple[int, ...]:
        return tuple(p for p, v in self.defcon_points_by_pos.items() if v)

    @property
    def defcon_points(self) -> int:
        vals = [v for v in self.defcon_points_by_pos.values() if v]
        return vals[0] if vals else 2


@dataclass
class GameRules:
    """Luật đội hình & chuyển nhượng."""

    squad_size: int = 15
    squad_play: int = 11
    team_limit: int = 3
    total_spend: int = 1000          # phần mười triệu
    max_free_transfers: int = 5      # 1 + max_extra_free_transfers
    sell_on_fee: float = 0.5
    transfers_cap: int = 20
    source: str = "fallback"


RULES = ScoringRules()
GAME = GameRules()

# Mùa giải & phiên bản luật hiện hành (được set khi nạp từ DB/API)
SEASON: str = "—"
RULES_VERSION: str = "fallback"


# --------------------------------------------------------------- phân tích ----
def season_from_config(game_config: dict) -> str | None:
    """Lấy tên mùa từ static_content_url, vd '.../2026_27/' -> '2026/27'."""
    url = (game_config.get("settings") or {}).get("static_content_url") or ""
    import re

    m = re.search(r"/(\d{4})_(\d{2})/", url)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def rules_hash(game_config: dict) -> str:
    """Vân tay của luật — đổi khi và chỉ khi FPL đổi luật."""
    payload = {
        "scoring": game_config.get("scoring"),
        "rules": game_config.get("rules"),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:10]


def scoring_from_config(game_config: dict) -> ScoringRules:
    """Dựng ScoringRules từ game_config; thiếu trường nào thì giữ mặc định."""
    sc = game_config.get("scoring") or {}
    r = ScoringRules(source=SCORING_SOURCE)

    def by_pos(key: str, default: dict[int, int]) -> dict[int, int]:
        raw = sc.get(key)
        if not isinstance(raw, dict):
            return default
        return {p: int(raw.get(name, default[p])) for p, name in _POS_KEY.items()}

    r.points_play_under_60 = int(sc.get("short_play", r.points_play_under_60))
    r.points_play_60_plus = int(sc.get("long_play", r.points_play_60_plus))
    r.goal_points = by_pos("goals_scored", r.goal_points)
    r.assist_points = int(sc.get("assists", r.assist_points))
    r.clean_sheet_points = by_pos("clean_sheets", r.clean_sheet_points)
    r.conceded_points = by_pos("goals_conceded", r.conceded_points)
    r.yellow_card_points = int(sc.get("yellow_cards", r.yellow_card_points))
    r.red_card_points = int(sc.get("red_cards", r.red_card_points))
    r.own_goal_points = int(sc.get("own_goals", r.own_goal_points))
    r.penalty_miss_points = int(sc.get("penalties_missed", r.penalty_miss_points))
    r.penalty_save_points = int(sc.get("penalties_saved", r.penalty_save_points))
    r.defcon_points_by_pos = by_pos("defensive_contribution", r.defcon_points_by_pos)
    return r


def game_from_config(game_config: dict) -> GameRules:
    gr = game_config.get("rules") or {}
    g = GameRules(source=SCORING_SOURCE)
    g.squad_size = int(gr.get("squad_squadsize", g.squad_size))
    g.squad_play = int(gr.get("squad_squadplay", g.squad_play))
    g.team_limit = int(gr.get("squad_team_limit", g.team_limit))
    g.total_spend = int(gr.get("squad_total_spend", g.total_spend))
    # FPL nêu số free transfer CỘNG THÊM tối đa; tổng = 1 + số đó
    extra = gr.get("max_extra_free_transfers")
    if extra is not None:
        g.max_free_transfers = int(extra) + 1
    g.sell_on_fee = float(gr.get("transfers_sell_on_fee", g.sell_on_fee))
    g.transfers_cap = int(gr.get("transfers_cap", g.transfers_cap))
    return g


# ------------------------------------------------------------------ áp dụng ----
def apply_config(game_config: dict, season: str | None = None) -> dict:
    """Nạp luật vào hai singleton. Cập nhật TẠI CHỖ để mọi module đang giữ tham
    chiếu (`from app.scoring import RULES`) tự động dùng luật mới."""
    global SEASON, RULES_VERSION

    new_scoring = scoring_from_config(game_config)
    new_game = game_from_config(game_config)
    for f in new_scoring.__dataclass_fields__:
        setattr(RULES, f, getattr(new_scoring, f))
    for f in new_game.__dataclass_fields__:
        setattr(GAME, f, getattr(new_game, f))

    SEASON = season or season_from_config(game_config) or SEASON
    RULES_VERSION = rules_hash(game_config)
    return {"season": SEASON, "rules_version": RULES_VERSION, "source": RULES.source}


def load_rules(db) -> dict:
    """Nạp luật đã lưu trong bảng `seasons` (gọi khi khởi động & trước khi tính)."""
    from sqlalchemy import select

    from app.models import Season

    row = db.scalar(select(Season).where(Season.is_current.is_(True)))
    if not row or not row.rules_json:
        return {"season": SEASON, "rules_version": RULES_VERSION, "source": RULES.source}
    try:
        return apply_config(json.loads(row.rules_json), row.name)
    except (ValueError, TypeError):
        return {"season": SEASON, "rules_version": RULES_VERSION, "source": "fallback"}


def position_name(element_type: int) -> str:
    return {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}.get(element_type, "UNK")


def position_name_vi(element_type: int) -> str:
    return {1: "Thủ môn", 2: "Hậu vệ", 3: "Tiền vệ", 4: "Tiền đạo"}.get(
        element_type, "?"
    )

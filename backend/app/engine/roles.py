"""Vai trò chiến thuật của cầu thủ (LW, RW, ST, AM, CM, DM, LB, RB, LWB, RWB, CB, GK).

**Điều quan trọng nhất ở module này là những gì nó TỪ CHỐI làm.** FPL API không
công bố vai trò chiến thuật — `element_type` chỉ có 4 giá trị (GK/DEF/MID/FWD) và
một "MID" có thể là Rodri hay Saka, hai vai trò chẳng liên quan gì tới nhau khi xét
xem đối thủ yếu ở đâu. Nên ở đây:

* vai trò **không bao giờ được suy ra từ nhãn GK/DEF/MID/FWD**;
* thiếu nguồn thì giá trị là `None`, và mọi hệ số phía sau bằng **1.0**;
* `element_type` chỉ dùng để **loại bỏ** vai trò bất khả (một DEF không thể là ST),
  tức thu hẹp tập hợp lệ, chứ không chọn giúp một giá trị.

**Hai nguồn, theo thứ tự ưu tiên:**

1. bảng `player_roles` — do nguồn tự động ghi (API công khai của Premier League,
   xem `app/providers/pl_positions.py`);
2. tệp JSON khai tay trỏ bằng `PLAYER_ROLES_JSON` — **đè lên** nguồn tự động, vì
   con người có thể biết những điều nguồn tự động không biết (một hậu vệ vừa được
   đẩy lên đá tiền vệ), còn chiều ngược lại thì không.

Không có nguồn nào thì toàn bộ phần điều chỉnh theo vị trí tự tắt và giao diện nói
"Chưa đủ dữ liệu" — không phải 0%, không phải một phỏng đoán.

Định dạng tệp::

    {
      "source": "tên nguồn người đọc hiểu được",
      "updated_at": "2026-09-01T12:00:00Z",
      "roles": {
        "427": "RW",
        "Bukayo Saka": {"role": "RW", "confidence": 0.9, "note": "..."}
      }
    }

Khoá là **id cầu thủ FPL** (ưu tiên, không mơ hồ) hoặc tên — tên chỉ khớp khi duy
nhất, vì hai cầu thủ trùng tên mà gán sai vai trò thì sai âm thầm.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

# Toàn bộ vai trò được phép. Thứ tự không có ý nghĩa.
ROLES: tuple[str, ...] = (
    "GK", "CB", "LB", "RB", "LWB", "RWB", "DM", "CM", "AM", "LW", "RW", "ST",
)

# Vai trò nào KHẢ THI với từng element_type. Dùng để bác bỏ dữ liệu vô lý
# (một GK được khai là ST), KHÔNG dùng để đoán.
_ALLOWED_BY_ELEMENT: dict[int, frozenset[str]] = {
    1: frozenset({"GK"}),
    2: frozenset({"CB", "LB", "RB", "LWB", "RWB", "DM"}),
    3: frozenset({"DM", "CM", "AM", "LW", "RW", "LWB", "RWB", "ST"}),
    4: frozenset({"ST", "AM", "LW", "RW"}),
}

# --------------------------------------------------------------- trục yếu ----
# Trục điểm yếu mà mỗi vai trò TẤN CÔNG VÀO. Đây là phần "ghép vai trò với điểm
# yếu tương ứng" của đặc tả: cánh phải tấn công đối đầu cánh trái phòng ngự.
AXIS_LEFT_DEF = "left_defensive_flank"
AXIS_RIGHT_DEF = "right_defensive_flank"
AXIS_CENTRAL = "central"
AXIS_AERIAL = "set_piece_aerial"
AXES: tuple[str, ...] = (AXIS_LEFT_DEF, AXIS_RIGHT_DEF, AXIS_CENTRAL, AXIS_AERIAL)

AXIS_LABEL_VI = {
    AXIS_LEFT_DEF: "cánh trái phòng ngự của đối thủ",
    AXIS_RIGHT_DEF: "cánh phải phòng ngự của đối thủ",
    AXIS_CENTRAL: "khả năng bảo vệ trung lộ của đối thủ",
    AXIS_AERIAL: "khả năng chống bóng chết của đối thủ",
}

# Cầu thủ đá bên PHẢI tấn công đối đầu cánh TRÁI phòng ngự, và ngược lại.
_ROLE_AXIS: dict[str, str] = {
    "RW": AXIS_LEFT_DEF,
    "RB": AXIS_LEFT_DEF,
    "RWB": AXIS_LEFT_DEF,
    "LW": AXIS_RIGHT_DEF,
    "LB": AXIS_RIGHT_DEF,
    "LWB": AXIS_RIGHT_DEF,
    "ST": AXIS_CENTRAL,
    "AM": AXIS_CENTRAL,
    "CM": AXIS_CENTRAL,
    "DM": AXIS_CENTRAL,
    # Trung vệ ghi bàn gần như toàn bộ từ bóng chết, nên điểm yếu tương ứng của
    # đối thủ là khả năng chống bóng chết, không phải trung lộ khi bóng sống.
    "CB": AXIS_AERIAL,
    "GK": AXIS_AERIAL,
}

# ------------------------------------------------- dứt điểm hay tạo cơ hội ---
# Phần điểm yếu của trục được phép chảy vào THÀNH PHẦN GHI BÀN của cầu thủ.
#
# Đây là điều đặc tả nhấn mạnh: "đối thủ yếu cánh trái không tự động làm tăng khả
# năng ghi bàn của mọi cầu thủ cánh phải". Một hậu vệ cánh khai thác được cánh yếu
# chủ yếu bằng quả tạt — lợi ích của anh ta nằm ở KIẾN TẠO. Trung phong thì ngược
# lại: anh ta là người dứt điểm.
#
# **Đây là GIẢ ĐỊNH, chưa kiểm chứng.** Không có dữ liệu trong dự án để khớp bộ số
# này (cần vị trí dứt điểm theo vùng sân, thứ không nguồn nào hiện có cung cấp).
# Chúng chỉ có hiệu lực khi phần đo điểm yếu theo trục được bật; khi chưa có nguồn
# thì hệ số bằng 1.0 và bộ số dưới đây không ảnh hưởng con số nào.
FINISHING_SHARE: dict[str, float] = {
    "ST": 1.00,
    "AM": 0.70,
    "LW": 0.60,
    "RW": 0.60,
    "CM": 0.50,
    "CB": 0.90,    # bàn của trung vệ gần như chỉ đến từ bóng chết -> trục rất khớp
    "DM": 0.35,
    "LWB": 0.30,
    "RWB": 0.30,
    "LB": 0.25,
    "RB": 0.25,
    "GK": 0.0,
}

ROLE_LABEL_VI = {
    "GK": "Thủ môn", "CB": "Trung vệ", "LB": "Hậu vệ trái", "RB": "Hậu vệ phải",
    "LWB": "Hậu vệ cánh trái (dâng cao)", "RWB": "Hậu vệ cánh phải (dâng cao)",
    "DM": "Tiền vệ trụ", "CM": "Tiền vệ trung tâm", "AM": "Tiền vệ tấn công (số 10)",
    "LW": "Tiền vệ cánh trái", "RW": "Tiền vệ cánh phải", "ST": "Trung phong",
}


@dataclass(frozen=True)
class RoleAssignment:
    """Vai trò của MỘT cầu thủ, kèm xuất xứ. `role=None` nghĩa là chưa biết."""

    role: str | None
    source: str | None = None
    confidence: float | None = None
    updated_at: datetime | None = None
    note: str | None = None

    @property
    def axis(self) -> str | None:
        return _ROLE_AXIS.get(self.role) if self.role else None

    @property
    def finishing_share(self) -> float:
        """0..1 — phần điểm yếu của trục được phép chảy vào thành phần ghi bàn."""
        if not self.role:
            return 0.0
        return FINISHING_SHARE.get(self.role, 0.0)

    @property
    def label_vi(self) -> str | None:
        return ROLE_LABEL_VI.get(self.role) if self.role else None


UNKNOWN_ROLE = RoleAssignment(role=None)


def role_axis(role: str | None) -> str | None:
    return _ROLE_AXIS.get(role) if role else None


def is_plausible(role: str, element_type: int) -> bool:
    """Vai trò này có khả thi với vị trí FPL đã biết không."""
    return role in _ALLOWED_BY_ELEMENT.get(element_type, frozenset())


@dataclass
class RoleTable:
    """Bảng vai trò đã nạp, kèm trạng thái nguồn để giao diện nói thật."""

    by_player: dict[int, RoleAssignment]
    source: str | None
    configured: bool
    updated_at: datetime | None = None
    rejected: int = 0              # số bản ghi bị bác vì vai trò bất khả / lạ
    unmatched: int = 0             # số khoá không khớp cầu thủ nào
    detail: str = ""

    def get(self, player_id: int) -> RoleAssignment:
        return self.by_player.get(player_id, UNKNOWN_ROLE)

    def status(self) -> dict:
        return {
            "configured": self.configured,
            "source": self.source,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "players_with_role": len(self.by_player),
            "rejected": self.rejected,
            "unmatched": self.unmatched,
            "detail": self.detail,
            # Thứ cần để lấp, nói thẳng như các tầng tin tức đang làm
            "needs": None if self.configured else (
                "Một nguồn vai trò chiến thuật theo từng cầu thủ: bật "
                "PL_POSITIONS_ENABLED để lấy từ API công khai của Premier League, "
                "hoặc trỏ PLAYER_ROLES_JSON tới tệp khai tay. Không suy ra được từ "
                "FPL API vì API chỉ có 4 nhãn vị trí."
            ),
        }


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_roles(players: list, path: str | None = None, db=None) -> RoleTable:
    """Nạp vai trò từ **bảng `player_roles`** rồi cho tệp JSON đè lên.

    Thứ tự đó là có lý do: bảng DB do nguồn tự động ghi (API Premier League), còn
    tệp JSON là thứ **con người khai bằng tay**. Khi hai bên nói khác nhau thì người
    thắng — người có thể biết những điều nguồn tự động không biết (một hậu vệ vừa
    được đẩy lên đá tiền vệ chẳng hạn), còn điều ngược lại thì không.

    `players` là danh sách ORM `Player` (cần `id`, `element_type`, `web_name`,
    `first_name`, `second_name`) để kiểm tra tính khả thi và khớp theo tên.
    """
    json_table = _load_roles_json(players, path)
    db_table = _load_roles_db(db, players) if db is not None else None
    if db_table is None:
        return json_table

    # Gộp LUÔN LUÔN, kể cả khi một bên (hoặc cả hai) không có vai trò nào dùng được.
    # Bản đầu trả thẳng `json_table` khi bảng DB rỗng, và thế là **nuốt mất phần
    # chẩn đoán**: nguồn tự động có 1 bản ghi bị bác vì mâu thuẫn vị trí thì người
    # dùng lại đọc được "PLAYER_ROLES_JSON chưa được đặt" — đúng chữ, sai chuyện.
    merged = dict(db_table.by_player)
    merged.update(json_table.by_player)      # khai tay đè lên nguồn tự động
    overridden = len(set(db_table.by_player) & set(json_table.by_player))
    parts = [t.detail for t in (db_table, json_table) if t.detail]
    if overridden:
        parts.append(f"{overridden} cầu thủ được khai tay đè lên nguồn tự động")
    sources = [t.source for t in (db_table, json_table) if t.source and t.by_player]
    return RoleTable(
        by_player=merged,
        source=" + ".join(sources) if sources else (db_table.source or json_table.source),
        configured=bool(merged),
        updated_at=json_table.updated_at or db_table.updated_at,
        rejected=db_table.rejected + json_table.rejected,
        unmatched=db_table.unmatched + json_table.unmatched,
        detail=" · ".join(parts),
    )


def _load_roles_db(db, players: list) -> RoleTable | None:
    """Vai trò đã lưu trong bảng `player_roles` (do provider ngoài ghi)."""
    try:
        from sqlalchemy import select

        from app.models import PlayerRole
    except ImportError:                       # pragma: no cover
        return None
    try:
        rows = db.scalars(select(PlayerRole)).all()
    except Exception:                          # noqa: BLE001 - bảng chưa tồn tại
        return None

    valid = {p.id: p for p in players}
    out: dict[int, RoleAssignment] = {}
    rejected = 0
    latest = None
    source = None
    for r in rows:
        # Hàng `role = NULL` là có ý: nguồn CÓ nói nhưng nói không đủ rõ (vd
        # "Left/Right Winger"). Không nạp thành vai trò, nhưng cũng không phải lỗi.
        if not r.role:
            continue
        p = valid.get(r.player_id)
        if p is None:
            continue
        if r.role not in ROLES or not is_plausible(r.role, p.element_type):
            rejected += 1
            continue
        source = source or r.source_name or None
        got = r.fetched_at
        if got is not None and (latest is None or got > latest):
            latest = got
        out[p.id] = RoleAssignment(
            role=r.role, source=r.source_name or None, confidence=None,
            updated_at=got, note=r.raw_label,
        )
    if not out:
        # `rejected` phải đi cùng: khi bản ghi DUY NHẤT trong bảng bị bác vì mâu
        # thuẫn với vị trí FPL, người dùng cần đọc được đúng điều đó, chứ không
        # phải "bảng trống".
        return RoleTable(
            by_player={}, source=source, configured=False, rejected=rejected,
            detail=(
                "Bảng player_roles chưa có vai trò nào dùng được"
                + (f" ({rejected} bản ghi bị bác vì mâu thuẫn vị trí FPL)"
                   if rejected else "")
                + "."
            ),
        )
    return RoleTable(
        by_player=out, source=source or "player_roles", configured=True,
        updated_at=latest, rejected=rejected,
        detail=(
            f"{len(out)} cầu thủ có vai trò từ “{source or 'player_roles'}”"
            + (f", {rejected} bản ghi bị bác (vai trò bất khả)" if rejected else "")
        ),
    )


def _load_roles_json(players: list, path: str | None = None) -> RoleTable:
    """Nạp vai trò từ tệp JSON đã khai. Không có tệp -> bảng rỗng, `configured=False`."""
    from app.config import settings

    path = settings.player_roles_json if path is None else path
    if not path:
        return RoleTable(
            by_player={}, source=None, configured=False,
            detail="PLAYER_ROLES_JSON chưa được đặt — phần điều chỉnh theo vị trí tắt.",
        )
    if not os.path.exists(path):
        return RoleTable(
            by_player={}, source=None, configured=False,
            detail=f"Không tìm thấy tệp vai trò: {path}",
        )
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return RoleTable(
            by_player={}, source=None, configured=False,
            detail=f"Không đọc được tệp vai trò ({exc}) — hệ số giữ ở 1.0.",
        )

    source = raw.get("source") or os.path.basename(path)
    updated_at = _parse_dt(raw.get("updated_at"))
    entries = raw.get("roles") or {}

    by_id = {p.id: p for p in players}
    # Khớp theo tên chỉ khi tên là DUY NHẤT: trùng tên mà gán sai vai trò là một
    # lỗi âm thầm, tệ hơn hẳn việc bỏ qua bản ghi đó.
    name_index: dict[str, list] = {}
    for p in players:
        for key in {
            (p.web_name or "").strip().lower(),
            f"{p.first_name or ''} {p.second_name or ''}".strip().lower(),
        }:
            if key:
                name_index.setdefault(key, []).append(p)

    out: dict[int, RoleAssignment] = {}
    rejected = unmatched = 0
    for key, value in entries.items():
        if isinstance(value, dict):
            role = (value.get("role") or "").strip().upper()
            conf = value.get("confidence")
            note = value.get("note")
        else:
            role, conf, note = str(value).strip().upper(), None, None

        player = None
        k = str(key).strip()
        if k.isdigit():
            player = by_id.get(int(k))
        if player is None:
            cands = name_index.get(k.lower(), [])
            player = cands[0] if len(cands) == 1 else None
        if player is None:
            unmatched += 1
            continue
        if role not in ROLES or not is_plausible(role, player.element_type):
            # Vai trò lạ hoặc bất khả với vị trí FPL -> bác bỏ, KHÔNG thay bằng
            # phỏng đoán. Một vai trò sai còn tệ hơn không có vai trò.
            rejected += 1
            continue
        out[player.id] = RoleAssignment(
            role=role,
            source=source,
            confidence=None if conf is None else max(0.0, min(1.0, float(conf))),
            updated_at=updated_at,
            note=note,
        )

    return RoleTable(
        by_player=out, source=source, configured=bool(out), updated_at=updated_at,
        rejected=rejected, unmatched=unmatched,
        detail=(
            f"{len(out)} cầu thủ có vai trò từ “{source}”"
            + (f", {rejected} bản ghi bị bác (vai trò bất khả)" if rejected else "")
            + (f", {unmatched} khoá không khớp cầu thủ" if unmatched else "")
        ),
    )

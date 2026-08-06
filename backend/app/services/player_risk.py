"""Các chỉ số rủi ro & giá trị cho từng cầu thủ, ngoài phần dự báo điểm.

Bốn thứ ở đây không có sẵn trong dự báo và mỗi thứ trả lời một câu khác nhau:

  * **VORP** — hơn được bao nhiêu so với người thay thế *rẻ nhất chấp nhận được*
    cùng vị trí. Đây là số quyết định "có đáng chiếm một suất không", khác với xP
    (chỉ nói được nhiều điểm hay ít điểm). Thủ môn 5.0 triệu ghi 4 điểm không giống
    tiền vệ 13.0 triệu ghi 4 điểm.
  * **Rotation risk** — nguy cơ bị xoay vòng, tính từ P(đá chính) cộng độ dao động
    số phút thực tế. Khác với injury risk: người khoẻ mạnh vẫn có thể bị xoay.
  * **Injury risk** — từ trạng thái ra sân FPL công bố (`status`,
    `chance_of_playing`) và độ mới của tin.
  * **Price risk** — động lượng chuyển nhượng ròng. **Chỉ báo, không phải dự báo**:
    ngưỡng đổi giá của FPL không công khai và còn phụ thuộc tỷ lệ sở hữu.

Mỗi chỉ số trả về `{level, score, basis}` — `basis` là căn cứ để người đọc tự kiểm,
vì một nhãn "Cao" không nói gì nếu không biết vì sao.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, PlayerGameweekStat, SourceFetchLog

# Người thay thế: xếp mọi cầu thủ cùng vị trí theo xP rồi lấy người ở phân vị này.
# 60% nghĩa là "khá hơn 40% số người cùng vị trí" — xấp xỉ chất lượng của một suất
# ghế dự bị dùng được, chứ không phải cầu thủ tệ nhất giải (lấy người tệ nhất sẽ
# làm VORP của ai cũng to và mất hết khả năng phân biệt).
REPLACEMENT_PERCENTILE = 0.60

# Ngưỡng chuyển nhượng ròng để coi là có động lượng giá. Giống decision_tree.py.
MODERATE_NET_TRANSFERS = 30_000
STRONG_NET_TRANSFERS = 100_000

# Tin ra sân cũ hơn mức này thì coi như đã lỗi thời với quyết định tuần này.
NEWS_STALE_DAYS = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ----------------------------------------------------------------- VORP --------
def replacement_level(xp_by_pos: dict[int, list[float]]) -> dict[int, float]:
    """xP của người thay thế cho từng vị trí, tại `REPLACEMENT_PERCENTILE`."""
    out: dict[int, float] = {}
    for pos, values in xp_by_pos.items():
        vals = sorted(v for v in values if v > 0)
        if not vals:
            out[pos] = 0.0
            continue
        idx = min(len(vals) - 1, int(REPLACEMENT_PERCENTILE * len(vals)))
        out[pos] = vals[idx]
    return out


def vorp(xp: float, replacement: float) -> dict:
    """Hơn người thay thế bao nhiêu điểm. Âm nghĩa là không đáng chiếm suất."""
    diff = xp - replacement
    return {
        "value": round(diff, 2),
        "replacement_xp": round(replacement, 2),
        "basis": (
            f"xP {xp:.2f} trừ mức người thay thế cùng vị trí {replacement:.2f} "
            f"(phân vị {REPLACEMENT_PERCENTILE:.0%} trong số người có xP dương)"
        ),
    }


# -------------------------------------------------------- rotation risk --------
def rotation_risk(p_start: float, recent_minutes: list[int] | None) -> dict:
    """Nguy cơ bị xoay vòng: P(đá chính) là chính, dao động số phút là phụ.

    Hai cầu thủ cùng P(đá chính) 0.7 không giống nhau: một người luôn đá 70 phút,
    người kia lúc 90 lúc 0. Độ lệch chuẩn số phút bắt được khác biệt đó, còn
    P(đá chính) một mình thì không.
    """
    score = 1.0 - max(0.0, min(1.0, p_start))
    basis = f"P(đá chính) {p_start:.0%}"

    if recent_minutes and len(recent_minutes) >= 3:
        recent = recent_minutes[-6:]
        sd = statistics.pstdev(recent) if len(recent) > 1 else 0.0
        # sd 45 phút (lúc đá cả trận, lúc ngồi ngoài) là mức dao động tối đa có nghĩa
        score = min(1.0, score + 0.4 * min(1.0, sd / 45.0))
        basis += f", độ lệch phút {len(recent)} vòng gần nhất {sd:.0f}'"
    else:
        basis += ", chưa có số phút từng vòng để đo dao động"

    level = "Thấp" if score < 0.25 else "Trung bình" if score < 0.55 else "Cao"
    return {"level": level, "score": round(score, 3), "basis": basis}


# ---------------------------------------------------------- injury risk --------_
_STATUS_LABEL = {
    "a": "sẵn sàng",
    "d": "có nghi ngờ",
    "i": "chấn thương",
    "s": "treo giò",
    "u": "không khả dụng",
    "n": "không đủ điều kiện",
}


def injury_risk(status: str, chance: int | None, news: str | None,
                news_added: datetime | None) -> dict:
    """Rủi ro ra sân theo đúng những gì FPL công bố — không suy diễn thêm.

    `chance_of_playing` là con số của chính FPL, nên khi có thì dùng thẳng. Chỉ khi
    thiếu mới suy từ `status`. Không tự đoán mức độ chấn thương từ nội dung tin: đó
    là việc của người đọc, và mọi con số ta gán vào đó đều là bịa.
    """
    label = _STATUS_LABEL.get(status, status)
    if chance is not None:
        score = 1.0 - max(0, min(100, chance)) / 100.0
        basis = f"FPL công bố {chance}% khả năng ra sân ({label})"
    elif status == "a":
        score = 0.0
        basis = f"FPL báo {label}, không có cảnh báo"
    else:
        score = {"d": 0.5, "i": 0.9, "s": 1.0, "u": 1.0, "n": 1.0}.get(status, 0.5)
        basis = f"FPL báo {label}, không kèm % khả năng ra sân"

    age_days = None
    added = _aware(news_added)
    if added:
        age_days = (_now() - added).total_seconds() / 86400.0
        basis += f", tin cách đây {age_days:.1f} ngày"
        if age_days > NEWS_STALE_DAYS and score > 0:
            basis += " (đã cũ — có thể chưa cập nhật)"

    level = "Thấp" if score < 0.15 else "Trung bình" if score < 0.5 else "Cao"
    return {
        "level": level,
        "score": round(score, 3),
        "status": status,
        "status_label": label,
        "news": news or "",
        "news_age_days": None if age_days is None else round(age_days, 1),
        "basis": basis,
    }


# ----------------------------------------------------------- price risk --------
def price_risk(transfers_in: int, transfers_out: int, ownership: float) -> dict:
    """Động lượng giá — CHỈ BÁO, không phải dự báo.

    Ngưỡng đổi giá của FPL không công khai và phụ thuộc tỷ lệ sở hữu, nên ở đây chỉ
    báo cáo số ròng và hướng. Gán một xác suất cho việc đổi giá là bịa một con số
    trông đáng tin.
    """
    net = (transfers_in or 0) - (transfers_out or 0)
    strong = abs(net) >= STRONG_NET_TRANSFERS
    moderate = abs(net) >= MODERATE_NET_TRANSFERS
    if not moderate:
        level, direction = "Thấp", "ổn định"
    else:
        level = "Cao" if strong else "Trung bình"
        direction = "có thể tăng" if net > 0 else "có thể giảm"
    return {
        "level": level,
        "direction": direction,
        "net_transfers": net,
        "basis": (
            f"{net:+,} lượt chuyển nhượng ròng trong vòng này, sở hữu {ownership}%"
        ),
        "caveat": (
            "Ngưỡng đổi giá của FPL không công khai và phụ thuộc tỷ lệ sở hữu — "
            "đây là chỉ báo động lượng, không phải dự báo."
        ),
        "is_prediction": False,
    }


# ------------------------------------------------------ source freshness -------
def source_freshness(db: Session, player: Player) -> dict:
    """Dữ liệu của cầu thủ này cũ bao nhiêu, và cũ ở phần nào.

    Ba mốc, vì chúng già đi với nhịp khác nhau:

      * `last_fpl_sync` — lần đồng bộ FPL gần nhất. Đây là mốc quyết định `stale`.
      * `player_updated_at` — lần hàng của cầu thủ này thật sự ĐỔI GIÁ TRỊ. Không
        dùng để kết luận cũ/mới: `onupdate` chỉ nhích khi có thay đổi, nên một cầu
        thủ không đổi gì suốt tuần sẽ mang mốc rất cũ dù dữ liệu vừa được xác nhận.
      * `has_gameweek_detail` — có số phút từng vòng hay không. Thiếu nó thì mô hình
        xoay vòng phải dùng tổng cả mùa, kém hơn nhiều.
    """
    now = _now()
    updated = _aware(player.updated_at)
    age_min = None if updated is None else (now - updated).total_seconds() / 60.0

    last_fpl = db.scalar(
        select(SourceFetchLog.fetched_at)
        .where(SourceFetchLog.source_name.like("FPL%"))
        .order_by(SourceFetchLog.fetched_at.desc())
    )
    last_fpl = _aware(last_fpl)
    sync_age_min = None if last_fpl is None else (now - last_fpl).total_seconds() / 60.0

    has_gw_detail = db.scalar(
        select(PlayerGameweekStat.id).where(
            PlayerGameweekStat.player_id == player.id
        ).limit(1)
    ) is not None

    # "Cũ" phải đo bằng tuổi của LẦN ĐỒNG BỘ, không phải `player.updated_at`.
    # `updated_at` có `onupdate=func.now()` nên chỉ nhích khi hàng thật sự đổi giá
    # trị; đồng bộ ghi lại đúng số cũ thì SQLAlchemy không phát UPDATE và mốc đó
    # đứng nguyên. Đã thấy hậu quả trên production: badge "đã cũ" nằm cạnh dòng
    # "đồng bộ gần nhất cách đây 9 phút" — hai câu tự mâu thuẫn trên cùng một thẻ.
    stale = bool(sync_age_min is not None and sync_age_min > 12 * 60)
    if sync_age_min is None:
        stale = bool(age_min is not None and age_min > 12 * 60)
    return {
        "player_updated_at": updated.isoformat() if updated else None,
        "player_age_minutes": None if age_min is None else round(age_min, 1),
        "last_fpl_sync": last_fpl.isoformat() if last_fpl else None,
        "fpl_sync_age_minutes": None if sync_age_min is None else round(sync_age_min, 1),
        "has_gameweek_detail": has_gw_detail,
        "stale": stale,
        "basis": (
            (f"Đồng bộ FPL gần nhất cách đây {sync_age_min:.0f} phút. "
             if sync_age_min is not None else "Chưa có log đồng bộ FPL. ")
            + ("Có số phút từng vòng." if has_gw_detail else
               "CHƯA có số phút từng vòng — mô hình xoay vòng phải dùng tổng cả mùa.")
        ),
    }

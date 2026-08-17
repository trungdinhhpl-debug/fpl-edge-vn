"""Chấm điểm dự báo của từng nguồn bằng kết quả THẬT.

Bảng `expert_track_record` từ trước tới nay **chưa ai ghi vào**, nên độ chính xác
trên trang Chuyên gia vĩnh viễn là "chưa đủ dữ liệu" — một ô trống được giải thích
tử tế, nhưng vẫn là ô trống. Module này đóng vòng lặp đó.

Hai bước, cố ý tách rời:

1. **Đóng băng** — mỗi tín hiệu của vòng N được ghi thành một dự báo `correct=None`
   ngay khi phát ra. Ghi trước là điều kiện để phép đo có nghĩa: nếu chỉ ghi lại
   sau khi đã biết kết quả thì không có gì ngăn được việc chọn lọc những dự báo
   trúng.
2. **Chấm** — sau khi vòng đấu kết thúc, đối chiếu với điểm thật.

**Chuẩn so sánh là trung vị cùng vị trí, không phải một ngưỡng điểm ghi cứng.**
"Trên 6 điểm là đúng" nghe hợp lý nhưng nó thưởng cho việc gợi ý tiền đạo và phạt
việc gợi ý hậu vệ, ở mọi mùa giải, mãi mãi. Câu hỏi thật là *"gợi ý này có hơn cái
mà người chơi lẽ ra đã chọn thay thế không"*, và người thay thế gần nhất là một cầu
thủ trung bình cùng vị trí đã ra sân vòng đó. Chuẩn này tự trôi theo mùa giải,
không cần chỉnh tay, và so sánh được giữa các vị trí.

Điểm thật lấy từ `/api/event/{gw}/live/` — **một** lệnh gọi cho cả vòng, thay vì
~700 lệnh `element-summary`. Nghĩa là chấm điểm không phụ thuộc `sync_players_detail`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ExpertSignal, ExpertTrackRecord, Gameweek, Player
from app.services.experts import SIGNAL_DOMAIN

FPL_API = "https://fantasy.premierleague.com/api"

# Tín hiệu nói "hãy sở hữu/dùng người này" so với "hãy tránh".
POSITIVE_CLAIM = {"start", "captain", "buy", "differential", "setpiece", "penalty"}
NEGATIVE_CLAIM = {"sell", "avoid"}
# `injury` được chấm theo phút thi đấu chứ không theo điểm — xem `_is_correct`.
MINUTES_CLAIM = {"injury"}
# Ngưỡng "đã thật sự ra sân" cho một dự báo chấn thương.
INJURY_MINUTES = 60


def fetch_live_points(gameweek: int) -> tuple[dict[int, int], dict[int, int]]:
    """({player_id: điểm}, {player_id: phút}) của một vòng đã đá.

    Trả về hai dict rỗng khi vòng chưa có dữ liệu — trạng thái hợp lệ, không phải
    lỗi, và chỗ gọi phải phân biệt được "chưa đá" với "đá mà 0 điểm".
    """
    try:
        r = httpx.get(
            f"{FPL_API}/event/{gameweek}/live/",
            timeout=settings.http_timeout_seconds,
            headers={"User-Agent": "fpl-edge-vn/1.0"},
        )
        if r.status_code != 200:
            return {}, {}
        elements = r.json().get("elements", []) or []
    except Exception:
        return {}, {}

    points: dict[int, int] = {}
    minutes: dict[int, int] = {}
    for e in elements:
        st = e.get("stats") or {}
        points[e["id"]] = int(st.get("total_points") or 0)
        minutes[e["id"]] = int(st.get("minutes") or 0)
    return points, minutes


def positional_benchmark(
    points: dict[int, int], minutes: dict[int, int], position_of: dict[int, int]
) -> dict[int, float]:
    """{element_type: điểm trung vị của người ĐÃ RA SÂN ở vị trí đó}.

    Chỉ tính người có phút thi đấu. Gộp cả những người ngồi ngoài sẽ kéo trung vị
    xuống 0 và biến gần như mọi gợi ý thành "đúng".
    """
    by_pos: dict[int, list[int]] = {}
    for pid, mins in minutes.items():
        if mins <= 0:
            continue
        pos = position_of.get(pid)
        if pos is None:
            continue
        by_pos.setdefault(pos, []).append(points.get(pid, 0))
    out: dict[int, float] = {}
    for pos, vals in by_pos.items():
        vals.sort()
        n = len(vals)
        out[pos] = float(vals[n // 2]) if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
    return out


def _is_correct(
    signal_type: str,
    player_points: int,
    player_minutes: int,
    benchmark: float,
) -> bool | None:
    """Dự báo này đúng hay sai. `None` = không chấm được loại tín hiệu này."""
    if signal_type in MINUTES_CLAIM:
        # "Người này dính chấn thương" đúng khi anh ta thật sự không đá đủ trận.
        return player_minutes < INJURY_MINUTES
    if signal_type in POSITIVE_CLAIM:
        return player_points > benchmark
    if signal_type in NEGATIVE_CLAIM:
        return player_points <= benchmark
    # `hold` không khẳng định điều gì kiểm chứng được, nên không chấm.
    return None


def freeze_predictions(db: Session, gameweek: int) -> int:
    """Ghi lại tín hiệu của vòng `gameweek` thành dự báo chưa chấm.

    Ghi TRƯỚC khi biết kết quả. Nếu chỉ ghi sau thì không gì ngăn được việc chọn
    lọc những dự báo đã trúng, và con số chính xác thu được sẽ vô nghĩa.
    """
    existing = {
        (r.source_id, r.player_id, r.domain)
        for r in db.scalars(
            select(ExpertTrackRecord).where(ExpertTrackRecord.gameweek == gameweek)
        ).all()
    }
    signals = db.scalars(
        select(ExpertSignal).where(
            ExpertSignal.gameweek == gameweek,
            ExpertSignal.is_mock.is_(False),
        )
    ).all()
    added = 0
    for s in signals:
        domain = SIGNAL_DOMAIN.get(s.signal_type)
        if not domain or s.player_id is None:
            continue
        if (s.source_id, s.player_id, domain) in existing:
            continue
        db.add(ExpertTrackRecord(
            source_id=s.source_id, domain=domain, gameweek=gameweek,
            player_id=s.player_id,
            claim=f"{s.signal_type}: {(s.summary or '')[:180]}",
            correct=None,
        ))
        existing.add((s.source_id, s.player_id, domain))
        added += 1
    return added


def resolve_predictions(db: Session, gameweek: int) -> dict:
    """Chấm những dự báo chưa có kết quả của một vòng đã đá."""
    points, minutes = fetch_live_points(gameweek)
    if not points:
        return {"resolved": 0, "reason": f"Vòng {gameweek} chưa có dữ liệu điểm."}

    position_of = {
        p.id: p.element_type for p in db.scalars(select(Player)).all()
    }
    bench = positional_benchmark(points, minutes, position_of)

    pending = db.scalars(
        select(ExpertTrackRecord).where(
            ExpertTrackRecord.gameweek == gameweek,
            ExpertTrackRecord.correct.is_(None),
        )
    ).all()
    now = datetime.now(timezone.utc)
    resolved = 0
    for rec in pending:
        if rec.player_id not in points:
            continue
        signal_type = (rec.claim or "").split(":", 1)[0].strip()
        verdict = _is_correct(
            signal_type,
            points[rec.player_id],
            minutes.get(rec.player_id, 0),
            bench.get(position_of.get(rec.player_id, 0), 0.0),
        )
        if verdict is None:
            continue
        rec.correct = verdict
        rec.resolved_at = now
        resolved += 1
    return {
        "resolved": resolved,
        "benchmark": {str(k): v for k, v in sorted(bench.items())},
        "reason": f"Đã chấm {resolved} dự báo của vòng {gameweek}.",
    }


def score_finished_gameweeks(db: Session, limit: int = 5) -> dict:
    """Đóng băng + chấm cho các vòng đã kết thúc mà chưa chấm xong.

    Chạy trong mỗi lần đồng bộ. Trước vòng 1 nó không làm gì và nói rõ như vậy —
    đó là câu trả lời đúng, không phải một chỗ hỏng.
    """
    finished = db.scalars(
        select(Gameweek).where(Gameweek.finished.is_(True)).order_by(Gameweek.id.desc())
    ).all()[:limit]
    if not finished:
        return {"gameweeks": [], "note": "Chưa vòng nào kết thúc nên chưa có gì để chấm."}

    out = []
    for gw in reversed(finished):
        frozen = freeze_predictions(db, gw.id)
        res = resolve_predictions(db, gw.id)
        out.append({"gameweek": gw.id, "frozen": frozen, **res})
    db.commit()
    return {"gameweeks": out, "note": f"Đã xét {len(out)} vòng đã kết thúc."}

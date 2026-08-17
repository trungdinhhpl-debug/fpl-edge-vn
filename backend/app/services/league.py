"""Đối thủ trong mini-league: sở hữu và băng đội trưởng ĐO ĐƯỢC, không phải mô hình.

FPL là trò chơi thứ hạng tương đối. Một nước đi hơn 0.3 xP nhưng bỏ mất người mà
80% mini-league đang có là nước LỖ về thứ hạng — bảng xP không nhìn thấy điều đó,
vì nó chỉ biết bạn được bao nhiêu điểm chứ không biết những người bạn đang đua
được bao nhiêu.

Trang Đội trưởng đã có một phiên bản EO, nhưng phần băng đội trưởng ở đó là MÔ
HÌNH (FPL không công khai số liệu đội trưởng trước hạn chót) và mẫu là toàn bộ
người chơi thế giới. Ở đây cả hai vế đều khác:

  * đám đông là đúng nhóm bạn đang đua, không phải 11 triệu người;
  * và cả hai con số đều ĐẾM ĐƯỢC từ đội hình thật, không cần giả định gì.

Đổi lại là độ trễ, và đây là giới hạn quan trọng nhất của cả trang: từ mùa
2026/27 FPL chỉ mở đội hình của người khác SAU KHI vòng đấu kết thúc, nên thứ
đếm được luôn là đội hình của vòng gần nhất đã xong. Đối thủ đã chuyển nhượng
sau đó. Mọi payload ở đây vì thế mang theo `measured_gameweek` và một câu cảnh
báo — con số này mô tả quá khứ gần, không phải đội hình họ sắp dùng.

EO ở đây định nghĩa bằng HỆ SỐ TRUNG BÌNH:

    EO = trung bình(multiplier) × 100

Cách này xử lý đúng mọi trường hợp mà không cần luật riêng: dự bị đóng góp 0,
đá chính 1, đội trưởng 2, Triple Captain 3. Nó khớp với định nghĩa EO quen thuộc
(%đá chính + %bắt băng) nhưng không vỡ khi có người dùng chip.
"""
from __future__ import annotations

import time
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache import cache
from app.config import settings
from app.models import Gameweek, Player
from app.providers.fpl_client import FPLClient, FPLNotFound
from app.services.common import planning_start_gw, player_public, projections_for_gw, team_lookup

# Một trang standings của FPL là 50 người, và mỗi đối thủ tốn MỘT lệnh gọi API.
# Trần này là đánh đổi trực tiếp giữa độ mịn của EO và thời gian chờ của người
# dùng (50 người ≈ 6 giây ở `fpl_request_delay_ms` mặc định).
MAX_RIVALS = 50
# Đội hình của một vòng ĐÃ KẾT THÚC không bao giờ đổi nữa, nên cache dài là an
# toàn — cái hết hạn không phải dữ liệu mà là vòng đấu.
PICKS_CACHE_TTL = 12 * 3600


def _latest_public_gameweek(db: Session) -> int | None:
    """Vòng gần nhất mà đội hình người khác đã được FPL mở.

    Mốc là vòng KẾT THÚC, không phải hạn chót đã qua: từ 2026/27 FPL giữ kín
    đội hình cho tới khi vòng đấu xong (xem `ingestion/team_import.py`).
    """
    return db.scalar(
        select(Gameweek.id).where(Gameweek.finished.is_(True)).order_by(Gameweek.id.desc())
    )


def _fetch_rival_picks(league_id: int, gw: int, top_n: int) -> dict:
    """Bảng xếp hạng + đội hình từng đối thủ. Trả về cả những gì KHÔNG lấy được."""
    key = f"league:{league_id}:gw{gw}:top{top_n}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    rivals: list[dict] = []
    failed: list[dict] = []
    delay = settings.fpl_request_delay_ms / 1000.0

    with FPLClient() as client:
        standings = client.league_standings(league_id)
        league = standings.get("league", {}) or {}
        results = (standings.get("standings", {}) or {}).get("results", []) or []

        for row in results[:top_n]:
            entry_id = row.get("entry")
            if not entry_id:
                # Người mới vào giải chưa có `entry` trong bảng xếp hạng.
                continue
            try:
                picks = client.entry_picks(entry_id, gw)
            except FPLNotFound:
                failed.append({"entry": entry_id, "name": row.get("entry_name"),
                               "reason": "not_public"})
                continue
            except Exception as exc:
                failed.append({"entry": entry_id, "name": row.get("entry_name"),
                               "reason": str(exc)[:80]})
                continue
            rivals.append({
                "entry": entry_id,
                "entry_name": row.get("entry_name"),
                "player_name": row.get("player_name"),
                "rank": row.get("rank"),
                "total": row.get("total"),
                "picks": [
                    {"element": p["element"], "multiplier": p.get("multiplier", 0)}
                    for p in picks.get("picks", [])
                ],
                "active_chip": picks.get("active_chip"),
            })
            time.sleep(delay)

    payload = {
        "league_name": league.get("name"),
        "league_id": league_id,
        # Chỉ trang đầu: giải 500 người thì đây là 50, và đó đúng là mẫu ta đo.
        "n_on_first_page": len(results),
        "rivals": rivals,
        "failed": failed,
    }
    cache.set(key, payload, PICKS_CACHE_TTL)
    return payload


def _my_multipliers(entry_id: int | None, squad_ids: list[int] | None,
                    gw: int) -> tuple[dict[int, float], str]:
    """Hệ số của CHÍNH BẠN, và nói rõ con số đó lấy từ đâu.

    Nhập Team ID thì đọc được đội hình thật (ai đá chính, ai bắt băng). Chỉ dán
    15 id thì không có thông tin đó, nên coi cả 15 là đá chính — điều này thổi
    phồng phần "tôi có" của bạn so với EO, và payload phải nói ra thay vì lặng lẽ
    trộn hai loại số vào một phép trừ.
    """
    if entry_id:
        try:
            with FPLClient() as client:
                picks = client.entry_picks(entry_id, gw)
            mults = {p["element"]: float(p.get("multiplier", 0))
                     for p in picks.get("picks", [])}
            if mults:
                return mults, "picks"
        except Exception:
            pass
    if squad_ids:
        return {pid: 1.0 for pid in squad_ids}, "squad_ids"
    return {}, "none"


def league_analysis(db: Session, league_id: int, squad_ids: list[int] | None = None,
                    entry_id: int | None = None, top_n: int = 30) -> dict:
    top_n = max(1, min(top_n, MAX_RIVALS))
    gw = _latest_public_gameweek(db)
    if gw is None:
        return {
            "available": False,
            "code": "no_finished_gameweek",
            "message": (
                "Chưa vòng nào kết thúc nên FPL chưa mở đội hình của bất kỳ ai — "
                "không có gì để đếm. Sau khi vòng 1 đá xong, trang này sẽ đo được "
                "sở hữu và băng đội trưởng THẬT trong mini-league của bạn."
            ),
        }

    data = _fetch_rival_picks(league_id, gw, top_n)
    rivals = data["rivals"]
    if not rivals:
        # Hai lý do rất khác nhau, và gộp chúng lại là đổ oan cho người dùng:
        # bảng xếp hạng RỖNG nghĩa là giải có thật nhưng chưa ai có điểm (FPL chỉ
        # dựng bảng sau vòng đầu), còn có người mà không đọc được đội hình nào
        # mới là dấu hiệu sai loại giải.
        empty = data.get("n_on_first_page", 0) == 0
        return {
            "available": False,
            "code": "league_not_ranked_yet" if empty else "no_rival_squads",
            "league_name": data.get("league_name"),
            "measured_gameweek": gw,
            "failed": data["failed"],
            "message": (
                f"Giải “{data.get('league_name')}” có tồn tại nhưng bảng xếp hạng "
                f"còn rỗng — FPL chỉ dựng bảng sau khi vòng đầu tiên có điểm. "
                f"Quay lại sau vòng 1."
                if empty else
                f"Đọc được bảng xếp hạng nhưng không lấy được đội hình nào ở vòng "
                f"{gw}. Nhiều khả năng đây là giải head-to-head — dạng giải đó "
                f"dùng endpoint khác và chưa được hỗ trợ."
            ),
        }

    n = len(rivals)
    mult_sum: dict[int, float] = defaultdict(float)
    own_count: dict[int, int] = defaultdict(int)
    cap_count: dict[int, int] = defaultdict(int)
    for r in rivals:
        for p in r["picks"]:
            pid, m = p["element"], float(p["multiplier"])
            own_count[pid] += 1
            mult_sum[pid] += m
            if m >= 2:
                cap_count[pid] += 1

    my_mult, my_source = _my_multipliers(entry_id, squad_ids, gw)

    teams = team_lookup(db)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    next_gw = planning_start_gw(db)
    projs = projections_for_gw(db, next_gw)

    rows: list[dict] = []
    for pid in set(mult_sum) | set(my_mult):
        p = players.get(pid)
        if not p:
            continue
        eo = 100.0 * mult_sum.get(pid, 0.0) / n
        mine = my_mult.get(pid, 0.0)
        pr = projs.get(pid)
        xp = pr.xp if pr else 0.0
        rows.append({
            **player_public(p, teams.get(p.team_id)),
            "xp_next": round(xp, 2),
            # Đo được, không mô hình: đếm trên đội hình thật của n đối thủ.
            "league_eo": round(eo, 1),
            "league_owned_pct": round(100.0 * own_count.get(pid, 0) / n, 1),
            "league_captain_pct": round(100.0 * cap_count.get(pid, 0) / n, 1),
            "my_multiplier": mine,
            "i_own": pid in my_mult,
            # Điểm bạn hơn (hoặc kém) đám đông ở vòng tới nếu mọi thứ diễn ra
            # đúng kỳ vọng. Dương = bứt lên, âm = đang hở sườn.
            "rank_edge": round(xp * (mine - eo / 100.0), 2),
        })

    rows.sort(key=lambda r: r["league_eo"], reverse=True)
    template = [r for r in rows if r["league_eo"] >= 50][:15]
    missing = sorted(
        [r for r in rows if not r["i_own"] and r["league_eo"] >= 30],
        key=lambda r: r["rank_edge"],
    )[:10]
    differentials = sorted(
        [r for r in rows if r["i_own"] and r["league_eo"] <= 30],
        key=lambda r: r["rank_edge"], reverse=True,
    )[:10]

    exposure = round(sum(r["rank_edge"] for r in missing), 2)
    upside = round(sum(r["rank_edge"] for r in differentials), 2)

    return {
        "available": True,
        "league_id": league_id,
        "league_name": data.get("league_name"),
        "measured_gameweek": gw,
        "projection_gameweek": next_gw,
        "n_rivals": n,
        "failed": data["failed"],
        "my_squad_source": my_source,
        "has_my_squad": bool(my_mult),
        # Ba danh sách dưới là các LÁT CẮT theo ngưỡng, nên có người rơi ra khỏi
        # cả ba: ai cũng sở hữu nhưng ai cũng để dự bị thì EO = 0 mà sở hữu =
        # 100%, và đó lại đúng là thứ đáng biết. `players` là bảng đầy đủ để
        # không có gì đã tính ra rồi mà lại không hiện được ở đâu.
        "players": rows,
        "template": template,
        "missing_template": missing,
        "my_differentials": differentials,
        "exposure_xp": exposure,
        "upside_xp": upside,
        "net_rank_edge": round(upside + exposure, 2),
        "notes": _notes(gw, next_gw, n, my_source, data["failed"]),
    }


def _notes(gw: int, next_gw: int, n: int, my_source: str, failed: list) -> list[str]:
    out = [
        f"EO đếm trên đội hình THẬT của {n} đối thủ ở vòng {gw} — không phải mô "
        f"hình, nhưng cũng không phải đội hình họ sẽ dùng ở vòng {next_gw}: FPL "
        f"chỉ mở đội hình sau khi vòng đấu kết thúc, nên họ đã chuyển nhượng "
        f"trong khoảng thời gian đó.",
        "EO = trung bình hệ số nhân × 100 (dự bị 0, đá chính 1, đội trưởng 2, "
        "Triple Captain 3).",
        f"xP là dự báo vòng {next_gw}; EO là số đo vòng {gw}. Hai vế lệch nhau "
        f"một vòng — đó là cái giá của việc dùng số đếm được thay cho số đoán.",
    ]
    if my_source == "squad_ids":
        out.append(
            "Đội của bạn nhận từ 15 id nên không biết ai đá chính, ai bắt băng — "
            "đang coi cả 15 là đá chính, tức phần 'tôi có' bị tính cao hơn thực tế. "
            "Nhập Team ID để đo đúng."
        )
    elif my_source == "none":
        out.append("Chưa có đội của bạn: chỉ hiện template của giải, không so sánh được.")
    if failed:
        out.append(f"{len(failed)} đối thủ không đọc được đội hình (đã loại khỏi mẫu).")
    return out

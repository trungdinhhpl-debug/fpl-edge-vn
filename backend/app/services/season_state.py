"""What phase the season is in, and how much of the model rests on last year.

A projection made before a ball is kicked and one made in December are not the
same claim, but a page that renders them identically invites the reader to treat
them identically. In pre-season almost every number here is a prior wearing a
decimal point: FPL's cumulative stats still describe LAST season, no minutes have
been observed, and team strength ratings are unset.

So the phase is computed from real data — finished fixtures, and how many
projections are still leaning on season averages rather than observed games —
and published site-wide so every page can say which kind of number it is showing.

Prior-season data is also down-weighted where its relevance is in doubt:

  * a player with no Premier League minutes at all (a signing from abroad, or a
    promoted club's squad) — detected;
  * a club that changed manager, or a player who changed club — NOT detectable
    from the FPL API, which publishes neither. These come from a configured list
    (`NEW_MANAGER_CLUBS`, `NEW_SIGNING_PLAYERS`) that a human maintains, and the
    payload reports whether that list has been filled in, so an empty one reads
    as "nobody told us", never as "nothing changed".
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ExpectedMinutes, Fixture, Player

# Matches per team below which the current season cannot outweigh the last one.
EARLY_SEASON_MATCHES = 6


def played_condition():
    """Điều kiện SQL cho "trận này đã đá xong rồi".

    KHÔNG dùng riêng `Fixture.finished`: FPL chỉ bật cờ đó khi cả vòng được
    data-check, tức là nhiều ngày sau khi bóng đã lăn. Đo 2026-08-22, vòng 1 mùa
    2026/27: trận Arsenal 3-0 tối 21/08 đã có `minutes=90`, tỷ số cuối và điểm
    thưởng đã chốt (3/2/1), nhưng `finished=false` — chỉ `finished_provisional`
    là true, và `events[0].data_checked` cũng còn false.

    Hậu quả của việc đếm sai chỗ này không nằm ở một con số lẻ: `stats_season()`
    dùng nó để trả lời "các tổng cả-mùa trong bảng `players` thuộc mùa nào", và
    câu trả lời đó quyết định engine có quy đổi BPS sang luật mùa mới hay không.
    FPL reset thống kê ngay tại hạn vòng 1, nên trong khoảng chờ data-check, số
    liệu ĐÃ là của mùa mới trong khi cờ `finished` vẫn nói chưa đá trận nào —
    engine quy đổi BPS một lần thừa, và cả trang vẫn dán nhãn PRE-SEASON.

    `finished_provisional` là NULL ở những hàng ghi trước khi có cột này, nên
    phải OR với `finished` — NULL nghĩa là "chưa đồng bộ trường đó", không phải
    "chưa đá".
    """
    return or_(Fixture.finished.is_(True), Fixture.finished_provisional.is_(True))


def not_played_condition():
    """Phủ định của `played_condition()` — viết tay vì NULL không phủ định được.

    `NOT (false OR NULL)` ra NULL chứ không ra true, nên `~played_condition()` sẽ
    LOẠI mọi trận chưa đá thuộc hàng cũ (nơi `finished_provisional` là NULL) —
    lặng lẽ, không lỗi. Ở đây NULL phải được đọc là "chưa đá".
    """
    return and_(
        Fixture.finished.is_(False),
        or_(
            Fixture.finished_provisional.is_(False),
            Fixture.finished_provisional.is_(None),
        ),
    )


def is_played(fixture: Fixture) -> bool:
    """Cùng câu hỏi với `played_condition()`, cho danh sách đã nạp sẵn trong Python."""
    return bool(fixture.finished or fixture.finished_provisional)


def last_completed_gameweek(db: Session) -> int | None:
    """Vòng gần nhất đã đá XONG TOÀN BỘ — không phải vòng đã đá trận nào.

    Hai câu hỏi khác nhau và trang hay lẫn: vòng 1 mùa 2026/27 trải từ 21/08 đến
    24/08, nên suốt ba ngày đó vòng đã bắt đầu mà chưa xong. Trả về 1 trong khoảng
    ấy là nói với người đọc rằng số liệu vòng 1 đã chốt, trong khi còn trận chưa
    đá. `matches_played` mới là thứ đo tiến độ; trường này đo cái đã đóng lại.

    Trả None khi chưa vòng nào trọn vẹn — KHÔNG trả 0: vòng 0 không tồn tại, và
    một con số giả sẽ lặng lẽ đi vào phép trừ ở phía đọc.
    """
    total: dict[int, int] = {}
    done: dict[int, int] = {}
    for f in db.scalars(select(Fixture).where(Fixture.event.isnot(None))).all():
        total[f.event] = total.get(f.event, 0) + 1
        if is_played(f):
            done[f.event] = done.get(f.event, 0) + 1
    complete = [gw for gw, n in total.items() if done.get(gw, 0) == n]
    return max(complete) if complete else None


def data_as_of(db: Session):
    """Lần cuối lấy được dữ liệu từ FPL — mốc mà mọi con số trên trang phản ánh.

    Đọc từ `source_fetch_logs` chứ không phải từ `updated_at` của từng hàng: hàng
    không đổi giá trị thì không được ghi lại, nên thời điểm cập nhật hàng trả lời
    "lần cuối số này THAY ĐỔI", không phải "lần cuối ta còn biết số này đúng".
    """
    from app.models import SourceFetchLog

    return db.scalar(
        select(func.max(SourceFetchLog.fetched_at))
        .where(SourceFetchLog.source_name.like("FPL%"))
    )


def _configured_ids(raw: str) -> set[str]:
    return {p.strip().upper() for p in (raw or "").split(",") if p.strip()}


def new_manager_clubs() -> set[str]:
    """Club short names whose manager changed — from config, not from the API."""
    return _configured_ids(settings.new_manager_clubs)


def new_signing_players() -> set[str]:
    """FPL player ids (as strings) flagged as new signings — from config."""
    return _configured_ids(settings.new_signing_players)


def prior_reliability(player: Player, team_short: str | None) -> float:
    """How far last season's numbers still describe THIS player, 0..1.

    Used to widen the shrinkage in the minutes model: less trust in the sample
    means the estimate leans harder on the positional prior instead. It never
    zeroes the data — a striker who moved clubs is still a striker.
    """
    w = 1.0
    if str(player.id) in new_signing_players():
        w *= settings.prior_weight_new_signing
    if team_short and team_short.upper() in new_manager_clubs():
        w *= settings.prior_weight_new_manager
    return round(max(0.1, w), 3)


def stats_season(db: Session, current_season: str | None = None) -> str | None:
    """Mùa mà các tổng cả-mùa trong bảng `players` thực sự thuộc về.

    Trước khi vòng 1 kết thúc, `bootstrap-static.elements` vẫn phát tổng của mùa
    TRƯỚC (kiểm chứng 2026-08-05: Haaland 2953 phút / 239 điểm trong khi hạn vòng
    1 là 2026-08-21). Với đa số hạng mục điều đó chỉ là "dữ liệu cũ", nhưng BPS
    thì khác: luật BPS đổi giữa hai mùa, nên con số cũ được kiếm theo một thước
    đo khác và phải quy đổi trước khi dùng (`app/bps_rules.equivalent_bps`).

    Trả None nếu không xác định được tên mùa trước — khi đó engine không quy đổi.
    """
    from app import bps_rules, scoring

    finished = db.scalar(
        select(func.count()).select_from(Fixture).where(played_condition())
    ) or 0
    current = current_season or scoring.SEASON
    if finished == 0:
        return bps_rules.previous_season(current)
    return current


def classify_phase(finished: int, max_team_matches: int) -> tuple[str, str | None, str]:
    """(phase, banner label, system confidence) from match counts alone.

    Kept pure so all three phases are testable without fabricating fixtures —
    the demo database happens to sit mid-season while production is pre-season,
    and a test that assumed either would only check one branch.
    """
    if finished == 0:
        return "preseason", "PRE-SEASON PROJECTION", "Low"
    if max_team_matches < EARLY_SEASON_MATCHES:
        return "early", "EARLY-SEASON PROJECTION", "Medium"
    return "established", None, "High"


def season_state(db: Session) -> dict:
    """Phase, evidence counts and the system-wide confidence label."""
    finished = db.scalar(
        select(func.count()).select_from(Fixture).where(played_condition())
    ) or 0

    # Matches for the busiest team — a blank/uneven start should not read as
    # "the season has begun" for everybody.
    per_team: dict[int, int] = {}
    for f in db.scalars(select(Fixture).where(played_condition())).all():
        per_team[f.team_h] = per_team.get(f.team_h, 0) + 1
        per_team[f.team_a] = per_team.get(f.team_a, 0) + 1
    max_team_matches = max(per_team.values()) if per_team else 0

    # Share of projections NOT built on games observed this season.
    #
    # Counted by what is ABSENT (no "recent games" in the reason) rather than by
    # matching one phrase. An earlier version looked for "season averages" and
    # reported 64% in a week when no match had been played — it silently missed
    # the promoted-club role estimates and the availability-flagged players,
    # which are every bit as much priors. The complement is the honest measure:
    # anything the model did not see happen this season is a prior.
    total_est = db.scalar(select(func.count()).select_from(ExpectedMinutes)) or 0
    observed = db.scalar(
        select(func.count()).select_from(ExpectedMinutes)
        .where(ExpectedMinutes.reason.like("%recent games%"))
    ) or 0
    prior_based = total_est - observed
    prior_share = round(100 * prior_based / total_est, 1) if total_est else None

    phase, label, confidence = classify_phase(finished, max_team_matches)

    managers = new_manager_clubs()
    signings = new_signing_players()

    from app.services.common import iso_utc

    fetched = data_as_of(db)
    return {
        "phase": phase,
        "label": label,
        "is_preseason": phase == "preseason",
        "system_confidence": confidence,
        "matches_played": finished,
        # Vòng đã ĐÓNG hẳn, khác `matches_played` vốn đếm tiến độ đang chạy.
        "last_completed_gw": last_completed_gameweek(db),
        # Mốc thời gian của dữ liệu, đi kèm chính những con số nó sinh ra.
        "data_as_of": iso_utc(fetched) if fetched else None,
        "max_team_matches": max_team_matches,
        "matches_until_established": max(0, EARLY_SEASON_MATCHES - max_team_matches),
        "prior_based_share_pct": prior_share,
        "prior_based_count": prior_based,
        "projection_count": total_est,
        "downweighting": {
            "new_manager_clubs": sorted(managers),
            "new_signing_players": sorted(signings),
            "configured": bool(managers or signings),
            "weight_new_manager": settings.prior_weight_new_manager,
            "weight_new_signing": settings.prior_weight_new_signing,
            "note": (
                "FPL API không công bố huấn luyện viên hay lịch sử chuyển nhượng, "
                "nên hai danh sách này do người vận hành khai báo. Danh sách rỗng "
                "nghĩa là CHƯA AI KHAI, không phải là không có ai đổi đội/đổi HLV."
            ),
        },
        "note": _phase_note(phase, finished, prior_share),
    }


def _phase_note(phase: str, finished: int, prior_share: float | None) -> str:
    if phase == "preseason":
        share = f"{prior_share:.0f}%" if prior_share is not None else "phần lớn"
        return (
            f"Ngoại hạng Anh mùa này chưa đá trận nào ({finished} trận). "
            f"{share} dự báo phút thi đấu đang dựa trên dữ liệu mùa trước và "
            f"tiền mùa, không phải trên trận đã đá. Hãy đọc mọi con số ở đây như "
            f"một điểm khởi đầu, không phải một kết luận."
        )
    if phase == "early":
        return (
            "Mùa giải mới bắt đầu: mẫu còn nhỏ nên dự báo vẫn nghiêng về dữ liệu "
            "mùa trước. Độ tin cậy tăng dần theo số trận đã đá."
        )
    return "Đã đủ số trận trong mùa để dự báo chủ yếu dựa trên dữ liệu mùa này."

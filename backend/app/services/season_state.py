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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ExpectedMinutes, Fixture, Player

# Matches per team below which the current season cannot outweigh the last one.
EARLY_SEASON_MATCHES = 6


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
        select(func.count()).select_from(Fixture).where(Fixture.finished.is_(True))
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
        select(func.count()).select_from(Fixture).where(Fixture.finished.is_(True))
    ) or 0

    # Matches for the busiest team — a blank/uneven start should not read as
    # "the season has begun" for everybody.
    per_team: dict[int, int] = {}
    for f in db.scalars(select(Fixture).where(Fixture.finished.is_(True))).all():
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

    return {
        "phase": phase,
        "label": label,
        "is_preseason": phase == "preseason",
        "system_confidence": confidence,
        "matches_played": finished,
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

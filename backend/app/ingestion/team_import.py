"""Import a manager's squad by FPL Team ID (spec §14).

Uses only public entry endpoints. Note: purchase/selling prices are NOT exposed
by the public API (they require an authenticated /my-team/ call), so we
approximate selling price with the current price and let the user override
free transfers / bank in the UI.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.common import iso_utc
from app.models import UserProfile
from app.providers.fpl_client import FPLClient, FPLNotFound


def upsert_profile(db: Session, team_id: int, **values) -> UserProfile:
    """Insert-or-update a manager profile, keyed on `fpl_team_id`.

    NOT db.merge(): merge matches on the primary key (`id`), which is None for a
    new object, so importing the same Team ID twice would violate the unique
    constraint on fpl_team_id.
    """
    profile = db.scalar(select(UserProfile).where(UserProfile.fpl_team_id == team_id))
    if profile is None:
        profile = UserProfile(fpl_team_id=team_id)
        db.add(profile)
    for key, val in values.items():
        setattr(profile, key, val)
    profile.last_synced = datetime.now(timezone.utc)
    db.commit()
    db.refresh(profile)
    return profile


def import_team(db: Session, team_id: int) -> dict:
    with FPLClient() as client:
        entry = client.entry(team_id)
        history = client.entry_history(team_id)

        # find the latest gameweek that has picks. Before the season starts the
        # history is empty, so fall back to the entry's first gameweek.
        current_events = history.get("current", [])
        latest_gw = (
            current_events[-1]["event"]
            if current_events
            else entry.get("started_event") or None
        )

        picks_data = {}
        picks_error: str | None = None
        if latest_gw:
            try:
                picks_data = client.entry_picks(team_id, latest_gw)
            except FPLNotFound:
                # FPL hides a manager's squad until that gameweek's deadline
                # passes (so nobody can copy it) -> 404 before the deadline.
                picks_data = {}
                picks_error = "not_public_yet"
            except Exception as exc:
                picks_data = {}
                picks_error = str(exc)[:120]

    eh = picks_data.get("entry_history", {})
    bank = eh.get("bank", entry.get("last_deadline_bank", 0) or 0)
    value = eh.get("value", entry.get("last_deadline_value", 1000) or 1000)

    # estimate free transfers (public API can't give the exact banked count)
    free_transfers = _estimate_free_transfers(current_events)

    profile = upsert_profile(
        db,
        team_id,
        player_name=f"{entry.get('player_first_name', '')} {entry.get('player_last_name', '')}".strip(),
        team_name=entry.get("name"),
        overall_rank=entry.get("summary_overall_rank"),
        bank=bank,
        team_value=value,
        free_transfers=free_transfers,
    )

    picks = [
        {
            "element": p["element"],
            "position": p["position"],
            "is_captain": p.get("is_captain", False),
            "is_vice_captain": p.get("is_vice_captain", False),
            "multiplier": p.get("multiplier", 1),
        }
        for p in picks_data.get("picks", [])
    ]

    chips_used = [c["name"] for c in history.get("chips", [])]

    return {
        "team_id": team_id,
        "player_name": profile.player_name,
        "team_name": profile.team_name,
        "overall_rank": profile.overall_rank,
        "bank": bank,
        "team_value": value,
        "free_transfers": free_transfers,
        "current_gameweek": latest_gw,
        "picks": picks,
        "has_squad": len(picks) == 15,
        # why the squad isn't here yet + when it will be (spec §5: be explicit)
        "squad_status": _squad_status(db, latest_gw, picks, picks_error),
        "chips_used": chips_used,
        "history": [
            {"event": h["event"], "points": h["points"], "rank": h.get("overall_rank")}
            for h in current_events
        ],
        "note": "Selling prices approximated at current price (public API limit).",
    }


def _squad_status(db: Session, gw: int | None, picks: list, error: str | None) -> dict:
    """Explain squad availability, with the exact deadline it unlocks."""
    from app.models import Gameweek

    if len(picks) == 15:
        return {"code": "ok", "gameweek": gw, "available_after": None, "message": ""}

    # Từ 2026/27, đội hình chỉ mở sau khi vòng đấu KẾT THÚC (lockdown muộn hơn
    # trước, không còn mở ngay sau hạn chót). Mốc chính xác = trận cuối của vòng
    # kết thúc, ước tính bằng giờ bóng lăn trận cuối + 2 tiếng.
    from datetime import timedelta

    from app.models import Fixture

    deadline = None
    unlock = None
    if gw:
        row = db.get(Gameweek, gw)
        if row and row.deadline_time:
            deadline = iso_utc(row.deadline_time)
        last_kick = db.scalar(
            select(func.max(Fixture.kickoff_time)).where(Fixture.event == gw)
        )
        if last_kick:
            unlock = (last_kick + timedelta(hours=2)).isoformat()

    if error == "not_public_yet":
        return {
            "code": "hidden_until_round_ends",
            "gameweek": gw,
            "deadline": deadline,
            "available_after": unlock or deadline,
            "message": (
                f"FPL giữ kín đội hình của bạn cho tới khi vòng {gw} kết thúc "
                f"(để không ai xem trước đội người khác). Sau trận cuối của vòng, "
                f"nhập lại Team ID là tải được ngay."
            ),
        }
    return {
        "code": "no_squad",
        "gameweek": gw,
        "available_after": deadline,
        "message": "Chưa lấy được đội hình 15 cầu thủ cho vòng này.",
    }


def _estimate_free_transfers(events: list[dict]) -> int:
    """Ước lượng số free transfer đang có (API công khai không cho biết chính xác).

    Trần lấy từ luật mùa hiện tại, không ghi cứng.
    """
    from app.scoring import GAME

    cap = GAME.max_free_transfers
    if not events:
        return 1
    ft = 1
    for h in events:
        used = h.get("event_transfers", 0)
        ft = max(1, min(cap, ft + 1 - used))
    return ft

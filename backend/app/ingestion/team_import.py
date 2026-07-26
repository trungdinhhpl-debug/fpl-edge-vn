"""Import a manager's squad by FPL Team ID (spec §14).

Uses only public entry endpoints. Note: purchase/selling prices are NOT exposed
by the public API (they require an authenticated /my-team/ call), so we
approximate selling price with the current price and let the user override
free transfers / bank in the UI.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UserProfile
from app.providers.fpl_client import FPLClient


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

        # find the latest gameweek that has picks
        current_events = history.get("current", [])
        latest_gw = current_events[-1]["event"] if current_events else None

        picks_data = {}
        if latest_gw:
            try:
                picks_data = client.entry_picks(team_id, latest_gw)
            except Exception:
                picks_data = {}

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
        # pre-season, or a manager who hasn't picked a squad yet, returns no picks
        "has_squad": len(picks) == 15,
        "chips_used": chips_used,
        "history": [
            {"event": h["event"], "points": h["points"], "rank": h.get("overall_rank")}
            for h in current_events
        ],
        "note": "Selling prices approximated at current price (public API limit).",
    }


def _estimate_free_transfers(events: list[dict]) -> int:
    """Rough banked-FT estimate. 2025/26 allows up to 5 banked."""
    if not events:
        return 1
    ft = 1
    for h in events:
        used = h.get("event_transfers", 0)
        ft = min(5, ft + 1 - used)
        ft = max(1, ft)
    return ft

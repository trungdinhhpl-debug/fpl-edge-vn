"""Dashboard / gameweek summary service (spec §16)."""
from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models import Gameweek, Player, PlayerProjection, SourceFetchLog
from app.services.captains import captain_ranking
from app.services.common import (
    blank_double_gws,
    current_gameweek,
    next_gameweek,
    planning_start_gw,
    player_public,
    projections_for_gw,
    team_lookup,
    iso_utc,
)
from app.services.news import news_feed


def gameweek_status(db: Session) -> dict:
    cur = current_gameweek(db)
    nxt = next_gameweek(db)
    return {
        "current": _gw_dict(cur),
        "next": _gw_dict(nxt),
        "deadline": iso_utc(nxt.deadline_time) if nxt and nxt.deadline_time else None,
        "planning_start_gw": planning_start_gw(db),
    }


def _gw_dict(gw: Gameweek | None) -> dict | None:
    if not gw:
        return None
    return {
        "id": gw.id, "name": gw.name,
        "deadline": iso_utc(gw.deadline_time) if gw.deadline_time else None,
        "finished": gw.finished, "is_current": gw.is_current, "is_next": gw.is_next,
        "average_score": gw.average_entry_score,
    }


def last_updated(db: Session) -> list[dict]:
    rows = db.scalars(
        select(SourceFetchLog).order_by(desc(SourceFetchLog.fetched_at)).limit(12)
    ).all()
    return [
        {
            "source": r.source_name, "type": r.source_type, "status": r.status,
            "rows": r.rows, "detail": r.detail,
            "fetched_at": iso_utc(r.fetched_at) if r.fetched_at else None,
        }
        for r in rows
    ]


def dashboard(db: Session) -> dict:
    teams = team_lookup(db)
    start = planning_start_gw(db)
    projs = projections_for_gw(db, start)
    players = {p.id: p for p in db.scalars(select(Player)).all()}

    def merge(pid: int) -> dict:
        p = players[pid]
        pr = projs.get(pid)
        base = player_public(p, teams.get(p.team_id))
        base.update({
            "xp_next": round(pr.xp, 2) if pr else 0.0,
            "xmins": round(pr.xmins, 1) if pr else 0.0,
            "ceiling": round(pr.mc_ceiling, 1) if pr else 0.0,
            "overall_risk": pr.overall_risk if pr else None,
            "confidence": pr.confidence if pr else None,
        })
        return base

    # top predicted (min minutes filter)
    ranked = sorted(
        [pid for pid, pr in projs.items() if pr.xp > 0],
        key=lambda pid: projs[pid].xp, reverse=True,
    )
    top_predicted = [merge(pid) for pid in ranked[:10]]

    # top transfers in (market movement — NOT used as quality evidence)
    top_in = sorted(players.values(), key=lambda p: p.transfers_in_event, reverse=True)[:8]
    top_transfers = [
        {**player_public(p, teams.get(p.team_id)),
         "transfers_in_event": p.transfers_in_event,
         "xp_next": round(projs[p.id].xp, 2) if p.id in projs else 0.0}
        for p in top_in
    ]

    caps = captain_ranking(db, start, limit=5)["lists"]["ev"]["players"]
    injuries = [n for n in news_feed(db, limit=40) if n["impact"] in ("Critical", "High")][:8]
    bd = blank_double_gws(db, start, start + 8)

    return {
        "gameweek": gameweek_status(db),
        "top_predicted": top_predicted,
        "top_transfers_in": top_transfers,
        "captain_top": caps,
        "injury_alerts": injuries,
        "blank_double": bd,
        "last_updated": last_updated(db),
    }

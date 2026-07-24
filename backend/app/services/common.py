"""Shared service helpers: gameweek resolution, projection loading, team lookup."""
from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExpectedMinutes,
    Fixture,
    Gameweek,
    Player,
    PlayerProjection,
    Team,
)
from app.scoring import position_name


def team_lookup(db: Session) -> dict[int, Team]:
    return {t.id: t for t in db.scalars(select(Team)).all()}


def current_gameweek(db: Session) -> Gameweek | None:
    return db.scalar(select(Gameweek).where(Gameweek.is_current.is_(True)))


def next_gameweek(db: Session) -> Gameweek | None:
    nxt = db.scalar(select(Gameweek).where(Gameweek.is_next.is_(True)))
    if nxt:
        return nxt
    cur = current_gameweek(db)
    if cur:
        return db.get(Gameweek, cur.id + 1)
    return db.scalars(
        select(Gameweek).where(Gameweek.finished.is_(False)).order_by(Gameweek.id)
    ).first()


def planning_start_gw(db: Session) -> int:
    nxt = next_gameweek(db)
    return nxt.id if nxt else 1


def projections_for_gw(db: Session, gw: int) -> dict[int, PlayerProjection]:
    rows = db.scalars(
        select(PlayerProjection).where(PlayerProjection.gameweek == gw)
    ).all()
    return {r.player_id: r for r in rows}


def xmins_for_gw(db: Session, gw: int) -> dict[int, ExpectedMinutes]:
    rows = db.scalars(
        select(ExpectedMinutes).where(ExpectedMinutes.gameweek == gw)
    ).all()
    return {r.player_id: r for r in rows}


def horizon_xp(db: Session, gws: list[int]) -> dict[int, float]:
    rows = db.scalars(
        select(PlayerProjection).where(PlayerProjection.gameweek.in_(gws))
    ).all()
    out: dict[int, float] = defaultdict(float)
    for r in rows:
        out[r.player_id] += r.xp
    return out


def gw_fixture_count_by_team(db: Session, gw: int) -> dict[int, int]:
    row = db.get(Gameweek, gw)
    if row and row.fixture_count_by_team:
        return {int(k): v for k, v in json.loads(row.fixture_count_by_team).items()}
    return {}


def blank_double_gws(db: Session, from_gw: int, to_gw: int) -> dict[int, dict]:
    """Return {gw: {"blank": [team_ids], "double": [team_ids]}} in a range."""
    out: dict[int, dict] = {}
    for gw in range(from_gw, to_gw + 1):
        counts = gw_fixture_count_by_team(db, gw)
        if not counts:
            continue
        blanks = [tid for tid, c in counts.items() if c == 0]
        doubles = [tid for tid, c in counts.items() if c >= 2]
        if blanks or doubles:
            out[gw] = {"blank": blanks, "double": doubles}
    return out


def player_public(p: Player, team: Team | None) -> dict:
    """Base player fields shared by every response."""
    return {
        "id": p.id,
        "code": p.code,
        "name": p.web_name,
        "full_name": f"{p.first_name} {p.second_name}".strip(),
        "team_id": p.team_id,
        "team": team.short_name if team else "",
        "team_name": team.name if team else "",
        "position": position_name(p.element_type),
        "element_type": p.element_type,
        "price": round(p.now_cost / 10.0, 1),
        "selected_by_percent": p.selected_by_percent,
        "status": p.status,
        "chance_of_playing": p.chance_of_playing_next_round,
        "news": p.news,
        "total_points": p.total_points,
        "form": p.form,
        "minutes": p.minutes,
        "photo_code": p.photo_code,
        "penalties_order": p.penalties_order,
    }

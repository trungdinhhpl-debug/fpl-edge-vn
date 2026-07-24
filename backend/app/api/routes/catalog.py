"""Read endpoints: gameweek, players, fixtures, captains, news, experts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Fixture, Team
from app.services.captains import captain_ranking
from app.services.common import team_lookup
from app.services.fixtures import fixture_ticker
from app.services.gameweek import dashboard, gameweek_status
from app.services.news import expert_consensus, news_feed
from app.services.players import compare_players, list_players, player_detail

router = APIRouter()


@router.get("/gameweek/current")
def gw_current(db: Session = Depends(get_db)) -> dict:
    return gameweek_status(db)


@router.get("/dashboard")
def get_dashboard(db: Session = Depends(get_db)) -> dict:
    return dashboard(db)


@router.get("/players")
def get_players(
    position: str | None = None,
    team_id: int | None = None,
    max_price: float | None = None,
    min_xp: float | None = None,
    limit: int = Query(800, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    return {"players": list_players(db, position, team_id, max_price, min_xp, limit)}


@router.get("/players/compare")
def players_compare(ids: str, db: Session = Depends(get_db)) -> dict:
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    return {"players": compare_players(db, id_list)}


@router.get("/players/{player_id}")
def get_player(player_id: int, db: Session = Depends(get_db)) -> dict:
    d = player_detail(db, player_id)
    if not d:
        raise HTTPException(404, "Player not found")
    return d


@router.get("/players/{player_id}/projections")
def get_player_projections(player_id: int, db: Session = Depends(get_db)) -> dict:
    d = player_detail(db, player_id)
    if not d:
        raise HTTPException(404, "Player not found")
    return {"player_id": player_id, "horizon": d["horizon"]}


@router.get("/teams")
def get_teams(db: Session = Depends(get_db)) -> dict:
    teams = team_lookup(db)
    return {"teams": [
        {"id": t.id, "name": t.name, "short_name": t.short_name}
        for t in sorted(teams.values(), key=lambda x: x.name)
    ]}


@router.get("/fixtures")
def get_fixtures(event: int | None = None, db: Session = Depends(get_db)) -> dict:
    q = select(Fixture)
    if event:
        q = q.where(Fixture.event == event)
    teams = team_lookup(db)
    rows = db.scalars(q.order_by(Fixture.event, Fixture.kickoff_time)).all()
    return {"fixtures": [
        {
            "id": f.id, "event": f.event,
            "kickoff_time": f.kickoff_time.isoformat() if f.kickoff_time else None,
            "home": teams.get(f.team_h).short_name if f.team_h in teams else "?",
            "away": teams.get(f.team_a).short_name if f.team_a in teams else "?",
            "home_id": f.team_h, "away_id": f.team_a,
            "finished": f.finished,
            "score": (f"{f.team_h_score}-{f.team_a_score}"
                      if f.team_h_score is not None else None),
        }
        for f in rows
    ]}


@router.get("/fixtures/ticker")
def get_ticker(start_gw: int | None = None, n_gws: int = Query(8, le=12),
               db: Session = Depends(get_db)) -> dict:
    return fixture_ticker(db, start_gw, n_gws)


@router.get("/captains")
def get_captains(gameweek: int | None = None, limit: int = 20,
                 db: Session = Depends(get_db)) -> dict:
    return captain_ranking(db, gameweek, limit)


@router.get("/news")
def get_news(impact: str | None = None, limit: int = 100,
             db: Session = Depends(get_db)) -> dict:
    return {"news": news_feed(db, impact, limit)}


@router.get("/expert-consensus")
def get_expert_consensus(db: Session = Depends(get_db)) -> dict:
    return expert_consensus(db)

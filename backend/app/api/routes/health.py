"""Health, model & source status, and admin refresh (spec §21, §5)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.db import session_scope
from app.models import (
    ExpertSignal,
    Fixture,
    Player,
    PlayerProjection,
    SourceFetchLog,
)
from app.scoring import SEASON

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "season": SEASON,
            "time": datetime.now(timezone.utc).isoformat()}


@router.get("/model/health")
def model_health(db: Session = Depends(get_db)) -> dict:
    from app.models import MarketOdds

    n_players = db.scalar(select(func.count()).select_from(Player)) or 0
    n_proj = db.scalar(select(func.count()).select_from(PlayerProjection)) or 0
    latest = db.scalar(select(func.max(PlayerProjection.data_cutoff)))

    n_odds = db.scalar(select(func.count()).select_from(MarketOdds)) or 0
    odds_gws = sorted({
        gw for (gw,) in db.execute(select(MarketOdds.gameweek).distinct()).all() if gw
    })
    odds_at = db.scalar(select(func.max(MarketOdds.fetched_at)))

    from app.models import ChampionshipStats

    champ_rows = db.scalars(select(ChampionshipStats)).all()

    return {
        "model_version": settings.model_version,
        "players": n_players,
        "projections": n_proj,
        "championship_data": {
            "enabled": settings.championship_enabled,
            "teams_covered": len(champ_rows),
            "season": champ_rows[0].season if champ_rows else None,
            "source": champ_rows[0].source_name if champ_rows else None,
            "damping": settings.championship_damping,
            "note": (
                "Chỉ dùng để xếp hạng các đội mới lên hạng so với nhau, luôn giữ "
                "dưới mức trung bình Ngoại hạng. Tắt bằng CHAMPIONSHIP_ENABLED=false."
            ),
        },
        "market_odds": {
            "enabled": bool(settings.odds_api_key),
            "fixtures_covered": n_odds,
            "gameweeks": odds_gws,
            "market_weight": settings.odds_market_weight,
            "last_fetched": odds_at.isoformat() if odds_at else None,
            "note": (
                "Vòng có kèo dùng đồng thuận nhà cái; vòng không có dùng mô hình "
                "nội bộ (model estimate)."
            ),
        },
        "montecarlo_iterations": settings.montecarlo_iterations,
        "projection_horizon": settings.projection_horizon,
        "last_projection_cutoff": latest.isoformat() if latest else None,
        "ready": n_proj > 0,
    }


@router.get("/sources/health")
def sources_health(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(SourceFetchLog).order_by(SourceFetchLog.fetched_at.desc()).limit(20)
    ).all()
    now = datetime.now(timezone.utc)
    sources = []
    for r in rows:
        age_min = None
        if r.fetched_at:
            fetched = r.fetched_at if r.fetched_at.tzinfo else r.fetched_at.replace(tzinfo=timezone.utc)
            age_min = round((now - fetched).total_seconds() / 60, 1)
        stale = age_min is not None and age_min > 240
        sources.append({
            "source": r.source_name, "type": r.source_type, "status": r.status,
            "rows": r.rows, "age_minutes": age_min,
            "flag": "stale" if stale else r.status,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
        })
    return {"sources": sources, "warning": any(s["flag"] == "stale" for s in sources)}


@router.post("/admin/refresh")
def admin_refresh(background: BackgroundTasks, detail: bool = False) -> dict:
    """Trigger a full data + projection rebuild in the background."""
    def _job() -> None:
        from app.ingestion.fpl_sync import run_full_sync
        with session_scope() as db:
            run_full_sync(db, build_proj=True, detail=detail)

    background.add_task(_job)
    return {"status": "started", "message": "Sync + projections running in background."}

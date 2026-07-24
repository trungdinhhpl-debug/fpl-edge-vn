"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.api import api_router
from app.config import settings
from app.db import SessionLocal, init_db
from app.models import Player

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("fpl-edge")


def _db_is_empty() -> bool:
    with SessionLocal() as db:
        n = db.scalar(select(func.count()).select_from(Player)) or 0
    return n == 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("Database initialised (%s)", settings.database_url.split("://")[0])

    if settings.auto_sync_on_startup and _db_is_empty():
        log.info("Empty DB detected — starting initial FPL sync in the background...")

        def _initial_sync() -> None:
            try:
                from app.db import session_scope
                from app.ingestion.fpl_sync import run_full_sync
                with session_scope() as db:
                    result = run_full_sync(db, build_proj=True, detail=settings.sync_players_detail)
                log.info("Initial sync complete: %s", result.get("projections"))
            except Exception as exc:
                log.warning("Initial sync failed (%s). Call POST /api/admin/refresh later.", exc)

        # run off the startup path so the server binds its port immediately
        # (cloud platforms health-check the port and would otherwise time out)
        threading.Thread(target=_initial_sync, name="initial-sync", daemon=True).start()

    if settings.enable_scheduler:
        _start_scheduler(app)

    yield

    sched = getattr(app.state, "scheduler", None)
    if sched:
        sched.shutdown(wait=False)


def _start_scheduler(app: FastAPI) -> None:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        def _refresh() -> None:
            from app.db import session_scope
            from app.ingestion.fpl_sync import run_full_sync
            with session_scope() as db:
                run_full_sync(db, build_proj=True)

        sched = BackgroundScheduler(timezone="UTC")
        sched.add_job(_refresh, "interval", hours=6, id="fpl_refresh")
        sched.start()
        app.state.scheduler = sched
        log.info("Background scheduler started (6h refresh).")
    except Exception as exc:
        log.warning("Scheduler not started: %s", exc)


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Data-driven Fantasy Premier League decision engine — expected points, "
        "expected minutes, Monte Carlo and MILP optimisation. Independent fan "
        "project, not affiliated with the Premier League or FPL."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/")
def root() -> dict:
    return {
        "app": settings.app_name,
        "docs": "/docs",
        "api_prefix": settings.api_prefix,
        "disclaimer": "Independent fan project. Not affiliated with the Premier League / FPL.",
    }

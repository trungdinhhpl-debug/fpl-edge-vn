"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
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


def _load_rules() -> None:
    """Nạp luật mùa hiện tại đã lưu (không ghi cứng trong code)."""
    try:
        from app import scoring
        with SessionLocal() as db:
            info = scoring.load_rules(db)
        log.info("Rules loaded: season=%s version=%s source=%s",
                 info.get("season"), info.get("rules_version"), info.get("source"))
    except Exception as exc:
        log.warning("Could not load season rules (%s) — using fallback.", exc)


def _db_bootstrap(app: FastAPI) -> None:
    """Mọi việc cần một kết nối DB sống.

    Tách riêng để chạy được ở hai nơi: ngay lúc khởi động, và trong luồng thử
    lại nếu lúc đó DB chưa tới được. Ném exception nếu DB vẫn hỏng — người gọi
    quyết định xử lý thế nào.
    """
    init_db()
    app.state.db_ready = True
    app.state.db_error = None
    log.info("Database initialised (%s)", settings.database_url.split("://")[0])

    _load_rules()

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


def _db_startup(app: FastAPI, ready: threading.Event) -> None:
    """Dựng DB trong luồng riêng, thử lại mãi, giãn dần 5s → 5 phút.

    Chạy ngoài luồng khởi động vì một DB không tới được KHÔNG phải lúc nào cũng
    báo lỗi: nếu gói tin bị nuốt (đúng kiểu hỏng hay gặp ở DB đám mây) thì lệnh
    kết nối treo im chứ không ném exception, và try/except quanh nó vô dụng.
    Đặt hẳn sang luồng khác thì cổng luôn mở được, hỏng kiểu gì cũng vậy.
    """
    delay = 5
    while True:
        try:
            _db_bootstrap(app)
            ready.set()
            return
        except Exception as exc:
            app.state.db_error = str(exc)
            ready.set()  # mở cổng ngay, đừng bắt cả service chờ DB
            log.warning("DB unreachable (%s) — retrying in %ss.", exc, delay)
            time.sleep(delay)
            delay = min(delay * 2, 300)


# DB lành thì dựng xong trong vài chục mili-giây; chờ một nhịp ngắn để giữ
# nguyên hành vi cũ (bảng có sẵn trước request đầu tiên). Quá hạn này thì mở
# cổng và phục vụ ở chế độ degraded — thà báo được bệnh còn hơn chết câm.
STARTUP_DB_WAIT_S = 10.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_ready = False
    app.state.db_error = None

    ready = threading.Event()
    threading.Thread(
        target=_db_startup, args=(app, ready), name="db-startup", daemon=True
    ).start()

    # chờ trong executor để không chặn event loop
    await asyncio.get_running_loop().run_in_executor(None, ready.wait, STARTUP_DB_WAIT_S)
    if not app.state.db_ready:
        log.error(
            "Database chưa sẵn sàng sau %.0fs — mở cổng và trả 'degraded' ở "
            "/api/health; luồng nền vẫn đang thử lại.", STARTUP_DB_WAIT_S,
        )

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

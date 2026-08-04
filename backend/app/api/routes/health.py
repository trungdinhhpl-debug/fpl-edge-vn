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
from app import scoring

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "season": scoring.SEASON,
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


def _by_pos_name(by_type: dict[int, int]) -> dict[str, int]:
    """Đổi khoá element_type sang tên vị trí cho dễ đọc trong API."""
    return {scoring.position_name(k): v for k, v in sorted(by_type.items())}


@router.get("/meta/version")
def meta_version(db: Session = Depends(get_db)) -> dict:
    """Nguồn gốc & phiên bản của mọi thứ đang hiển thị.

    Mùa giải và luật KHÔNG ghi cứng trong code — đọc từ bảng `seasons`, vốn được
    ingestion lưu nguyên văn từ FPL `bootstrap-static.game_config`.
    """
    import json

    from app.models import Season

    # nạp lại từ DB mỗi lần gọi: rẻ (1 truy vấn) và đảm bảo tiến trình chạy lâu
    # vẫn báo đúng luật hiện hành sau khi FPL đổi luật giữa mùa
    scoring.load_rules(db)
    season = db.scalar(select(Season).where(Season.is_current.is_(True)))
    last_data = db.scalar(
        select(func.max(SourceFetchLog.fetched_at)).where(
            SourceFetchLog.source_name.like("FPL%")
        )
    )
    last_model = db.scalar(select(func.max(PlayerProjection.data_cutoff)))

    chips: list[dict] = []
    if season and season.chips_json:
        try:
            raw = json.loads(season.chips_json)
            for c in raw:
                chips.append({
                    "name": c.get("name"),
                    "type": c.get("chip_type"),
                    "start_event": c.get("start_event"),
                    "stop_event": c.get("stop_event"),
                })
        except ValueError:
            chips = []

    # hai bộ chip cho hai nửa mùa: gom theo khoảng gameweek
    halves: dict[str, list[str]] = {}
    for c in chips:
        key = f"GW{c['start_event']}–{c['stop_event']}"
        halves.setdefault(key, []).append(c["name"])

    return {
        "season": scoring.SEASON,
        "season_source": season.scoring_source if season else "fallback",
        "rules_version": scoring.RULES_VERSION,
        "rules_updated_at": (
            season.rules_updated_at.isoformat()
            if season and season.rules_updated_at else None
        ),
        "rules_source": scoring.RULES.source,
        "projection_version": settings.model_version,
        "last_data_update": last_data.isoformat() if last_data else None,
        "last_model_run": last_model.isoformat() if last_model else None,
        "scoring": {
            "goal_points": _by_pos_name(scoring.RULES.goal_points),
            "clean_sheet_points": _by_pos_name(scoring.RULES.clean_sheet_points),
            "assist_points": scoring.RULES.assist_points,
            "defensive_contribution": _by_pos_name(scoring.RULES.defcon_points_by_pos),
            "defcon_thresholds": {
                "DEF": scoring.RULES.defcon_threshold_def,
                "MID_FWD": scoring.RULES.defcon_threshold_att,
            },
            "saves_per_point": scoring.RULES.saves_per_point,
        },
        "squad_rules": {
            "squad_size": scoring.GAME.squad_size,
            "starting_xi": scoring.GAME.squad_play,
            "max_per_club": scoring.GAME.team_limit,
            "budget": scoring.GAME.total_spend / 10,
            "max_free_transfers": scoring.GAME.max_free_transfers,
            "sell_on_fee": scoring.GAME.sell_on_fee,
        },
        "chips": chips,
        "chip_windows": halves,
        "note": (
            "Ngưỡng Defensive Contribution và số lần cứu thua cho mỗi điểm không có "
            "trong API nên được giữ trong cấu hình; mọi giá trị còn lại đọc trực tiếp "
            "từ FPL game_config."
        ),
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

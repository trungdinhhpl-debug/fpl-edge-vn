"""Health, model & source status, and admin refresh (spec §21, §5)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.db import session_scope
from app.services.common import iso_utc
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
def health(request: Request) -> dict:
    """Liveness + tình trạng DB.

    Luôn trả 200 kể cả khi DB hỏng: tiến trình vẫn sống thật, và một health
    check trả lỗi chỉ khiến nền tảng khởi động lại vô ích. Trạng thái thật nằm
    ở trường `status` ("ok" | "degraded") để người đọc log phân biệt được
    "app chết" với "app sống nhưng mất DB".
    """
    db_ready = getattr(request.app.state, "db_ready", True)
    out = {
        "status": "ok" if db_ready else "degraded",
        "app": settings.app_name,
        "season": scoring.SEASON,
        "db": "ok" if db_ready else "unreachable",
        "time": datetime.now(timezone.utc).isoformat(),
    }
    if not db_ready:
        # DB treo im (không ném lỗi) thì db_error còn rỗng — vẫn phải nói được
        # là đang ở tình trạng nào, chứ không trả về một chỗ trống khó hiểu.
        out["db_error"] = (
            getattr(request.app.state, "db_error", None)
            or "chưa có phản hồi từ máy chủ dữ liệu (đang thử lại nền)"
        )
    return out


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
            # trọng số trên bị hạ theo tỷ lệ khi trận có ít nhà cái ra giá hơn mức này
            "full_support_books": settings.odds_full_support_books,
            "consensus": "median per outcome/line, de-vigged per bookmaker",
            "last_fetched": iso_utc(odds_at) if odds_at else None,
            "inversion": {
                "method": "joint least-squares, Dixon–Coles score matrix",
                "markets": ["1X2", "over/under"]
                + (["asian_handicap"] if settings.odds_include_handicap else []),
                "weights": {
                    "1x2": settings.odds_weight_1x2,
                    "totals": settings.odds_weight_totals,
                    "handicap": settings.odds_weight_handicap,
                },
                "dixon_coles_rho": settings.odds_dixon_coles_rho,
            },
            "note": (
                "Vòng có kèo dùng đồng thuận nhà cái; vòng không có dùng mô hình "
                "nội bộ (model estimate). λ mỗi đội được khớp đồng thời với cả ba "
                "thị trường trên một ma trận tỷ số Dixon–Coles (ρ hiệu chỉnh các "
                "tỷ số thấp 0-0, 1-0, 0-1, 1-1)."
            ),
        },
        "montecarlo_iterations": settings.montecarlo_iterations,
        "projection_horizon": settings.projection_horizon,
        "last_projection_cutoff": iso_utc(latest) if latest else None,
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

    # Số lần luật đã đổi trong mùa này -> phần sau của nhãn 'v2026.N'.
    # Đếm dòng thật trong season_rules, không phải một con số tự đặt trong code.
    from app.models import SeasonRules

    revision = 1
    if season is not None:
        revision = max(
            1,
            db.scalar(
                select(func.count()).select_from(SeasonRules).where(
                    SeasonRules.season_id == season.id
                )
            ) or 1,
        )
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

    from app.services.season_state import season_state

    return {
        "season": scoring.SEASON,
        "season_source": season.scoring_source if season else "fallback",
        # Every page renders this, so the phase label travels with the numbers:
        # a pre-season projection and a December one must not look alike.
        "season_state": season_state(db),
        "rules_version": scoring.RULES_VERSION,
        # nhãn dễ đọc cho giao diện; băm ở trên vẫn là danh tính chính xác
        "rules_label": scoring.rules_label(revision),
        "rules_revision": revision,
        "rules_updated_at": (
            iso_utc(season.rules_updated_at)
            if season and season.rules_updated_at else None
        ),
        "rules_source": scoring.RULES.source,
        # BPS tách riêng: FPL không phát trọng số BPS qua API nên phiên bản này do
        # app/bps_rules.py khai, và ngày dưới đây là ngày FPL CÔNG BỐ luật.
        "bps_rules_version": scoring.BPS_RULES.version,
        "bps_rules_effective_from": scoring.BPS_RULES.effective_from,
        "bps_rules_source_url": scoring.BPS_RULES.source_url,
        "bps_rules_known": scoring.BPS_RULES_KNOWN,
        "projection_version": settings.model_version,
        "last_data_update": iso_utc(last_data) if last_data else None,
        "last_model_run": iso_utc(last_model) if last_model else None,
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
            "fetched_at": iso_utc(r.fetched_at) if r.fetched_at else None,
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

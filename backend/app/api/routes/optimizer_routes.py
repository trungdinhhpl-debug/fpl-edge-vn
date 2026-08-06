"""Team import/analyze + optimizer endpoints (spec §21)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.ingestion.team_import import import_team
from app.models import OptimizationRun
from app.schemas import (
    ChipCalendarRequest,
    FreeHitRequest,
    LongTermRequest,
    NextGwRequest,
    TeamAnalyzeRequest,
    TeamImportRequest,
    WildcardRequest,
)
from app.services import team as team_svc
from app.services.common import planning_start_gw

router = APIRouter()


def _persist_run(db: Session, kind: str, start_gw: int, horizon: int,
                 params: dict, result: dict) -> int:
    run = OptimizationRun(
        kind=kind, start_gw=start_gw, horizon=horizon,
        params_json=json.dumps(params, default=str),
        result_json=json.dumps(result, default=str),
        model_version=settings.model_version,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run.id


# ------------------------------------------------------------------ team -------
@router.post("/team/import")
def team_import(req: TeamImportRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return import_team(db, req.team_id)
    except Exception as exc:
        raise HTTPException(502, f"Could not import team {req.team_id}: {exc}")


@router.post("/team/analyze")
def team_analyze(req: TeamAnalyzeRequest, db: Session = Depends(get_db)) -> dict:
    return team_svc.analyze_team(db, req.squad_ids, req.bank)


# ------------------------------------------------------------- optimizers ------
@router.post("/optimizer/next-gameweek")
def opt_next_gw(req: NextGwRequest, db: Session = Depends(get_db)) -> dict:
    result = team_svc.optimize_next_gw(
        db, req.squad_ids, bank=req.bank, free_transfers=req.free_transfers,
        max_transfers=req.max_transfers,
    )
    gw = planning_start_gw(db)
    result["run_id"] = _persist_run(db, "next_gw", gw, 1, req.model_dump(), result)
    return result


@router.post("/optimizer/long-term")
def opt_long_term(req: LongTermRequest, db: Session = Depends(get_db)) -> dict:
    result = team_svc.optimize_long_term(
        db, req.squad_ids, bank=req.bank, free_transfers=req.free_transfers,
        horizon=req.horizon, discount=req.discount,
    )
    gw = planning_start_gw(db)
    result["run_id"] = _persist_run(db, "long_term", gw, req.horizon, req.model_dump(), result)
    return result


@router.post("/optimizer/free-hit")
def opt_free_hit(req: FreeHitRequest, db: Session = Depends(get_db)) -> dict:
    result = team_svc.optimize_free_hit(
        db, gw=req.gameweek, budget=req.budget, mode=req.mode,
        locked=set(req.locked), excluded=set(req.excluded),
    )
    gw = req.gameweek or planning_start_gw(db)
    result["run_id"] = _persist_run(db, "free_hit", gw, 1, req.model_dump(), result)
    return result


@router.post("/optimizer/wildcard")
def opt_wildcard(req: WildcardRequest, db: Session = Depends(get_db)) -> dict:
    result = team_svc.optimize_wildcard(
        db, budget=req.budget, horizon=req.horizon, mode=req.mode,
    )
    gw = planning_start_gw(db)
    result["run_id"] = _persist_run(db, "wildcard", gw, req.horizon, req.model_dump(), result)
    return result


@router.post("/chips/calendar")
def chip_calendar(req: ChipCalendarRequest, db: Session = Depends(get_db)) -> dict:
    """Bảng chip thống nhất cho cả 8 chip — xem app/services/chip_calendar.py."""
    from app.services.chip_calendar import chip_calendar as build

    return build(
        db,
        squad_ids=req.squad_ids,
        bank=req.bank,
        free_transfers=req.free_transfers,
        chips_used=req.chips_used,
    )


@router.post("/optimizer/transfer-verdict")
def opt_transfer_verdict(req: NextGwRequest, db: Session = Depends(get_db)) -> dict:
    """Khuyến nghị ROLL / TRANSFER theo cấu trúc cố định, kèm điều chỉnh và lý do."""
    from app.services.transfer_verdict import transfer_verdict

    return transfer_verdict(
        db, req.squad_ids, bank=req.bank, free_transfers=req.free_transfers
    )


@router.get("/model/performance")
def model_performance(db: Session = Depends(get_db)) -> dict:
    """Chất lượng dự báo đo bằng kết quả thật — xem app/services/model_performance.py."""
    from app.services.model_performance import model_performance as build

    return build(db)


@router.post("/model/snapshot")
def model_snapshot(db: Session = Depends(get_db), gameweek: int | None = None) -> dict:
    """Đóng băng dự báo của vòng sắp tới để sau này chấm được.

    Chạy tự động trong mỗi lần đồng bộ; endpoint này để chạy tay khi cần.
    """
    from app.services.model_performance import (
        capture_captain_picks,
        capture_snapshots,
        fill_outcomes,
    )

    captured = capture_snapshots(db, gameweek)
    captains = capture_captain_picks(db, gameweek)
    filled = fill_outcomes(db)
    db.commit()
    return {"captured": captured, "captain_picks": captains, "outcomes": filled}


@router.get("/chips/windows")
def chips_windows(db: Session = Depends(get_db)) -> dict:
    """Cửa sổ dùng của từng chip, đọc nguyên văn từ FPL (không ghi cứng)."""
    from app.services.chip_calendar import chip_windows

    return {"windows": chip_windows(db), "current_gameweek": planning_start_gw(db)}


@router.get("/optimization/{run_id}")
def get_optimization(run_id: int, db: Session = Depends(get_db)) -> dict:
    run = db.get(OptimizationRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return {
        "run_id": run.id, "kind": run.kind, "start_gw": run.start_gw,
        "horizon": run.horizon, "model_version": run.model_version,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "params": json.loads(run.params_json) if run.params_json else None,
        "result": json.loads(run.result_json) if run.result_json else None,
    }

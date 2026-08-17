"""Bảng lịch thi đấu — chạy trọn sáu bước của mô hình độ khó.

    BƯỚC 1  prior đội bóng            engine/prior_strength.py
    BƯỚC 2  log λ tuyến tính          engine/team_strength.py
    BƯỚC 3  hiệu chuẩn theo thị trường engine/team_strength.py
    BƯỚC 4  percentile độ dễ          engine/fixture_difficulty.py
    BƯỚC 5  điểm lịch cả cửa sổ       engine/fixture_difficulty.py
    BƯỚC 6  FDR ngũ phân vị           engine/fixture_difficulty.py

Module này chỉ nối dây và đóng gói ra JSON; không có quyết định mô hình nào ở đây.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.engine.fixture_difficulty import (
    POSITION_NAMES,
    POSITIONS,
    build_reference_players,
    rank_fixtures,
    rate_fixture,
    schedule_ease,
)
from app.engine.team_strength import (
    TeamStrength,
    load_market_map,
    load_market_maturity,
    load_market_support,
    load_match_xg,
    load_promoted_map,
)
from app.models import Fixture, Player, Team
from app.services.common import planning_start_gw, team_lookup

# Vai trò mặc định của bảng chính. Tiền vệ là vai trò đông nhất trong một đội hình
# FPL và là vai trò duy nhất ăn điểm cả từ tấn công lẫn sạch lưới, nên nó là mặc
# định ít thiên lệch nhất — nhưng cả bốn vai trò đều được trả về.
DEFAULT_ROLE = 3


def _build_ts(db: Session) -> TeamStrength:
    teams = db.scalars(select(Team)).all()
    players = db.scalars(select(Player)).all()
    all_fixtures = db.scalars(select(Fixture)).all()
    finished = [f for f in all_fixtures if f.finished]
    return TeamStrength(
        teams, players, finished,
        market=load_market_map(db),
        market_weight=settings.odds_market_weight,
        market_support=load_market_support(db),
        market_maturity=load_market_maturity(db),
        full_support_books=settings.odds_full_support_books,
        promoted=load_promoted_map(db),
        promoted_damping=settings.championship_damping,
        match_xg=load_match_xg(db),
        schedule=all_fixtures,
    )


def fixture_ticker(db: Session, start_gw: int | None = None, n_gws: int = 8) -> dict:
    from app.services.season_state import stats_season

    teams = team_lookup(db)
    if not teams:
        return {"gameweeks": [], "rows": [], "roles": [], "best_attack": [], "best_defence": []}

    ts = _build_ts(db)
    players = db.scalars(select(Player)).all()
    references = build_reference_players(players)
    season_of_stats = stats_season(db)

    start = start_gw or planning_start_gw(db)
    gws = list(range(start, start + n_gws))
    fixtures = db.scalars(select(Fixture).where(Fixture.event.in_(gws))).all()

    # ---- BƯỚC 4 (phần thô): chấm từng ô -------------------------------------
    by_team: dict[int, list] = {tid: [] for tid in teams}
    for f in fixtures:
        if f.event not in gws:
            continue
        has_ko = f.kickoff_time is not None
        for tid, opp, home in ((f.team_h, f.team_a, True), (f.team_a, f.team_h, False)):
            if tid not in by_team:
                continue
            by_team[tid].append(
                rate_fixture(
                    ts, tid, opp, f.event, home,
                    references=references,
                    fixture_id=f.id,
                    has_kickoff=has_ko,
                    stats_season=season_of_stats,
                )
            )

    all_ratings = [r for rows in by_team.values() for r in rows]
    rank_fixtures(all_ratings)          # BƯỚC 4: percentile + FDR từng ô

    # ---- BƯỚC 5 + 6: điểm lịch và FDR của cả cửa sổ, cho từng vai trò --------
    evidence = {
        tid: (ts.prior(tid).evidence_weight if ts.prior(tid) else 0.0) for tid in teams
    }
    ease_by_role: dict[int, dict[int, object]] = {}
    for pos in POSITIONS:
        rows = schedule_ease(by_team, gws, pos, evidence_weight=evidence)
        ease_by_role[pos] = {s.team_id: s for s in rows}

    # ---- đóng gói ------------------------------------------------------------
    out_rows = []
    for tid, team in teams.items():
        cells = {str(gw): [] for gw in gws}
        for r in by_team[tid]:
            cells[str(r.gameweek)].append(_cell(r, teams.get(r.opponent_id)))
        rows = by_team[tid]
        n_fix = len(rows)
        default = ease_by_role[DEFAULT_ROLE][tid]
        out_rows.append({
            "team_id": tid,
            "team": team.short_name,
            "team_name": team.name,
            "cells": cells,
            "n_fixtures": n_fix,
            "blanks": default.blanks,
            "doubles": default.doubles,
            # BƯỚC 5 + 6 cho cả bốn vai trò
            "schedule": {
                POSITION_NAMES[pos]: {
                    "ease": ease_by_role[pos][tid].ease,
                    "raw_ease": ease_by_role[pos][tid].raw_ease,
                    "uncertainty_penalty": ease_by_role[pos][tid].uncertainty_penalty,
                    "fdr": ease_by_role[pos][tid].fdr,
                }
                for pos in POSITIONS
            },
            # Tổng hợp thô, giữ lại vì hai thẻ dưới bảng vẫn đọc chúng và vì đó là
            # đơn vị người dùng hiểu ngay (bàn thắng, số trận sạch lưới).
            "sum_proj_goals": round(sum(r.proj_goals_for for r in rows), 2),
            "sum_clean_sheet_prob": round(sum(r.clean_sheet_prob for r in rows), 2),
            "avg_attack_ease": (
                round(sum(r.attack_ease for r in rows) / n_fix, 1) if n_fix else None
            ),
            "avg_defence_ease": (
                round(sum(r.defence_ease for r in rows) / n_fix, 1) if n_fix else None
            ),
            # Giữ tên cũ để không phá vỡ nơi khác đang đọc; giá trị giờ là FDR
            # ngũ phân vị chứ không còn là phép co tuyến tính.
            "avg_attack_difficulty": (
                round(sum(r.attack_difficulty for r in rows) / n_fix, 2) if n_fix else None
            ),
            "avg_defence_difficulty": (
                round(sum(r.defence_difficulty for r in rows) / n_fix, 2) if n_fix else None
            ),
        })

    by_default_ease = sorted(
        out_rows, key=lambda r: r["schedule"][POSITION_NAMES[DEFAULT_ROLE]]["ease"],
        reverse=True,
    )
    n_priced = sum(1 for r in all_ratings if r.has_market)
    return {
        "gameweeks": gws,
        "rows": by_default_ease,
        "roles": [POSITION_NAMES[p] for p in POSITIONS],
        "default_role": POSITION_NAMES[DEFAULT_ROLE],
        "best_attack": sorted(out_rows, key=lambda r: r["sum_proj_goals"], reverse=True)[:6],
        "best_defence": sorted(
            out_rows, key=lambda r: r["sum_clean_sheet_prob"], reverse=True
        )[:6],
        "model": _model_notes(ts, references, all_ratings, n_priced),
    }


def _model_notes(ts, references, ratings, n_priced: int) -> dict:
    """Những gì bảng này dựa vào, nói thẳng ngay trong payload."""
    n = len(ratings)
    return {
        "baseline_goals": round(ts.baseline, 3),
        "market_coverage": {
            "fixtures_with_odds": n_priced,
            "fixtures_total": n,
            "share": round(n_priced / n, 3) if n else 0.0,
        },
        "calibration": {
            "multiplier": round(ts.calibration_multiplier, 4),
            "applied": ts.calibration_multiplier != 1.0,
            "note": (
                "λ cấu trúc được nhân hệ số này để khớp mức chung của thị trường; "
                "hệ số đo trên các trận CÓ giá rồi áp cho cả những trận chưa có."
            ),
        },
        "reference_players": {
            POSITION_NAMES[pos]: references[pos].source
            for pos in POSITIONS
            if pos in references
        },
        "steps": [
            "BƯỚC 1 · prior 5 nguồn (45/25/15/10/5), chuẩn hoá lại theo nguồn thật sự có",
            "BƯỚC 2 · log λ cộng 10 số hạng",
            "BƯỚC 3 · blend hình học với λ nhà cái (Dixon–Coles) theo thanh khoản & độ trưởng thành",
            "BƯỚC 4 · percentile: Attack Ease, Defence Ease, Role Ease từng vai trò",
            "BƯỚC 5 · Schedule Ease = trung bình suy giảm theo thời gian − phạt bất định",
            "BƯỚC 6 · FDR 1–5 theo ngũ phân vị (mỗi bậc đúng 20% số đội)",
        ],
    }


def _cell(r, opp: Team | None) -> dict:
    return {
        "opponent": opp.short_name if opp else "?",
        "opponent_id": r.opponent_id,
        "is_home": r.is_home,
        # True = số liệu dựa trên kèo nhà cái; False = ước lượng từ mô hình nội bộ
        "has_market": r.has_market,
        "market_weight": r.market_weight,
        "proj_goals_for": r.proj_goals_for,
        "proj_goals_against": r.proj_goals_against,
        "clean_sheet_prob": r.clean_sheet_prob,
        "attack_ease": r.attack_ease,
        "defence_ease": r.defence_ease,
        "attack_difficulty": r.attack_difficulty,
        "defence_difficulty": r.defence_difficulty,
        "role_ease": {POSITION_NAMES[p]: v for p, v in r.role_ease.items()},
        "role_fdr": {POSITION_NAMES[p]: v for p, v in r.role_fdr.items()},
        "role_points": {POSITION_NAMES[p]: v for p, v in r.role_points.items()},
    }


def explain_fixture(
    db: Session, team_id: int, opponent_id: int, is_home: bool, fixture_id: int | None = None
) -> dict:
    """Phân rã `log λ` của một trận thành đúng các số hạng của BƯỚC 2 + BƯỚC 3."""
    ts = _build_ts(db)
    teams = team_lookup(db)
    prior = ts.prior(team_id)
    return {
        "team": teams[team_id].short_name if team_id in teams else str(team_id),
        "opponent": teams[opponent_id].short_name if opponent_id in teams else str(opponent_id),
        "is_home": is_home,
        "prior": prior.as_dict() if prior else None,
        "lambda_terms": ts.explain(team_id, opponent_id, is_home, fixture_id),
    }

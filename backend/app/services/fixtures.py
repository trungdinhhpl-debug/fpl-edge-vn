"""Fixture ticker service (spec §13)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.fixture_difficulty import rate_fixture
from app.engine.team_strength import TeamStrength
from app.models import Fixture, Player, Team
from app.services.common import gw_fixture_count_by_team, planning_start_gw, team_lookup


def _build_ts(db: Session) -> TeamStrength:
    teams = db.scalars(select(Team)).all()
    players = db.scalars(select(Player)).all()
    finished = db.scalars(select(Fixture).where(Fixture.finished.is_(True))).all()
    return TeamStrength(teams, players, finished)


def fixture_ticker(db: Session, start_gw: int | None = None, n_gws: int = 8) -> dict:
    teams = team_lookup(db)
    ts = _build_ts(db)
    start = start_gw or planning_start_gw(db)
    gws = list(range(start, start + n_gws))

    fixtures = db.scalars(
        select(Fixture).where(Fixture.event.in_(gws))
    ).all()

    # team -> gw -> list of ratings
    grid: dict[int, dict[int, list]] = {tid: {gw: [] for gw in gws} for tid in teams}
    for f in fixtures:
        if f.event not in gws:
            continue
        rh = rate_fixture(ts, f.team_h, f.team_a, f.event, True)
        ra = rate_fixture(ts, f.team_a, f.team_h, f.event, False)
        grid[f.team_h][f.event].append(_cell(rh, teams.get(f.team_a), True))
        grid[f.team_a][f.event].append(_cell(ra, teams.get(f.team_h), False))

    rows = []
    for tid, team in teams.items():
        cells = grid[tid]
        # aggregate difficulty across the window
        att_scores, def_scores, cs_sum, goals_sum = [], [], 0.0, 0.0
        for gw in gws:
            for c in cells[gw]:
                att_scores.append(c["attack_difficulty"])
                def_scores.append(c["defence_difficulty"])
                cs_sum += c["clean_sheet_prob"]
                goals_sum += c["proj_goals_for"]
        rows.append({
            "team_id": tid,
            "team": team.short_name,
            "team_name": team.name,
            "cells": {str(gw): cells[gw] for gw in gws},
            "avg_attack_difficulty": round(sum(att_scores) / len(att_scores), 2) if att_scores else None,
            "avg_defence_difficulty": round(sum(def_scores) / len(def_scores), 2) if def_scores else None,
            "sum_clean_sheet_prob": round(cs_sum, 2),
            "sum_proj_goals": round(goals_sum, 2),
            "n_fixtures": sum(len(cells[gw]) for gw in gws),
        })

    return {
        "gameweeks": gws,
        "rows": sorted(rows, key=lambda r: r["avg_attack_difficulty"] or 5),
        "best_attack": sorted(rows, key=lambda r: r["avg_attack_difficulty"] or 5)[:6],
        "best_defence": sorted(rows, key=lambda r: r["sum_clean_sheet_prob"], reverse=True)[:6],
    }


def _cell(rating, opp: Team | None, is_home: bool) -> dict:
    return {
        "opponent": opp.short_name if opp else "?",
        "is_home": is_home,
        "attack_difficulty": rating.attack_difficulty,
        "defence_difficulty": rating.defence_difficulty,
        "clean_sheet_prob": rating.clean_sheet_prob,
        "proj_goals_for": rating.proj_goals_for,
        "proj_goals_against": rating.proj_goals_against,
    }

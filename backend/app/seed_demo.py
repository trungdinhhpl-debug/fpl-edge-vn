"""Offline demo dataset (spec §25: clearly-labelled mock data, no network).

Builds a small but internally-consistent league (6 teams, 90 players, a double
round-robin over 10 gameweeks) so the whole stack — engine, optimizer, API and
UI — can run and be tested without calling the FPL API.

Everything here is SYNTHETIC. Never mix into production recommendations.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import (
    Fixture,
    Gameweek,
    Player,
    Season,
    SourceFetchLog,
    Team,
)
from app.scoring import SCORING_SOURCE

TEAMS = [
    ("Arsenal Demo", "ARS", 1350),
    ("City Demo", "CIT", 1370),
    ("Liverpool Demo", "LIV", 1360),
    ("Spurs Demo", "TOT", 1300),
    ("Villa Demo", "AVL", 1280),
    ("Brighton Demo", "BHA", 1270),
]

# per-position squad template (count, base_price_tenths)
POS_TEMPLATE = {1: (2, 45), 2: (5, 45), 3: (5, 60), 4: (3, 70)}
FIRST_NAMES = ["Alex", "Ben", "Cai", "Dan", "Eze", "Fin", "Gus", "Hal", "Ivo", "Jon"]


def seed_demo(db: Session, n_finished: int = 4, n_gameweeks: int = 10) -> dict:
    rng = np.random.default_rng(7)

    # wipe existing demo/live rows
    for model in (Fixture, Player, Team, Gameweek, Season):
        db.execute(delete(model))
    db.commit()

    db.add(Season(name="DEMO", is_current=True, scoring_source=SCORING_SOURCE))

    # gameweeks
    base = datetime(2025, 8, 15, 17, 30, tzinfo=timezone.utc)
    for gw in range(1, n_gameweeks + 1):
        db.add(Gameweek(
            id=gw, name=f"Gameweek {gw}",
            deadline_time=base + timedelta(days=7 * (gw - 1)),
            is_current=(gw == n_finished), is_next=(gw == n_finished + 1),
            is_previous=(gw == n_finished - 1), finished=(gw <= n_finished),
            data_checked=(gw <= n_finished),
        ))

    # teams
    for i, (name, short, strength) in enumerate(TEAMS, start=1):
        atk = strength + int(rng.integers(-30, 30))
        dfc = strength + int(rng.integers(-30, 30))
        db.add(Team(
            id=i, name=name, short_name=short, code=i, strength=strength,
            strength_overall_home=strength + 20, strength_overall_away=strength - 20,
            strength_attack_home=atk + 30, strength_attack_away=atk - 10,
            strength_defence_home=dfc + 20, strength_defence_away=dfc - 20,
        ))
    db.commit()

    # players
    pid = 1
    for team_id in range(1, len(TEAMS) + 1):
        team_quality = TEAMS[team_id - 1][2] / 1350.0
        for etype, (count, base_price) in POS_TEMPLATE.items():
            for j in range(count):
                nailed = j < (count - 1)  # last of each pos is a rotation option
                starts = int(n_finished * (0.95 if nailed else 0.4))
                minutes = starts * 88 + (0 if nailed else int(rng.integers(0, 60)))
                per90 = minutes / 90.0 if minutes else 0.0

                if etype == 4:      # FWD
                    xg = per90 * rng.uniform(0.3, 0.7) * team_quality
                    xa = per90 * rng.uniform(0.1, 0.3)
                    dc = per90 * rng.uniform(1, 3)
                elif etype == 3:    # MID
                    xg = per90 * rng.uniform(0.1, 0.45) * team_quality
                    xa = per90 * rng.uniform(0.15, 0.4)
                    dc = per90 * rng.uniform(4, 8)
                elif etype == 2:    # DEF
                    xg = per90 * rng.uniform(0.02, 0.12)
                    xa = per90 * rng.uniform(0.05, 0.2)
                    dc = per90 * rng.uniform(8, 14)
                else:               # GK
                    xg = 0.0
                    xa = 0.0
                    dc = 0.0

                goals = int(round(xg * rng.uniform(0.7, 1.3)))
                assists = int(round(xa * rng.uniform(0.7, 1.3)))
                saves = int(per90 * rng.uniform(2, 4)) if etype == 1 else 0
                price = base_price + int(j == 0) * 15 + int(team_quality > 1.0) * 10 + int(rng.integers(0, 10))

                db.add(Player(
                    id=pid, code=pid, first_name=FIRST_NAMES[pid % len(FIRST_NAMES)],
                    second_name=f"{TEAMS[team_id-1][1]}{pid}", web_name=f"{TEAMS[team_id-1][1]}-{etype}{j+1}",
                    team_id=team_id, element_type=etype, now_cost=price,
                    selected_by_percent=float(round(rng.uniform(0.5, 45), 1)),
                    transfers_in_event=int(rng.integers(0, 50000)),
                    status="a" if rng.random() > 0.06 else "d",
                    chance_of_playing_next_round=None if rng.random() > 0.06 else 75,
                    total_points=int(minutes / 90 * rng.uniform(2, 6)),
                    minutes=minutes, starts=starts, goals_scored=goals, assists=assists,
                    clean_sheets=int(rng.integers(0, n_finished)) if etype <= 2 else 0,
                    goals_conceded=int(rng.integers(0, 8)) if etype <= 2 else 0,
                    saves=saves, yellow_cards=int(rng.integers(0, 3)), red_cards=0,
                    bonus=int(rng.integers(0, 8)), bps=int(minutes / 90 * rng.uniform(15, 30)),
                    penalties_order=1 if (etype >= 3 and j == 0) else None,
                    expected_goals=round(xg, 2), expected_assists=round(xa, 2),
                    expected_goal_involvements=round(xg + xa, 2),
                    expected_goals_conceded=round(rng.uniform(1, 2) * n_finished, 2) if etype <= 2 else 0.0,
                    defensive_contribution=round(dc * n_finished / max(per90, 0.1) * per90, 1) if per90 else 0.0,
                    form=float(round(rng.uniform(1, 7), 1)),
                    points_per_game=float(round(rng.uniform(2, 6), 1)),
                    photo_code=str(pid),
                ))
                pid += 1
    db.commit()

    # fixtures: double round-robin across gameweeks (3 fixtures per GW)
    pairs = [(h, a) for h in range(1, 7) for a in range(1, 7) if h != a]
    rng.shuffle(pairs)
    fid = 1
    per_gw = 3
    for gw in range(1, n_gameweeks + 1):
        chunk = pairs[(gw - 1) * per_gw: gw * per_gw]
        for (h, a) in chunk:
            finished = gw <= n_finished
            hs = int(rng.integers(0, 4)) if finished else None
            as_ = int(rng.integers(0, 3)) if finished else None
            db.add(Fixture(
                id=fid, event=gw, kickoff_time=base + timedelta(days=7 * (gw - 1), hours=fid % 5),
                team_h=h, team_a=a, team_h_difficulty=3, team_a_difficulty=3,
                team_h_score=hs, team_a_score=as_, finished=finished, started=finished,
            ))
            fid += 1
    db.commit()

    # annotate blank/double + log
    from app.ingestion.fpl_sync import _annotate_gameweeks
    _annotate_gameweeks(db)
    db.add(SourceFetchLog(source_name="DEMO seed", source_url="internal",
                          source_type="mock", status="ok", rows=pid - 1,
                          detail="Synthetic offline dataset — not for real advice."))
    db.commit()

    # projections + experts
    from app.engine.projections import build_projections
    from app.ingestion.fpl_sync import seed_experts
    seed_experts(db)
    proj = build_projections(db)

    return {"teams": len(TEAMS), "players": pid - 1, "fixtures": fid - 1,
            "projections": proj["projections_written"], "gameweeks": proj["gameweeks"]}

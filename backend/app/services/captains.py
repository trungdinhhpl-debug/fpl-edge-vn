"""Captaincy service (spec §11).

Ranks candidates by captain EV (2x xP) while surfacing floor/ceiling, haul
probability, effective ownership and the reason — never by reputation or last
week's score alone.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, PlayerProjection
from app.services.common import planning_start_gw, player_public, team_lookup


def captain_ranking(db: Session, gw: int | None = None, limit: int = 20) -> dict:
    gw = gw or planning_start_gw(db)
    teams = team_lookup(db)
    projs = db.scalars(
        select(PlayerProjection).where(PlayerProjection.gameweek == gw)
    ).all()
    players = {p.id: p for p in db.scalars(select(Player)).all()}

    cands = []
    for pr in projs:
        p = players.get(pr.player_id)
        if not p or pr.xp <= 0 or pr.p_start < 0.4:
            continue
        base = player_public(p, teams.get(p.team_id))
        eo = _effective_ownership(p.selected_by_percent, pr.p_start)
        cands.append({
            **base,
            "captain_xp": round(pr.xp * 2, 2),
            "xp": round(pr.xp, 2),
            "xmins": round(pr.xmins, 1),
            "p_start": pr.p_start,
            "p_haul": pr.p_haul,          # >=10 pts (so >=20 as captain)
            "p_returns": pr.p_returns,
            "p_blank": pr.p_blank,
            "ceiling": round(pr.mc_ceiling * 2, 1),
            "floor": round(pr.mc_p25 * 2, 1),
            "effective_ownership": eo,
            "clean_sheet_prob": pr.clean_sheet_prob,
            "confidence": pr.confidence,
            "overall_risk": pr.overall_risk,
            "penalty_taker": p.penalties_order == 1,
        })

    cands.sort(key=lambda c: c["captain_xp"], reverse=True)
    cands = cands[:limit]

    # classify
    for i, c in enumerate(cands):
        c["tags"] = _classify(c, i)
    return {"gameweek": gw, "candidates": cands}


def _effective_ownership(owned: float, p_start: float) -> float:
    # rough EO proxy: ownership scaled by captaincy popularity of top picks
    return round(owned * p_start, 1)


def _classify(c: dict, rank: int) -> list[str]:
    tags = []
    if rank == 0:
        tags.append("EV cao nhất")
    if c["overall_risk"] in ("Low",) and c["p_start"] >= 0.9:
        tags.append("An toàn")
    if c["ceiling"] >= 24:
        tags.append("Ceiling cao")
    if c["effective_ownership"] < 15 and c["captain_xp"] >= 8:
        tags.append("Differential")
    if c["p_start"] < 0.7 or c["overall_risk"] in ("High", "Very High"):
        tags.append("Rủi ro")
    return tags

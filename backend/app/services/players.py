"""Player Explorer + detail services (spec §12)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpertSignal, ExpertSource, Fixture, Player, PlayerProjection
from app.services.common import (
    horizon_xp,
    planning_start_gw,
    player_public,
    projections_for_gw,
    team_lookup,
)


def _proj_public(proj: PlayerProjection | None) -> dict:
    if not proj:
        return {
            "xp": None, "xmins": None, "p_start": None, "n_fixtures": None,
            "confidence": None, "overall_risk": None, "minutes_risk": None,
            "performance_risk": None, "clean_sheet_prob": None, "goal_prob": None,
            "assist_prob": None, "ceiling": None, "p_haul": None, "p_blank": None,
            "opponent_team": None, "was_home": None,
        }
    return {
        "xp": round(proj.xp, 2),
        "xmins": round(proj.xmins, 1),
        "p_start": proj.p_start,
        "n_fixtures": proj.n_fixtures,
        "confidence": proj.confidence,
        "overall_risk": proj.overall_risk,
        "minutes_risk": proj.minutes_risk,
        "performance_risk": proj.performance_risk,
        "clean_sheet_prob": proj.clean_sheet_prob,
        "goal_prob": proj.goal_prob,
        "assist_prob": proj.assist_prob,
        "ceiling": round(proj.mc_ceiling, 1),
        "p_haul": proj.p_haul,
        "p_blank": proj.p_blank,
        "opponent_team": proj.opponent_team,
        "was_home": proj.was_home,
    }


def list_players(db: Session, position: str | None = None, team_id: int | None = None,
                 max_price: float | None = None, min_xp: float | None = None,
                 limit: int = 800) -> list[dict]:
    teams = team_lookup(db)
    start = planning_start_gw(db)
    gws = list(range(start, start + 5))
    proj_next = projections_for_gw(db, start)
    xp3 = horizon_xp(db, gws[:3])
    xp5 = horizon_xp(db, gws[:5])

    q = select(Player)
    if position:
        etype = {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}.get(position.upper())
        if etype:
            q = q.where(Player.element_type == etype)
    if team_id:
        q = q.where(Player.team_id == team_id)

    rows = db.scalars(q).all()
    out = []
    for p in rows:
        base = player_public(p, teams.get(p.team_id))
        if max_price and base["price"] > max_price:
            continue
        proj = proj_next.get(p.id)
        merged = {
            **base,
            **_proj_public(proj),
            "xp_next": round(proj.xp, 2) if proj else 0.0,
            "xp_next3": round(xp3.get(p.id, 0.0), 2),
            "xp_next5": round(xp5.get(p.id, 0.0), 2),
            "value_next5": round(xp5.get(p.id, 0.0) / max(base["price"], 0.1), 2),
        }
        if min_xp and merged["xp_next5"] < min_xp:
            continue
        out.append(merged)

    out.sort(key=lambda r: r["xp_next5"], reverse=True)
    return out[:limit]


def player_detail(db: Session, player_id: int) -> dict | None:
    p = db.get(Player, player_id)
    if not p:
        return None
    teams = team_lookup(db)
    base = player_public(p, teams.get(p.team_id))

    # projections across the horizon
    projs = db.scalars(
        select(PlayerProjection)
        .where(PlayerProjection.player_id == player_id)
        .order_by(PlayerProjection.gameweek)
    ).all()
    horizon = [
        {
            "gameweek": pr.gameweek,
            "xp": round(pr.xp, 2),
            "xmins": round(pr.xmins, 1),
            "opponent": teams.get(pr.opponent_team).short_name if pr.opponent_team in teams else None,
            "was_home": pr.was_home,
            "n_fixtures": pr.n_fixtures,
            "clean_sheet_prob": pr.clean_sheet_prob,
            "ceiling": round(pr.mc_ceiling, 1),
            "p_haul": pr.p_haul,
            "breakdown": {
                "appearance": pr.xp_appearance, "goals": pr.xp_goals,
                "assists": pr.xp_assists, "clean_sheet": pr.xp_clean_sheet,
                "saves": pr.xp_saves, "bonus": pr.xp_bonus,
                "defcon": pr.xp_defcon, "negative": pr.xp_negative,
            },
        }
        for pr in projs
    ]

    # expert signals about this player
    signals = db.scalars(
        select(ExpertSignal).where(ExpertSignal.player_id == player_id)
        .order_by(ExpertSignal.signal_score.desc())
    ).all()
    sources = {s.id: s for s in db.scalars(select(ExpertSource)).all()}
    expert = [
        {
            "type": s.signal_type, "confidence": s.confidence,
            "summary": s.summary, "score": s.signal_score,
            "source": sources.get(s.source_id).name if s.source_id in sources else "?",
            "link": s.link, "is_mock": s.is_mock,
        }
        for s in signals
    ]

    underlying = {
        "expected_goals": p.expected_goals,
        "expected_assists": p.expected_assists,
        "expected_goal_involvements": p.expected_goal_involvements,
        "expected_goals_conceded": p.expected_goals_conceded,
        "goals_scored": p.goals_scored,
        "assists": p.assists,
        "defensive_contribution": p.defensive_contribution,
        "bps": p.bps,
        "starts": p.starts,
        "xg_overperformance": round(p.goals_scored - p.expected_goals, 2),
        "penalties_order": p.penalties_order,
        "corners_freekicks_order": p.corners_and_indirect_freekicks_order,
        "direct_freekicks_order": p.direct_freekicks_order,
    }

    verdict = _verdict(base, horizon, underlying, p)

    return {
        "player": base,
        "horizon": horizon,
        "underlying": underlying,
        "expert_signals": expert,
        "verdict": verdict,
    }


def _verdict(base: dict, horizon: list[dict], underlying: dict, p: Player) -> dict:
    """Buy / Hold / Sell / Monitor with data-backed reasons (spec §17)."""
    xp5 = sum(h["xp"] for h in horizon[:5])
    reasons = []
    label = "Theo dõi"

    if base["status"] != "a":
        label = "Theo dõi"
        reasons.append(f"Cảnh báo tình trạng ra sân ({base['status']}).")
    elif xp5 >= 25:
        label = "Mua/Giữ"
        reasons.append(f"Tổng xP 5 vòng cao ({xp5:.1f}).")
    elif xp5 >= 15:
        label = "Giữ"
        reasons.append(f"xP 5 vòng khá ({xp5:.1f}).")
    else:
        label = "Bán/Tránh"
        reasons.append(f"xP 5 vòng thấp ({xp5:.1f}).")

    if underlying["xg_overperformance"] > 1.5:
        reasons.append(
            f"Đang ghi vượt xG {underlying['xg_overperformance']:.1f} — rủi ro hồi quy."
        )
    if p.penalties_order == 1:
        reasons.append("Đá penalty số 1.")
    return {"label": label, "xp_next5": round(xp5, 1), "reasons": reasons}


def compare_players(db: Session, ids: list[int]) -> list[dict]:
    details = []
    for pid in ids[:5]:
        d = player_detail(db, pid)
        if d:
            details.append(d)
    return details

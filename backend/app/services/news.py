"""News / injury centre (spec §15) and expert consensus (spec §4)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExpertSignal, ExpertSource, InjuryReport, Player
from app.services.common import player_public, team_lookup


def news_feed(db: Session, impact: str | None = None, limit: int = 100) -> list[dict]:
    teams = team_lookup(db)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    q = select(InjuryReport).order_by(InjuryReport.fetched_at.desc())
    reports = db.scalars(q).all()

    out = []
    for r in reports:
        if impact and r.impact.lower() != impact.lower():
            continue
        p = players.get(r.player_id)
        if not p:
            continue
        base = player_public(p, teams.get(p.team_id))
        out.append({
            "player_id": r.player_id,
            "name": base["name"],
            "team": base["team"],
            "position": base["position"],
            "status": r.status,
            "chance_of_playing": r.chance_of_playing,
            "impact": r.impact,
            "confirmed": r.confirmed,
            "news": r.news,
            "source_name": r.source_name,
            "source_url": r.source_url,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
        })
    # order by impact severity
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    out.sort(key=lambda r: order.get(r["impact"], 9))
    return out[:limit]


def expert_consensus(db: Session, limit: int = 50) -> dict:
    teams = team_lookup(db)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    sources = {s.id: s for s in db.scalars(select(ExpertSource)).all()}
    signals = db.scalars(
        select(ExpertSignal).order_by(ExpertSignal.signal_score.desc())
    ).all()

    grouped: dict[int, dict] = {}
    for s in signals:
        if s.player_id is None:
            continue
        p = players.get(s.player_id)
        if not p:
            continue
        g = grouped.setdefault(s.player_id, {
            **player_public(p, teams.get(p.team_id)),
            "signals": [], "consensus_score": 0.0,
        })
        src = sources.get(s.source_id)
        g["signals"].append({
            "type": s.signal_type,
            "confidence": s.confidence,
            "summary": s.summary,
            "score": s.signal_score,
            "source": src.name if src else "?",
            "source_type": src.source_type if src else "?",
            "independence": src.independence if src else 1.0,
            "link": s.link,
            "is_mock": s.is_mock,
        })
        # echo-chamber aware aggregation: independence already folded into score
        g["consensus_score"] = round(g["consensus_score"] + s.signal_score, 3)

    rows = sorted(grouped.values(), key=lambda r: r["consensus_score"], reverse=True)

    return {
        "sources": [
            {
                "name": s.name, "type": s.source_type, "url": s.url,
                "reliability": s.reliability, "historical_accuracy": s.historical_accuracy,
                "independence": s.independence, "expertise": s.expertise,
                "verified_track_record": s.verified_track_record,
            }
            for s in sources.values()
        ],
        "players": rows[:limit],
        "disclaimer": (
            "Tín hiệu chuyên gia là dữ liệu tham khảo (một phần là mock có nhãn). "
            "Không ghi đè dữ liệu chính thức hoặc xác suất ra sân đã xác nhận."
        ),
    }

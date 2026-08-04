"""News / injury centre (spec §15). Expert signals live in services/experts.py."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InjuryReport, Player
from app.services.common import player_public, team_lookup
from app.services.news_tiers import (
    BY_KEY, TIERS, classify_origin, recommend, xmins_before_after,
)


def _news_context(db: Session) -> dict:
    """Inputs the minutes model needs, gathered once for the whole feed."""
    from app.models import Fixture, PlayerGameweekStat
    from app.services.common import planning_start_gw

    recent: dict[int, list[int]] = defaultdict(list)
    for s in db.scalars(
        select(PlayerGameweekStat).order_by(PlayerGameweekStat.gameweek)
    ).all():
        recent[s.player_id].append(s.minutes)

    played: dict[int, int] = defaultdict(int)
    for f in db.scalars(select(Fixture).where(Fixture.finished.is_(True))).all():
        played[f.team_h] += 1
        played[f.team_a] += 1

    return {"recent": recent, "played": played, "gw": planning_start_gw(db)}


def news_feed(db: Session, impact: str | None = None, tier: str | None = None,
              limit: int = 100) -> list[dict]:
    """Injury/news items enriched with provenance and their xMins impact.

    Kept returning a plain list because the dashboard and chatbot consume it
    directly; `news_centre` wraps this with the tier coverage report.
    """
    teams = team_lookup(db)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    reports = db.scalars(
        select(InjuryReport).order_by(InjuryReport.fetched_at.desc())
    ).all()
    # One card per player: the CURRENT state, with everything it superseded kept
    # as that card's history. Two things made this necessary — older syncs
    # inserted an identical row every run (fixed in ingestion, but production
    # rows still carry it), and a story genuinely develops: "75% chance" one
    # week, "departed the club" the next. Showing both as live advice is wrong;
    # throwing the old one away loses the development. So: newest wins, the rest
    # become history.
    reports.sort(key=lambda r: r.fetched_at or datetime.min.replace(tzinfo=timezone.utc),
                 reverse=True)
    current: dict[int, InjuryReport] = {}
    history: dict[int, list[dict]] = defaultdict(list)
    for r in reports:
        if r.player_id not in current:
            current[r.player_id] = r
            continue
        head = current[r.player_id]
        key = (r.status, r.chance_of_playing, r.news)
        if key == (head.status, head.chance_of_playing, head.news):
            continue                      # exact re-fetch of the current story
        if any(key == (h["status"], h["chance_of_playing"], h["news"])
               for h in history[r.player_id]):
            continue                      # re-fetch of an already-recorded step
        history[r.player_id].append({
            "status": r.status,
            "chance_of_playing": r.chance_of_playing,
            "news": r.news,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
        })
    reports = list(current.values())
    ctx = _news_context(db)

    # How many DISTINCT feeds carry the same player right now. With one feed
    # wired up this is 1 for everything, and saying "1" is the honest answer —
    # not evidence of consensus.
    origins_by_player: dict[int, set[str]] = defaultdict(set)
    for r in reports:
        if r.source_name:
            origins_by_player[r.player_id].add(r.source_name)

    out = []
    for r in reports:
        if impact and r.impact.lower() != impact.lower():
            continue
        p = players.get(r.player_id)
        if not p:
            continue
        tier_key = classify_origin(r.source_name)
        if tier and tier_key != tier:
            continue
        t = BY_KEY[tier_key]
        base = player_public(p, teams.get(p.team_id))

        before, after = xmins_before_after(
            element_type=p.element_type, status=r.status,
            chance_of_playing=r.chance_of_playing,
            season_starts=p.starts, season_minutes=p.minutes,
            team_matches_played=ctx["played"].get(p.team_id, 0),
            recent_minutes=ctx["recent"].get(p.id),
        )
        names = sorted(origins_by_player.get(r.player_id, set()))

        out.append({
            "player_id": r.player_id,
            "name": base["name"],
            "team": base["team"],
            "position": base["position"],
            "selected_by_percent": base["selected_by_percent"],
            "status": r.status,
            "chance_of_playing": r.chance_of_playing,
            "impact": r.impact,
            "confirmed": r.confirmed,
            "news": r.news,
            # --- provenance ---
            "tier": tier_key,
            "tier_label": t.label,
            "tier_rank": t.rank,
            "tier_reliability": t.reliability,
            "source_name": r.source_name,
            "source_url": r.source_url,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
            "independent_sources": len(names),
            "independent_source_names": names,
            "history": history.get(r.player_id, []),
            # --- what it moved ---
            "affected_gameweek": ctx["gw"],
            "xmins_before": before,
            "xmins_after": after,
            "xmins_delta": round(after - before, 1),
            "action": recommend(before, after, r.status,
                                base["selected_by_percent"] or 0.0),
        })

    # Most severe first, then the biggest minutes swing — a Critical item that
    # moved nothing is less urgent than one that halved a starter's minutes.
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    out.sort(key=lambda r: (order.get(r["impact"], 9), r["xmins_delta"]))
    return out[:limit]


MODEL_FLAG_MIN_OWNERSHIP = 5.0    # below this nobody is holding him anyway
MODEL_FLAG_MAX_P_START = 0.65     # the model genuinely doubts the start


def model_inferred(db: Session, ctx: dict, reported: set[int]) -> list[dict]:
    """Players the minutes model doubts although nobody has reported anything.

    Deliberately NOT given an xMins before/after: this is a standing assessment,
    not an event, so there is no "before" to measure against. Fabricating one to
    fill the column would be the exact dishonesty this page exists to avoid.
    """
    from app.models import ExpectedMinutes

    teams = team_lookup(db)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    ests = db.scalars(
        select(ExpectedMinutes).where(ExpectedMinutes.gameweek == ctx["gw"])
    ).all()
    t = BY_KEY["model_inference"]

    out = []
    for e in ests:
        p = players.get(e.player_id)
        if not p or p.id in reported:
            continue
        if (p.status or "a") != "a":
            continue                      # a flagged player is not model-only
        own = p.selected_by_percent or 0.0
        if own < MODEL_FLAG_MIN_OWNERSHIP or e.p_start > MODEL_FLAG_MAX_P_START:
            continue
        base = player_public(p, teams.get(p.team_id))
        out.append({
            "player_id": p.id, "name": base["name"], "team": base["team"],
            "position": base["position"], "selected_by_percent": own,
            "status": p.status, "chance_of_playing": None,
            "impact": "Medium" if e.p_start < 0.5 else "Low",
            "confirmed": False,
            "news": (f"Không có tin chính thức. Mô hình ước tính khả năng đá chính "
                     f"{e.p_start * 100:.0f}% ({e.xmins:.0f}′). {e.reason or ''}").strip(),
            "tier": t.key, "tier_label": t.label, "tier_rank": t.rank,
            "tier_reliability": t.reliability,
            "source_name": "FPL Edge model", "source_url": None,
            "published_at": None,
            "fetched_at": e.data_cutoff.isoformat() if e.data_cutoff else None,
            "independent_sources": 0,
            "independent_source_names": [],
            "affected_gameweek": ctx["gw"],
            # no event => no before/after; the UI must show this as "—"
            "xmins_before": None, "xmins_after": round(e.xmins, 1),
            "xmins_delta": None,
            "action": {
                "from": "Giữ", "to": "Theo dõi", "label": "Giữ → Theo dõi",
                "why": (f"Chưa ai đưa tin — đây là đánh giá thường trực của mô hình, "
                        f"độ tin cậy {e.confidence}. Chờ đội hình xuất phát."),
                "xmins_drop": None,
            },
        })
    out.sort(key=lambda r: -(r["selected_by_percent"] or 0))
    return out


def news_centre(db: Session, impact: str | None = None, tier: str | None = None,
                limit: int = 100) -> dict:
    """The feed plus an honest report of which tiers actually have a source."""
    reported_items = news_feed(db, impact=impact, limit=500)
    ctx = _news_context(db)
    reported_ids = {i["player_id"] for i in reported_items}
    inferred = model_inferred(db, ctx, reported_ids)
    if impact:
        inferred = [i for i in inferred if i["impact"].lower() == impact.lower()]

    counts: dict[str, int] = defaultdict(int)
    for it in reported_items + inferred:
        counts[it["tier"]] += 1

    pool = reported_items + inferred
    if tier:
        pool = [i for i in pool if i["tier"] == tier]
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    pool.sort(key=lambda r: (order.get(r["impact"], 9), r["xmins_delta"] or 0))
    items = pool[:limit]

    return {
        "gameweek": items[0]["affected_gameweek"] if items else None,
        "items": items,
        "tiers": [
            {
                "key": t.key, "label": t.label, "rank": t.rank,
                "reliability": t.reliability, "description": t.description,
                "configured": t.feed is not None,
                "feed": t.feed, "needs": t.needs,
                "count": counts.get(t.key, 0),
            }
            for t in TIERS
        ],
        "note": ("Mỗi tin kèm nguồn gốc, thời điểm xuất bản/lấy tin, vòng bị ảnh "
                 "hưởng và thay đổi xMins trước/sau tin. xMins trước là phản thực: "
                 "chạy lại chính mô hình phút thi đấu với trạng thái sẵn sàng, nên "
                 "chênh lệch đúng bằng phần do tin này gây ra."),
    }

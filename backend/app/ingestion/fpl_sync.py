"""Ingestion: FPL API -> database (spec §3 Tier-1, §5 freshness logging).

Every fetch writes a SourceFetchLog row so the UI can show data recency and
flag stale / errored sources.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ExpertSignal,
    ExpertSource,
    Fixture,
    Gameweek,
    InjuryReport,
    Player,
    PlayerPrice,
    Season,
    SourceFetchLog,
    Team,
)
from app.providers.expert_provider import ExpertProvider, compute_signal_score
from app.providers.fpl_client import FPLClient
from app.scoring import SCORING_SOURCE, SEASON


# ------------------------------------------------------------- parse helpers --
def _dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _log(db: Session, name: str, url: str, status: str, rows: int, detail: str = "",
         source_type: str = "official") -> None:
    db.add(SourceFetchLog(
        source_name=name, source_url=url, source_type=source_type,
        status=status, rows=rows, detail=detail,
    ))


# ------------------------------------------------------------------ bootstrap --
def sync_bootstrap(db: Session, client: FPLClient) -> dict:
    data = client.bootstrap_static()
    url = f"{settings.fpl_base_url}/bootstrap-static/"

    # season
    season = db.scalar(select(Season).where(Season.is_current.is_(True)))
    if not season:
        db.add(Season(name=SEASON, is_current=True, scoring_source=SCORING_SOURCE))

    # gameweeks
    for ev in data.get("events", []):
        db.merge(Gameweek(
            id=ev["id"], name=ev["name"], deadline_time=_dt(ev.get("deadline_time")),
            is_current=ev.get("is_current", False), is_next=ev.get("is_next", False),
            is_previous=ev.get("is_previous", False), finished=ev.get("finished", False),
            data_checked=ev.get("data_checked", False),
            average_entry_score=ev.get("average_entry_score"),
            highest_score=ev.get("highest_score"),
        ))

    # teams
    for t in data.get("teams", []):
        db.merge(Team(
            id=t["id"], name=t["name"], short_name=t["short_name"], code=t["code"],
            strength=t.get("strength"),
            strength_overall_home=t.get("strength_overall_home"),
            strength_overall_away=t.get("strength_overall_away"),
            strength_attack_home=t.get("strength_attack_home"),
            strength_attack_away=t.get("strength_attack_away"),
            strength_defence_home=t.get("strength_defence_home"),
            strength_defence_away=t.get("strength_defence_away"),
        ))

    # players
    n_players = 0
    for e in data.get("elements", []):
        db.merge(Player(
            id=e["id"], code=e.get("code", 0), first_name=e.get("first_name", ""),
            second_name=e.get("second_name", ""), web_name=e.get("web_name", ""),
            team_id=e["team"], element_type=e["element_type"],
            now_cost=e.get("now_cost", 0),
            selected_by_percent=_f(e.get("selected_by_percent")),
            transfers_in_event=_i(e.get("transfers_in_event")),
            transfers_out_event=_i(e.get("transfers_out_event")),
            status=e.get("status", "a"),
            chance_of_playing_next_round=e.get("chance_of_playing_next_round"),
            news=e.get("news") or None, news_added=_dt(e.get("news_added")),
            total_points=_i(e.get("total_points")), minutes=_i(e.get("minutes")),
            starts=_i(e.get("starts")), goals_scored=_i(e.get("goals_scored")),
            assists=_i(e.get("assists")), clean_sheets=_i(e.get("clean_sheets")),
            goals_conceded=_i(e.get("goals_conceded")), saves=_i(e.get("saves")),
            yellow_cards=_i(e.get("yellow_cards")), red_cards=_i(e.get("red_cards")),
            bonus=_i(e.get("bonus")), bps=_i(e.get("bps")),
            penalties_order=e.get("penalties_order"),
            corners_and_indirect_freekicks_order=e.get("corners_and_indirect_freekicks_order"),
            direct_freekicks_order=e.get("direct_freekicks_order"),
            expected_goals=_f(e.get("expected_goals")),
            expected_assists=_f(e.get("expected_assists")),
            expected_goal_involvements=_f(e.get("expected_goal_involvements")),
            expected_goals_conceded=_f(e.get("expected_goals_conceded")),
            defensive_contribution=_f(e.get("defensive_contribution")),
            recoveries=_i(e.get("recoveries")), tackles=_i(e.get("tackles")),
            clearances_blocks_interceptions=_i(e.get("clearances_blocks_interceptions")),
            form=_f(e.get("form")), points_per_game=_f(e.get("points_per_game")),
            ep_next=_f(e.get("ep_next"), None), photo_code=(e.get("photo") or "").replace(".jpg", ""),
        ))
        n_players += 1

    # ensure teams/players are actually inserted before rows that FK to them.
    # (Postgres enforces FKs immediately; SQLAlchemy only orders by relationship,
    #  and player_prices/injury_reports have FK columns but no relationship.)
    db.flush()

    # price snapshots + injury reports (only for flagged players)
    for e in data.get("elements", []):
        db.add(PlayerPrice(
            player_id=e["id"], now_cost=e.get("now_cost", 0),
            selected_by_percent=_f(e.get("selected_by_percent")),
        ))
        if e.get("status") not in ("a", None) or e.get("news"):
            impact = _injury_impact(e.get("status"), e.get("chance_of_playing_next_round"))
            db.add(InjuryReport(
                player_id=e["id"], status=e.get("status", "d"),
                chance_of_playing=e.get("chance_of_playing_next_round"),
                impact=impact, confirmed=bool(e.get("news")),
                news=e.get("news") or None, source_name="FPL Official",
                source_url="https://fantasy.premierleague.com",
                published_at=_dt(e.get("news_added")),
            ))

    _log(db, "FPL bootstrap-static", url, "ok", n_players)
    db.commit()
    return {"players": n_players, "teams": len(data.get("teams", [])),
            "gameweeks": len(data.get("events", []))}


def _injury_impact(status, chance) -> str:
    if status in ("i", "s", "u"):
        return "Critical" if (chance in (0, None)) else "High"
    if status == "d":
        if chance is not None and chance <= 25:
            return "High"
        return "Medium"
    return "Low"


# ------------------------------------------------------------------- fixtures --
def sync_fixtures(db: Session, client: FPLClient) -> dict:
    data = client.fixtures()
    url = f"{settings.fpl_base_url}/fixtures/"
    n = 0
    for f in data:
        db.merge(Fixture(
            id=f["id"], event=f.get("event"), kickoff_time=_dt(f.get("kickoff_time")),
            team_h=f["team_h"], team_a=f["team_a"],
            team_h_difficulty=f.get("team_h_difficulty"),
            team_a_difficulty=f.get("team_a_difficulty"),
            team_h_score=f.get("team_h_score"), team_a_score=f.get("team_a_score"),
            finished=f.get("finished", False), started=f.get("started", False),
        ))
        n += 1
    _log(db, "FPL fixtures", url, "ok", n)
    db.commit()

    # mark blank/double gameweeks by counting fixtures per team per event
    _annotate_gameweeks(db)
    return {"fixtures": n}


def _annotate_gameweeks(db: Session) -> None:
    """Store per-team fixture counts to flag blank (0) / double (2+) GWs."""
    import json
    fixtures = db.scalars(select(Fixture).where(Fixture.event.isnot(None))).all()
    teams = [t.id for t in db.scalars(select(Team)).all()]
    per_gw: dict[int, dict[int, int]] = {}
    for f in fixtures:
        per_gw.setdefault(f.event, {}).setdefault(f.team_h, 0)
        per_gw.setdefault(f.event, {}).setdefault(f.team_a, 0)
        per_gw[f.event][f.team_h] += 1
        per_gw[f.event][f.team_a] += 1
    for gw_id, counts in per_gw.items():
        gw = db.get(Gameweek, gw_id)
        if gw:
            full = {tid: counts.get(tid, 0) for tid in teams}
            gw.fixture_count_by_team = json.dumps(full)
    db.commit()


# -------------------------------------------------------------------- experts --
def seed_experts(db: Session) -> dict:
    provider = ExpertProvider()
    name_to_source: dict[str, ExpertSource] = {}
    for s in provider.get_sources():
        existing = db.scalar(select(ExpertSource).where(ExpertSource.name == s.name))
        if existing:
            name_to_source[s.name] = existing
            continue
        src = ExpertSource(
            name=s.name, source_type=s.source_type, url=s.url,
            reliability=s.reliability, historical_accuracy=s.historical_accuracy,
            expertise=s.expertise, independence=s.independence,
            verified_track_record=s.verified_track_record,
            last_updated=datetime.now(timezone.utc),
        )
        db.add(src)
        db.flush()
        name_to_source[s.name] = src

    # only seed signals once
    if db.scalar(select(func.count()).select_from(ExpertSignal)) == 0:
        for sig in provider.get_signals():
            src = name_to_source.get(sig.source_name)
            if not src:
                continue
            player = db.scalar(select(Player).where(Player.web_name == sig.web_name))
            specificity = 0.85 if sig.signal_type in ("start", "penalty", "setpiece") else 0.6
            score = compute_signal_score(
                src.reliability, src.historical_accuracy, src.independence,
                specificity, sig.published_hours_ago,
            )
            db.add(ExpertSignal(
                source_id=src.id, player_id=player.id if player else None,
                gameweek=None, signal_type=sig.signal_type, confidence=sig.confidence,
                summary=sig.summary, link=sig.link, signal_score=score, is_mock=True,
            ))
    _log(db, "Expert seed (mock)", "internal", "ok", 0, "labelled mock data", "community")
    db.commit()
    return {"sources": len(name_to_source)}


# ----------------------------------------------------------- market odds ------
def sync_odds(db: Session) -> dict:
    """Fetch bookmaker odds -> per-fixture expected goals (spec §3 Tier-2).

    Odds are only published for the near-term fixtures, so this typically covers
    the next gameweek; later gameweeks keep using the internal model.
    """
    from app.models import MarketOdds
    from app.providers.probability import get_probability_provider, match_team_id

    provider = get_probability_provider()
    if provider is None:
        _log(db, "Odds provider", "internal", "ok", 0,
             "Chưa cấu hình ODDS_API_KEY — dùng mô hình nội bộ (model estimate).",
             "market")
        db.commit()
        return {"matched": 0, "skipped": 0, "enabled": False}

    try:
        matches = provider.get_matches()
    except Exception as exc:
        _log(db, "Odds provider", ODDS_URL, "error", 0, str(exc)[:300], "market")
        db.commit()
        return {"matched": 0, "skipped": 0, "enabled": True, "error": str(exc)[:200]}

    teams = {t.id: t.name for t in db.scalars(select(Team)).all()}
    fixtures = db.scalars(select(Fixture).where(Fixture.finished.is_(False))).all()
    by_pair: dict[tuple[int, int], Fixture] = {}
    for f in fixtures:
        by_pair.setdefault((f.team_h, f.team_a), f)

    # upsert by fixture_id — NOT db.merge(): merge matches on the primary key
    # (`id`), which is None for new objects, so it would always INSERT and trip
    # the unique constraint on fixture_id every run after the first.
    existing = {r.fixture_id: r for r in db.scalars(select(MarketOdds)).all()}

    matched = skipped = 0
    now = datetime.now(timezone.utc)
    for m in matches:
        hid = match_team_id(m.home_name, teams)
        aid = match_team_id(m.away_name, teams)
        fx = by_pair.get((hid, aid)) if hid and aid else None
        if not fx:
            skipped += 1
            continue
        values = dict(
            gameweek=fx.event, team_h=hid, team_a=aid,
            lam_home=m.lam_home, lam_away=m.lam_away,
            p_home=m.p_home, p_draw=m.p_draw, p_away=m.p_away,
            total_goals=m.total_goals, n_bookmakers=m.n_bookmakers,
            source_name=m.source, is_market=m.is_market, fetched_at=now,
        )
        row = existing.get(fx.id)
        if row is not None:
            for k, v in values.items():
                setattr(row, k, v)
        else:
            db.add(MarketOdds(fixture_id=fx.id, **values))
        matched += 1

    _log(db, "The Odds API (soccer_epl)", ODDS_URL, "ok" if matched else "error",
         matched, f"{matched} trận khớp, {skipped} bỏ qua", "market")
    db.commit()
    return {"matched": matched, "skipped": skipped, "enabled": True}


ODDS_URL = "https://the-odds-api.com/"


# ----------------------------------------------------------------- full sync --
def run_full_sync(db: Session, build_proj: bool = True, detail: bool = False) -> dict:
    result: dict = {"started_at": datetime.now(timezone.utc).isoformat()}
    with FPLClient() as client:
        result["bootstrap"] = sync_bootstrap(db, client)
        result["fixtures"] = sync_fixtures(db, client)
    result["experts"] = seed_experts(db)
    result["odds"] = sync_odds(db)
    if build_proj:
        from app.engine.projections import build_projections
        result["projections"] = build_projections(db)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result

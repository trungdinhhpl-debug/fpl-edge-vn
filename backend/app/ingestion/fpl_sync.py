"""Ingestion: FPL API -> database (spec §3 Tier-1, §5 freshness logging).

Every fetch writes a SourceFetchLog row so the UI can show data recency and
flag stale / errored sources.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select
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
    SeasonRules,
    SourceFetchLog,
    Team,
)
from app.providers.expert_provider import ExpertProvider, compute_signal_score
from app.providers.fpl_client import FPLClient


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


# -------------------------------------------------------- luật của mùa giải ----
def sync_season_rules(db: Session, data: dict) -> dict:
    """Lưu tên mùa + luật + chip từ `game_config`, rồi nạp vào engine.

    Tên mùa và mọi giá trị tính điểm đều lấy từ API — không ghi cứng ở đâu cả.
    `rules_updated_at` chỉ đổi khi vân tay luật đổi, nên biết chính xác luật có
    hiệu lực từ bao giờ.
    """
    import json as _json

    from app.scoring import SCORING_SOURCE, apply_config, rules_hash, season_from_config

    gc = data.get("game_config") or {}
    if not gc:
        _log(db, "FPL game_config", "bootstrap-static", "error", 0,
             "API không trả về game_config — engine dùng luật dự phòng", "official")
        return {"ok": False}

    name = season_from_config(gc) or "unknown"
    version = rules_hash(gc)
    now = datetime.now(timezone.utc)

    season = db.scalar(select(Season).where(Season.name == name))
    if season is None:
        season = Season(name=name)
        db.add(season)
    changed = season.rules_version != version
    season.is_current = True
    season.scoring_source = SCORING_SOURCE
    season.rules_json = _json.dumps(gc, ensure_ascii=False)
    season.chips_json = _json.dumps(data.get("chips") or [], ensure_ascii=False)
    season.rules_version = version
    if changed or season.rules_updated_at is None:
        season.rules_updated_at = now
    season.fetched_at = now

    # các mùa khác không còn là mùa hiện tại
    for other in db.scalars(select(Season).where(Season.name != name)).all():
        other.is_current = False

    db.flush()
    applied = apply_config(gc, name)
    _upsert_season_rules(db, season, now)
    _log(db, "FPL game_config", "bootstrap-static", "ok", 1,
         f"mùa {name}, luật {version}" + (" (ĐỔI LUẬT)" if changed else ""), "official")
    return {"ok": True, "season": name, "rules_version": version, "changed": changed,
            **applied}


def _upsert_season_rules(db: Session, season: Season, now: datetime) -> None:
    """Ghi phiên bản từng nhóm luật vào `season_rules`.

    Gọi SAU `apply_config` để `scoring.rules_versions()` đã phản ánh mùa mới.
    Một dòng cho mỗi mốc hiệu lực: nếu FPL sửa luật giữa mùa thì phiên bản mới
    được thêm vào chứ không ghi đè, để còn biết dự báo cũ chạy trên luật nào.
    """
    from app.scoring import rules_versions

    v = rules_versions()
    effective = season.rules_updated_at or now
    row = db.scalar(
        select(SeasonRules).where(
            SeasonRules.season_id == season.id,
            SeasonRules.effective_from == effective,
        )
    )
    if row is None:
        row = SeasonRules(season_id=season.id, effective_from=effective)
        db.add(row)
    row.scoring_rules_version = v["scoring_rules_version"]
    row.bps_rules_version = v["bps_rules_version"]
    row.assist_rules_version = v["assist_rules_version"]
    row.chip_rules_version = v["chip_rules_version"]
    row.source_url = v["source_url"]
    db.flush()


# ------------------------------------------------------------------ bootstrap --
def sync_bootstrap(db: Session, client: FPLClient) -> dict:
    data = client.bootstrap_static()
    url = f"{settings.fpl_base_url}/bootstrap-static/"

    sync_season_rules(db, data)

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

    # Latest stored report per player, so an unchanged story is refreshed rather
    # than inserted again. Without this every sync added another identical row:
    # by GW1 the same suspension appeared three times in the news feed, and the
    # per-tier counts were inflated by the duplicates.
    latest_report: dict[int, InjuryReport] = {}
    for r in db.scalars(select(InjuryReport)).all():
        prev = latest_report.get(r.player_id)
        if prev is None or (r.fetched_at and prev.fetched_at
                            and r.fetched_at > prev.fetched_at):
            latest_report[r.player_id] = r

    now = datetime.now(timezone.utc)

    # price snapshots + injury reports (only for flagged players)
    for e in data.get("elements", []):
        db.add(PlayerPrice(
            player_id=e["id"], now_cost=e.get("now_cost", 0),
            selected_by_percent=_f(e.get("selected_by_percent")),
        ))
        if e.get("status") not in ("a", None) or e.get("news"):
            status = e.get("status", "d")
            chance = e.get("chance_of_playing_next_round")
            news = e.get("news") or None
            impact = _injury_impact(status, chance)
            prev = latest_report.get(e["id"])
            unchanged = (
                prev is not None and prev.status == status
                and prev.chance_of_playing == chance and prev.news == news
            )
            if unchanged:
                # same story, seen again — only the "last checked" time moves
                prev.fetched_at = now
                prev.impact = impact
                continue
            db.add(InjuryReport(
                player_id=e["id"], status=status,
                chance_of_playing=chance,
                impact=impact, confirmed=bool(news),
                news=news, source_name="FPL Official",
                source_url="https://fantasy.premierleague.com",
                published_at=_dt(e.get("news_added")),
                fetched_at=now,
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
            # Overwrite rather than skip. Earlier seeds shipped invented accuracy
            # figures and a `verified` flag for real, named people; skipping would
            # leave those claims in a live database forever. Accuracy now comes
            # only from scored predictions (ExpertTrackRecord).
            existing.source_type = s.source_type
            existing.url = s.url
            existing.reliability = s.reliability
            existing.historical_accuracy = s.historical_accuracy
            existing.expertise = s.expertise
            existing.independence = s.independence
            existing.verified_track_record = s.verified_track_record
            existing.last_updated = datetime.now(timezone.utc)
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

    # Rebuild demo signals every run instead of seeding once. The earlier seed
    # attributed invented quotes to real named analysts and those rows are still
    # sitting in live databases; only a rewrite clears them. Real signals from a
    # licensed feed would carry is_mock=False and are never touched here.
    db.execute(delete(ExpertSignal).where(ExpertSignal.is_mock.is_(True)))
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
            origin_ref=sig.origin_ref,
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

    # Fit quality is worth seeing in the log: a big residual means the three
    # markets disagree with each other more than the score model can reconcile.
    fitted = [m for m in matches if m.markets_used]
    detail = f"{matched} trận khớp, {skipped} bỏ qua"
    if fitted:
        rmse = (sum(m.fit_error for m in fitted) / len(fitted)) ** 0.5
        used = sorted({"+".join(m.markets_used) for m in fitted})
        detail += (
            f" · sai số khớp RMSE {rmse * 100:.2f} điểm % · thị trường {', '.join(used)}"
        )
    _log(db, "The Odds API (soccer_epl)", ODDS_URL, "ok" if matched else "error",
         matched, detail, "market")
    db.commit()
    return {"matched": matched, "skipped": skipped, "enabled": True}


ODDS_URL = "https://the-odds-api.com/"


# ------------------------------------- Championship (đội mới lên hạng) --------
def sync_championship(db: Session) -> dict:
    """Lấy bảng Championship mùa trước cho các đội vừa lên hạng (tuỳ chọn).

    Chỉ ghi dữ liệu cho những đội KHÔNG có lịch sử Ngoại hạng — các đội lâu năm
    đã có dữ liệu tốt hơn nhiều. Tắt bằng CHAMPIONSHIP_ENABLED=false.
    """
    from app.models import ChampionshipStats, Player
    from app.providers.championship import (
        SOURCE_NAME,
        fetch_championship_table,
        league_averages,
        season_code,
    )
    from app.providers.probability import match_team_id

    if not settings.championship_enabled:
        return {"enabled": False, "matched": 0}

    # năm bắt đầu mùa Ngoại hạng hiện tại, lấy từ trận sớm nhất trong lịch
    first = db.scalars(
        select(Fixture).where(Fixture.kickoff_time.isnot(None)).order_by(Fixture.kickoff_time)
    ).first()
    if not first or not first.kickoff_time:
        return {"enabled": True, "matched": 0, "error": "chưa có lịch thi đấu"}
    pl_year = first.kickoff_time.year

    try:
        stats, url = fetch_championship_table(pl_year)
    except Exception as exc:
        _log(db, SOURCE_NAME, "https://www.football-data.co.uk/", "error", 0,
             str(exc)[:250], "stats")
        db.commit()
        return {"enabled": True, "matched": 0, "error": str(exc)[:200]}

    avg_gf, avg_ga = league_averages(stats)

    # đội nào không có lịch sử Ngoại hạng -> coi là mới lên hạng
    minutes_by_team: dict[int, int] = {}
    for p in db.scalars(select(Player)).all():
        minutes_by_team[p.team_id] = minutes_by_team.get(p.team_id, 0) + (p.minutes or 0)
    promoted_ids = {tid for tid, mins in minutes_by_team.items() if mins < 6000}

    teams = {t.id: t.name for t in db.scalars(select(Team)).all()}
    existing = {r.team_id: r for r in db.scalars(select(ChampionshipStats)).all()}

    matched = 0
    now = datetime.now(timezone.utc)
    season = season_code(pl_year)
    for s in stats:
        tid = match_team_id(s.name, teams)
        if tid is None or tid not in promoted_ids:
            continue
        values = dict(
            source_team_name=s.name, season=season, played=s.played,
            goals_for=s.goals_for, goals_against=s.goals_against,
            # chỉ số TƯƠNG ĐỐI trong Championship (1.0 = trung bình giải đó)
            attack_index=round(s.gf_per_game / avg_gf, 4) if avg_gf else 1.0,
            defence_index=round(avg_ga / s.ga_per_game, 4) if s.ga_per_game else 1.0,
            source_name="football-data.co.uk", source_url=url, fetched_at=now,
        )
        row = existing.get(tid)
        if row is not None:
            for k, v in values.items():
                setattr(row, k, v)
        else:
            db.add(ChampionshipStats(team_id=tid, **values))
        matched += 1

    _log(db, SOURCE_NAME, url, "ok" if matched else "error", matched,
         f"{matched} đội mới lên hạng khớp dữ liệu Championship {season}", "stats")
    db.commit()
    return {"enabled": True, "matched": matched, "season": season}


# ----------------------------------------------------------------- full sync --
def run_full_sync(db: Session, build_proj: bool = True, detail: bool = False) -> dict:
    result: dict = {"started_at": datetime.now(timezone.utc).isoformat()}
    with FPLClient() as client:
        result["bootstrap"] = sync_bootstrap(db, client)
        result["fixtures"] = sync_fixtures(db, client)
    result["experts"] = seed_experts(db)
    result["odds"] = sync_odds(db)
    result["championship"] = sync_championship(db)
    if build_proj:
        from app.engine.projections import build_projections
        result["projections"] = build_projections(db)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result

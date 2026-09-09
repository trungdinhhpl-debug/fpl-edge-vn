"""Ingestion: FPL API -> database (spec §3 Tier-1, §5 freshness logging).

Every fetch writes a SourceFetchLog row so the UI can show data recency and
flag stale / errored sources.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ExpertSignal,
    ExpertSource,
    ExpertTrackRecord,
    Fixture,
    Gameweek,
    InjuryReport,
    Player,
    PlayerGameweekStat,
    PlayerPrice,
    PlayerSeasonHistory,
    Season,
    SeasonRules,
    SourceFetchLog,
    Team,
)
from app.providers.expert_provider import ExpertProvider, compute_signal_score
from app.providers.fpl_client import FPLClient
from app.services.season_state import not_played_condition, played_condition


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
            penalties_missed=_i(e.get("penalties_missed")),
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
            finished_provisional=f.get("finished_provisional"),
        ))
        n += 1
    _log(db, "FPL fixtures", url, "ok", n)
    db.commit()

    # mark blank/double gameweeks by counting fixtures per team per event
    _annotate_gameweeks(db)
    return {"fixtures": n}


# ------------------------------------------------ nền lịch sử các mùa trước ----
# Số cầu thủ hỏi trong MỘT lần chạy. Toàn bộ 609 người mất ~73 giây với
# `fpl_request_delay_ms=120`, vẫn nằm gọn trong hạn 20 phút của GitHub Actions —
# nhưng chia nhỏ thì lần chạy đầu tiên không biến thành một lần chạy dị thường,
# và một sự cố mạng giữa chừng chỉ mất phần dở chứ không mất cả mẻ.
HISTORY_BATCH = 250


def sync_player_history_past(db: Session, client: FPLClient,
                             batch: int = HISTORY_BATCH) -> dict:
    """Tổng cả-mùa của các MÙA ĐÃ QUA, từ `element-summary.history_past`.

    Đây là nền lịch sử mà engine mất khi FPL reset thống kê tại hạn vòng 1. Không
    có nó, `xmins` phải co mọi người về `PRIOR_START_RATE = 0.45` — một hằng số
    dùng chung cho Haaland lẫn thủ môn số ba — và `xpoints` co xG/90 về prior theo
    VỊ TRÍ. Đo được ở vòng 1 mùa 2026/27: Haaland, Isak, Szoboszlai và hàng loạt
    hậu vệ cùng ra 65–66 phút, tức mô hình đang nói "tôi không phân biệt được".

    Chỉ hỏi cầu thủ CHƯA hỏi bao giờ (`Player.history_fetched_at`). Mùa đã khép
    lại thì số không đổi nữa, nên đây là việc chạy một lần chứ không phải mỗi 6
    tiếng; cầu thủ mới xuất hiện giữa mùa sẽ được nhặt ở lần đồng bộ kế tiếp.
    """
    pending = db.scalars(
        select(Player).where(Player.history_fetched_at.is_(None)).limit(batch)
    ).all()
    if not pending:
        return {"fetched": 0, "rows": 0, "remaining": 0}

    existing = {
        (r.player_id, r.season_name): r
        for r in db.scalars(select(PlayerSeasonHistory)).all()
    }
    now = datetime.now(timezone.utc)
    written = 0
    summaries = client.element_summaries([p.id for p in pending])
    for player in pending:
        summary = summaries.get(player.id)
        if summary is None:
            continue        # lỗi mạng: để nguyên None, lần sau hỏi lại
        for row in summary.get("history_past", []):
            season = row.get("season_name")
            if not season:
                continue
            fields = dict(
                minutes=_i(row.get("minutes")), starts=_i(row.get("starts")),
                total_points=_i(row.get("total_points")),
                goals_scored=_i(row.get("goals_scored")),
                assists=_i(row.get("assists")),
                clean_sheets=_i(row.get("clean_sheets")),
                goals_conceded=_i(row.get("goals_conceded")),
                saves=_i(row.get("saves")),
                yellow_cards=_i(row.get("yellow_cards")),
                red_cards=_i(row.get("red_cards")),
                bonus=_i(row.get("bonus")), bps=_i(row.get("bps")),
                expected_goals=_f(row.get("expected_goals")),
                expected_assists=_f(row.get("expected_assists")),
                defensive_contribution=_f(row.get("defensive_contribution")),
                clearances_blocks_interceptions=_f(
                    row.get("clearances_blocks_interceptions")
                ),
            )
            found = existing.get((player.id, season))
            if found is None:
                found = PlayerSeasonHistory(
                    player_id=player.id, season_name=season, **fields
                )
                db.add(found)
                existing[(player.id, season)] = found
                written += 1
            else:
                for k, v in fields.items():
                    setattr(found, k, v)
        # Đánh dấu kể cả khi KHÔNG có mùa nào: tân binh chưa từng đá Ngoại hạng là
        # câu trả lời hợp lệ, không phải một lần hỏi hỏng cần thử lại mãi.
        player.history_fetched_at = now

    # `SessionLocal` dựng với `autoflush=False`, nên nếu không đẩy tay thì phép đếm
    # dưới đây chạy trên trạng thái TRƯỚC lô vừa xử lý và báo thừa đúng một lô.
    db.flush()
    remaining = db.scalar(
        select(func.count()).select_from(Player)
        .where(Player.history_fetched_at.is_(None))
    ) or 0
    _log(db, "FPL element history", f"{settings.fpl_base_url}/element-summary/", "ok",
         written, f"{len(pending)} cầu thủ, còn lại {remaining}")
    db.commit()
    return {"fetched": len(pending), "rows": written, "remaining": remaining}


# ------------------------------------------- thống kê thật theo từng trận ----
def sync_player_gameweek_stats(db: Session, client: FPLClient) -> dict:
    """Số liệu THẬT của từng cầu thủ trong từng trận, đổ vào `player_gameweek_stats`.

    Nguồn là `/event/{gw}/live/`: MỘT lệnh cho cả vòng đấu, thay vì một lệnh cho
    mỗi cầu thủ như `element-summary` (~600 lệnh mỗi lần đồng bộ, chạy 4 lần một
    ngày). Trường `explain[].fixture` cho biết dòng đó thuộc trận nào nên vẫn ghi
    được đúng khoá (player_id, fixture_id).

    Hai điều đã đo trên dữ liệu thật (vòng 1 mùa 2026/27, 2026-08-22), không suy
    luận lại:

    1. **`explain` có mặt cho CẢ cầu thủ mà câu lạc bộ chưa đá.** Lúc mới xong
       9/10 trận, cả 609/609 phần tử đều có đúng một mục `explain` — 330 mục
       trong đó là 0 phút, và phần lớn chỉ vì trận chưa diễn ra. Ghi thẳng chúng
       vào bảng sẽ biến "chưa tới lượt" thành "có cơ hội mà không được đá", tức
       bịa ra bằng chứng xoay tua. Nên chỉ ghi trận đã đá (`played_condition`).
    2. **Vòng đôi thì `stats` là TỔNG của hai trận** trong khi `explain` có hai
       mục — không tách được. Riêng những người đó phải hỏi `element-summary`,
       nơi `history` là từng trận một. Vòng 1 không có ai như vậy, nên nhánh này
       hiếm khi chạy và không kéo theo chi phí thường trực.
    """
    played = {
        f.id: f for f in db.scalars(select(Fixture).where(played_condition())).all()
    }
    if not played:
        return {"gameweeks": 0, "rows": 0, "reason": "chưa có trận nào đá xong"}

    by_event: dict[int, set[int]] = defaultdict(set)
    for f in played.values():
        if f.event is not None:
            by_event[f.event].add(f.id)

    known_players = set(db.scalars(select(Player.id)).all())
    checked = {g.id: g.data_checked for g in db.scalars(select(Gameweek)).all()}
    existing = {
        (r.player_id, r.fixture_id): r
        for r in db.scalars(select(PlayerGameweekStat)).all()
    }
    stored_fixtures = {fid for _, fid in existing}

    written = 0
    fetched_gws = 0
    dgw_players = 0
    for gw in sorted(by_event):
        # Vòng đã được FPL data-check thì số không đổi nữa — chỉ gọi lại khi còn
        # thiếu dòng. Nhờ vậy trạng thái ổn định là 1–2 lệnh mỗi lần đồng bộ chứ
        # không phải 38 lệnh vào cuối mùa.
        # Đủ điều kiện bỏ qua = FPL đã data-check vòng đó (số không đổi nữa) VÀ mọi
        # trận của vòng đã có dòng. Đo bằng ĐỘ PHỦ TRẬN, không phải tích cầu thủ ×
        # trận: chỉ cầu thủ của hai đội trong trận mới có dòng, nên phép so
        # "đủ mọi cặp" sẽ không bao giờ đúng và vòng nào cũng bị gọi lại.
        if checked.get(gw) and by_event[gw] <= stored_fixtures:
            continue
        try:
            data = client.event_live(gw)
        except Exception as exc:
            _log(db, f"FPL live GW{gw}", f"{settings.fpl_base_url}/event/{gw}/live/",
                 "error", 0, str(exc)[:300])
            continue
        fetched_gws += 1

        multi: list[int] = []
        for el in data.get("elements", []):
            pid = el.get("id")
            if pid not in known_players:
                continue
            fixtures_here = [
                x["fixture"] for x in el.get("explain", [])
                if x.get("fixture") in played and played[x["fixture"]].event == gw
            ]
            if not fixtures_here:
                continue
            if len(fixtures_here) > 1:
                multi.append(pid)      # vòng đôi: `stats` là tổng, phải hỏi riêng
                continue
            written += _upsert_gw_stat(
                db, existing, pid, fixtures_here[0], gw, el.get("stats", {}), played
            )

        if multi:
            dgw_players += len(multi)
            for pid, summary in client.element_summaries(multi).items():
                for h in summary.get("history", []):
                    if h.get("round") != gw or h.get("fixture") not in played:
                        continue
                    written += _upsert_gw_stat(
                        db, existing, pid, h["fixture"], gw, h, played
                    )

    _log(db, "FPL element stats", f"{settings.fpl_base_url}/event/<gw>/live/", "ok",
         written, f"{fetched_gws} vòng gọi lại, {dgw_players} người ở vòng đôi")
    db.commit()
    return {"gameweeks": fetched_gws, "rows": written, "dgw_players": dgw_players}


def _upsert_gw_stat(db: Session, existing: dict, player_id: int, fixture_id: int,
                    gameweek: int, stats: dict, played: dict) -> int:
    """Ghi/cập nhật một dòng. Trả 1 nếu là dòng mới, 0 nếu chỉ cập nhật.

    Không dùng `db.merge()`: merge khớp theo KHOÁ CHÍNH (`id`), vốn là None với
    đối tượng mới, nên nó sẽ INSERT mọi lần và vi phạm unique
    (player_id, fixture_id) ngay lần chạy thứ hai.
    """
    fixture = played[fixture_id]
    # `element-summary` nói thẳng sân nhà/đối thủ; `/event/live/` thì không, nên
    # suy từ CLB của cầu thủ trong bảng `fixtures`.
    was_home = stats.get("was_home")
    opponent = stats.get("opponent_team")
    if was_home is None or opponent is None:
        team_id = db.get(Player, player_id).team_id
        if team_id == fixture.team_h:
            was_home, opponent = True, fixture.team_a
        elif team_id == fixture.team_a:
            was_home, opponent = False, fixture.team_h

    row = existing.get((player_id, fixture_id))
    fields = dict(
        minutes=_i(stats.get("minutes")),
        total_points=_i(stats.get("total_points")),
        goals_scored=_i(stats.get("goals_scored")),
        assists=_i(stats.get("assists")),
        clean_sheets=_i(stats.get("clean_sheets")),
        goals_conceded=_i(stats.get("goals_conceded")),
        saves=_i(stats.get("saves")),
        bonus=_i(stats.get("bonus")),
        bps=_i(stats.get("bps")),
        expected_goals=_f(stats.get("expected_goals")),
        expected_assists=_f(stats.get("expected_assists")),
        starts=_i(stats.get("starts")),
        opponent_team=opponent,
        was_home=None if was_home is None else bool(was_home),
    )
    if row is None:
        row = PlayerGameweekStat(
            player_id=player_id, fixture_id=fixture_id, gameweek=gameweek, **fields
        )
        db.add(row)
        existing[(player_id, fixture_id)] = row
        return 1
    for k, v in fields.items():
        setattr(row, k, v)
    return 0


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
    # Xoá nguồn KHÔNG còn trong đăng bạ, kèm mọi tín hiệu của nó.
    #
    # Ghi đè theo tên thôi thì chưa đủ: 5 hàng "Nguồn demo A–E" đã bị bỏ khỏi code
    # nhưng vẫn nằm nguyên trong CSDL production, và không vòng lặp nào ở trên chạm
    # tới chúng — chỉ có phép xoá tường minh mới dọn được. Mọi nguồn trong bảng đều
    # do chính hàm này gieo, nên "không còn trong đăng bạ" đúng bằng "phải xoá".
    keep = {s.name for s in provider.get_sources()}
    stale = [s for s in db.scalars(select(ExpertSource)).all() if s.name not in keep]
    for s in stale:
        db.execute(delete(ExpertSignal).where(ExpertSignal.source_id == s.id))
        db.execute(delete(ExpertTrackRecord).where(ExpertTrackRecord.source_id == s.id))
        db.delete(s)

    _log(db, "Expert registry", "internal", "ok", len(name_to_source),
         f"đăng bạ {len(name_to_source)} nguồn; xoá {len(stale)} nguồn không còn "
         f"trong đăng bạ; tín hiệu thật do sync_expert_signals nạp", "community")
    db.commit()
    return {"sources": len(name_to_source), "removed": [s.name for s in stale]}


def sync_expert_signals(db: Session, bootstrap: dict | None = None) -> dict:
    """Tín hiệu chuyên gia THẬT, từ API công khai của FPL.

    Hai nguồn, và cả hai đều là dữ liệu first-party chứ không phải nội dung cào về:

    * **Mô hình riêng của FPL** (`ep_next`) — có ngay, kể cả tiền mùa.
    * **Đồng thuận nhóm dẫn đầu** — đội hình thật của top N bảng xếp hạng tổng.
      Chưa dùng được cho tới sau hạn chót vòng 1, và khi đó nó ghi rõ lý do thay
      vì im lặng trả rỗng.

    Ghi `is_mock=False`, nên `seed_experts` (vốn chỉ xoá hàng `is_mock=True`) không
    bao giờ đụng vào chúng.
    """
    from app.providers.expert_provider import compute_signal_score
    from app.providers.fpl_experts import (
        fetch_top_manager_consensus,
        fpl_model_signals,
        top_manager_signals,
    )

    if bootstrap is None:
        with FPLClient() as client:
            bootstrap = client.bootstrap_static()
    elements = bootstrap.get("elements", [])
    events = bootstrap.get("events", [])
    nxt = next((e["id"] for e in events if e.get("is_next")), None)
    cur = next((e["id"] for e in events if e.get("is_current")), None)

    by_name = {s.name: s for s in db.scalars(select(ExpertSource)).all()}
    players = {p.web_name: p for p in db.scalars(select(Player)).all()}

    seeds = list(fpl_model_signals(elements))
    notes: list[str] = [f"mô hình FPL: {len(seeds)} tín hiệu"]

    top_name = next((n for n in by_name if n.startswith("Top ")), None)
    if not settings.expert_fetch_top_managers:
        notes.append("đồng thuận nhóm dẫn đầu: đã tắt bằng cấu hình")
    elif top_name:
        # Đội hình chỉ công khai SAU hạn chót, nên vòng đọc được là vòng hiện tại.
        consensus = fetch_top_manager_consensus(gameweek=cur)
        if consensus.available:
            seeds += top_manager_signals(consensus, elements, top_name)
        notes.append(f"nhóm dẫn đầu: {consensus.reason}")

    # Chỉ xoá tín hiệu THẬT của lần đồng bộ trước; đăng bạ nguồn giữ nguyên.
    db.execute(delete(ExpertSignal).where(ExpertSignal.is_mock.is_(False)))
    written = 0
    for sig in seeds:
        src = by_name.get(sig.source_name)
        player = players.get(sig.web_name)
        if not src or not player:
            continue
        specificity = 0.85 if sig.signal_type in ("start", "penalty", "setpiece") else 0.6
        db.add(ExpertSignal(
            source_id=src.id, player_id=player.id, gameweek=nxt,
            signal_type=sig.signal_type, confidence=sig.confidence,
            summary=sig.summary, link=sig.link,
            signal_score=compute_signal_score(
                src.reliability, src.historical_accuracy, src.independence,
                specificity, sig.published_hours_ago,
            ),
            is_mock=False, origin_ref=sig.origin_ref,
            published_at=datetime.now(timezone.utc),
        ))
        written += 1

    _log(db, "Expert signals (FPL API)", "fantasy.premierleague.com", "ok",
         written, " · ".join(notes), "community")
    db.commit()
    return {"signals": written, "notes": notes}


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
    fixtures = db.scalars(select(Fixture).where(not_played_condition())).all()
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


# ----------------------------------- vai trò chiến thuật (nguồn ngoài) --------
def sync_player_roles(db: Session) -> dict:
    """Nạp vai trò chiến thuật từ API công khai của Premier League.

    Bật/tắt bằng `PL_POSITIONS_ENABLED` (hiện BẬT). robots.txt của
    premierleague.com cho phép crawl; Điều khoản sử dụng giới hạn ở "private and
    personal use" nên `source_name` được ghi xuống từng hàng để phần ghi nguồn đi
    kèm dữ liệu chứ không nằm rời ở một chỗ nào đó có thể quên.

    Khớp tên được giới hạn **trong chính CLB** mà nguồn báo, vì toàn giải có nhiều
    cặp họ trùng nhau và gán vai trò cho nhầm người là lỗi âm thầm: không có thông
    báo nào, chỉ có hai cầu thủ cùng bị tính sai.
    """
    from app.models import PlayerRole
    from app.providers.pl_positions import (
        SOURCE_NAME,
        SOURCE_URL,
        fetch_roles,
        get_pl_positions_provider,
    )
    from app.providers.player_goals import match_player_id
    from app.providers.probability import normalise_team

    provider = get_pl_positions_provider()
    if provider is None:
        _log(db, SOURCE_NAME, SOURCE_URL, "ok", 0,
             "PL_POSITIONS_ENABLED=false — vai trò chiến thuật giữ nguyên nguồn "
             "khai tay (nếu có); hệ số điều chỉnh theo vị trí bằng 1.0.", "official")
        db.commit()
        return {"enabled": False, "roles": 0}

    report = fetch_roles(provider)
    if not report.records:
        _log(db, SOURCE_NAME, SOURCE_URL, "error", 0,
             report.detail or "Không lấy được dữ liệu", "official")
        db.commit()
        return {"enabled": True, "roles": 0, "errors": report.errors}

    players = db.scalars(select(Player)).all()
    teams = {t.id: t.name for t in db.scalars(select(Team)).all()}
    by_norm_team: dict[str, list] = defaultdict(list)
    for pl in players:
        by_norm_team[normalise_team(teams.get(pl.team_id, ""))].append(pl)

    now = datetime.now(timezone.utc)
    existing = {r.player_id: r for r in db.scalars(select(PlayerRole)).all()}
    seen: set[int] = set()
    written = unmatched = ambiguous = rejected = 0

    for rec in report.records:
        candidates = by_norm_team.get(normalise_team(rec.club_name), [])
        if not candidates:
            # CLB của nguồn không khớp CLB nào của FPL (tên viết khác, hoặc đội mới)
            unmatched += 1
            continue
        pid = match_player_id(rec.display_name, candidates)
        if pid is None:
            unmatched += 1
            continue
        if pid in seen:
            # Hai bản ghi cùng trỏ về một cầu thủ FPL -> không biết cái nào đúng.
            continue
        player = next((x for x in candidates if x.id == pid), None)
        role = rec.role
        if role and player is not None:
            from app.engine.roles import is_plausible

            if not is_plausible(role, player.element_type):
                # Vai trò mâu thuẫn với vị trí FPL (thủ môn thành tiền đạo) -> bác,
                # nhưng vẫn ghi hàng với role=None để giao diện giải thích được.
                rejected += 1
                rec = replace_role(rec, None, (
                    f"Nguồn nói “{rec.raw_label}” nhưng FPL xếp cầu thủ này ở vị trí "
                    "khác — bác bỏ thay vì tin một trong hai"
                ))
                role = None
        seen.add(pid)
        values = dict(
            role=role, raw_label=rec.raw_label, reason=rec.reason or None,
            source_name=SOURCE_NAME, source_url=SOURCE_URL,
            external_name=rec.display_name, external_club=rec.club_name,
            season_label=report.season_label, fetched_at=now,
        )
        row = existing.get(pid)
        if row is not None:
            # Khoá duy nhất là `player_id`, không phải khoá chính -> `db.merge()` sẽ
            # luôn INSERT và vi phạm ràng buộc ở lần chạy thứ hai.
            for k, v in values.items():
                setattr(row, k, v)
        else:
            db.add(PlayerRole(player_id=pid, **values))
        if role:
            written += 1

    # Đếm trên TOÀN BỘ bản ghi của nguồn, không phải trên nhóm đã khớp — đây là
    # thông tin về chất lượng NGUỒN ("nguồn nói không đủ rõ bao nhiêu lần"), khác
    # với `unmatched` vốn là vấn đề khớp tên của phía ta.
    ambiguous = sum(1 for r in report.records if not r.role)
    detail = (
        f"{report.detail} · khớp FPL {len(seen)}, ghi {written} vai trò dùng được"
        f" · nguồn có {ambiguous} nhãn không quy được về một vai trò"
        " (để trống, không đoán)"
        f" · {unmatched} tên không khớp cầu thủ FPL nào trong CLB đó"
        + (f" · {rejected} bị bác vì mâu thuẫn vị trí FPL" if rejected else "")
    )
    _log(db, SOURCE_NAME, SOURCE_URL, "ok" if written else "error", written,
         detail, "official")
    db.commit()
    return {
        "enabled": True, "roles": written, "matched": len(seen),
        "ambiguous": ambiguous, "unmatched": unmatched, "rejected": rejected,
        "season": report.season_label,
    }


def replace_role(rec, role, reason):
    """Bản sao của bản ghi vai trò với `role`/`reason` mới (dataclass bất biến hoá)."""
    from dataclasses import replace as _replace

    return _replace(rec, role=role, reason=reason)
PLAYER_ODDS_DOCS = "https://the-odds-api.com/sports-odds-data/betting-markets.html"


# --------------------------------- kèo ghi bàn theo cầu thủ (Tier-2) ----------
def sync_player_goal_odds(db: Session, start_gw: int | None = None) -> dict:
    """Lấy `player_goal_scorer_anytime` cho các trận của vòng sắp tới.

    Khác `sync_odds` ở chi phí: kèo cầu thủ chỉ lấy được **mỗi lần một trận**, nên
    1 credit/trận (~10 credit mỗi lượt) thay vì 3 credit cho cả vòng. Vì thế bước
    này có **ngân sách**: giới hạn số trận, chỉ gọi lại sau
    `ODDS_PLAYER_GOALS_MIN_REFRESH_HOURS`, và dừng khi quota chạm mức dự trữ để
    phần kèo đội (ảnh hưởng λ của mọi cầu thủ) luôn được ưu tiên.

    Không có key / tắt tính năng / hết quota / nhà cái chưa ra kèo đều cho cùng một
    kết quả: không ghi gì, engine chạy y như trước.
    """
    from datetime import timedelta

    from app.engine import goal_market
    from app.models import PlayerGoalOdds
    from app.providers.player_goals import (
        fetch_player_goal_markets,
        get_player_goals_provider,
        match_player_id,
    )
    from app.providers.probability import normalise_team
    from app.services.common import planning_start_gw

    provider = get_player_goals_provider()
    if provider is None:
        reason = (
            "Chưa cấu hình ODDS_API_KEY"
            if not settings.odds_api_key
            else "ODDS_PLAYER_GOALS_ENABLED=false"
        )
        _log(db, "Odds API - kèo ghi bàn cầu thủ", PLAYER_ODDS_DOCS, "ok", 0,
             f"{reason} — thành phần ghi bàn giữ nguyên mô hình nội bộ.", "market")
        db.commit()
        return {"enabled": False, "players": 0, "fixtures": 0, "reason": reason}

    gw = start_gw or planning_start_gw(db)
    fixtures = db.scalars(
        select(Fixture).where(Fixture.event == gw, not_played_condition())
    ).all()
    if not fixtures:
        _log(db, "Odds API - kèo ghi bàn cầu thủ", PLAYER_ODDS_DOCS, "ok", 0,
             f"Vòng {gw} không có trận chưa đá — không gọi API.", "market")
        db.commit()
        return {"enabled": True, "players": 0, "fixtures": 0}

    teams = {t.id: t.name for t in db.scalars(select(Team)).all()}
    players = db.scalars(select(Player)).all()
    by_team: dict[int, list] = defaultdict(list)
    for pl in players:
        by_team[pl.team_id].append(pl)

    wanted: dict[tuple[str, str], Fixture] = {}
    for f in fixtures:
        key = (
            normalise_team(teams.get(f.team_h, "")),
            normalise_team(teams.get(f.team_a, "")),
        )
        if key[0] and key[1]:
            wanted[key] = f

    # Trận nào còn mới thì KHÔNG gọi lại — đây là toàn bộ lý do lịch 6 tiếng không
    # nuốt hết quota tháng.
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=settings.odds_player_goals_min_refresh_hours
    )
    latest: dict[int, datetime] = {}
    for row in db.scalars(
        select(PlayerGoalOdds).where(PlayerGoalOdds.gameweek == gw)
    ).all():
        got = row.fetched_at
        if got is None:
            continue
        if got.tzinfo is None:
            got = got.replace(tzinfo=timezone.utc)
        if row.fixture_id not in latest or got > latest[row.fixture_id]:
            latest[row.fixture_id] = got
    fresh = {
        key: (latest.get(fx.id) is not None and latest[fx.id] > cutoff)
        for key, fx in wanted.items()
    }

    report = fetch_player_goal_markets(provider, wanted=wanted, fresh_until=fresh)

    written = unmatched = 0
    touched_fixtures = 0
    for ev in report.events:
        key = (normalise_team(ev.home_name), normalise_team(ev.away_name))
        fx = wanted.get(key)
        if fx is None or not ev.quotes:
            continue
        candidates = by_team.get(fx.team_h, []) + by_team.get(fx.team_a, [])

        # Gom giá theo cầu thủ: mỗi nhà cái một giá, đồng thuận bằng TRUNG VỊ, và
        # **không** chuẩn hoá giữa các cầu thủ (nhiều người cùng ghi bàn được).
        grouped: dict[int, list] = defaultdict(list)
        for q in ev.quotes:
            pid = match_player_id(q.player_name, candidates)
            if pid is None:
                unmatched += 1
                continue
            grouped[pid].append(q)
        if not grouped:
            continue

        # Ghi lại cả trận: xoá trước rồi chèn, để cầu thủ mà nhà cái đã RÚT kèo
        # không còn nằm lại với giá cũ (dữ liệu cũ sống lâu hơn code).
        db.execute(delete(PlayerGoalOdds).where(PlayerGoalOdds.fixture_id == fx.id))
        touched_fixtures += 1
        now = datetime.now(timezone.utc)
        for pid, quotes in grouped.items():
            devigged = [q.prob for q in quotes if q.devigged and q.prob is not None]
            all_probs = [q.prob for q in quotes if q.prob is not None]
            prob = goal_market.consensus_probability(devigged)
            prob_raw = goal_market.consensus_probability(all_probs)
            updates = [q.last_update for q in quotes if q.last_update]
            db.add(PlayerGoalOdds(
                fixture_id=fx.id, player_id=pid, gameweek=fx.event,
                # prob = 0 khi chưa có giá nào loại biên được; `n_devigged` là thứ
                # phía engine đọc để biết có được dùng cho xP hay không.
                prob=round(prob or 0.0, 5),
                prob_raw=None if prob_raw is None else round(prob_raw, 5),
                n_bookmakers=len(quotes),
                n_devigged=len(devigged),
                best_price_yes=max(
                    (q.price_yes for q in quotes if q.price_yes), default=None
                ),
                best_price_no=max(
                    (q.price_no for q in quotes if q.price_no), default=None
                ),
                condition=settings.odds_player_goals_condition,
                source_name="odds_api",
                bookmaker_names=", ".join(sorted({q.bookmaker for q in quotes}))[:256],
                market_updated_at=max(updates) if updates else None,
                fetched_at=now,
            ))
            written += 1

    detail = report.detail
    if unmatched:
        detail += f" · {unmatched} tên cầu thủ không khớp FPL (bỏ qua, không đoán)"
    if settings.odds_player_goals_condition == goal_market.COND_UNKNOWN:
        detail += (
            " · ĐIỀU KIỆN ÁP DỤNG chưa xác minh -> kèo chỉ hiển thị tham khảo, "
            "KHÔNG vào xP (đặt ODDS_PLAYER_GOALS_CONDITION)"
        )
    for err in report.errors[:3]:
        detail += f" · lỗi: {err}"
    _log(db, "Odds API - kèo ghi bàn cầu thủ", PLAYER_ODDS_DOCS,
         "ok" if written or report.skipped_fresh else "error", written, detail,
         "market")
    db.commit()
    return {
        "enabled": True,
        "players": written,
        "fixtures": touched_fixtures,
        "unmatched": unmatched,
        "credits_used": report.credits_used,
        "requests_remaining": report.requests_remaining,
        "skipped_fresh": report.skipped_fresh,
        "skipped_budget": report.skipped_budget,
        "condition": settings.odds_player_goals_condition,
    }


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
def _record_model_version(db: Session) -> dict:
    """Ghi phiên bản engine hiện hành vào `model_versions`, một dòng cho mỗi phiên bản."""
    from app.models import ModelVersion

    v = settings.model_version
    row = db.scalar(
        select(ModelVersion).where(
            ModelVersion.version == v, ModelVersion.kind == "xp"
        )
    )
    if row is None:
        db.add(ModelVersion(version=v, kind="xp", notes="ghi tự động khi đồng bộ"))
        db.flush()
        return {"version": v, "recorded": "new"}
    return {"version": v, "recorded": "existing"}


def run_full_sync(db: Session, build_proj: bool = True,
                  detail: bool | None = None) -> dict:
    # None = theo cấu hình (SYNC_PLAYERS_DETAIL). Người gọi vẫn ép được True/False,
    # nhưng mặc định `False` cứng như trước sẽ khiến biến môi trường không có tác
    # dụng ở chính chỗ chạy thật là GitHub Actions.
    if detail is None:
        detail = settings.sync_players_detail
    result: dict = {"started_at": datetime.now(timezone.utc).isoformat()}
    with FPLClient() as client:
        result["bootstrap"] = sync_bootstrap(db, client)
        result["fixtures"] = sync_fixtures(db, client)
        # Phải nằm TRƯỚC build_projections: engine đọc `player_gameweek_stats` để
        # có phút thi đấu từng vòng và xG từng trận.
        if detail:
            result["player_stats"] = sync_player_gameweek_stats(db, client)
            result["player_history"] = sync_player_history_past(db, client)
    result["experts"] = seed_experts(db)
    result["expert_signals"] = sync_expert_signals(db)
    # Đóng vòng lặp đo lường: dự báo đã phát ra phải được chấm bằng kết quả thật,
    # nếu không thì độ chính xác trên trang Chuyên gia vĩnh viễn là "chưa đủ dữ liệu".
    from app.services.expert_scoring import score_finished_gameweeks

    result["expert_scoring"] = score_finished_gameweeks(db)
    # Vai trò chiến thuật: phải nằm TRƯỚC build_projections vì engine đọc bảng
    # `player_roles` để ghép cầu thủ với trục điểm yếu của đối thủ.
    result["player_roles"] = sync_player_roles(db)
    result["odds"] = sync_odds(db)
    # Phải nằm TRƯỚC build_projections: engine đọc `player_goal_odds` khi tính lại
    # thành phần ghi bàn của từng trận.
    result["player_goal_odds"] = sync_player_goal_odds(db)
    result["championship"] = sync_championship(db)
    if build_proj:
        from app.engine.projections import build_projections
        result["projections"] = build_projections(db)

        # Đóng băng dự báo của vòng sắp tới NGAY sau khi tính, rồi đổ kết quả thật
        # cho các vòng đã xong. Phải nằm trong mỗi lần đồng bộ, không phải một việc
        # chạy tay: `player_projections` bị xoá và ghi lại ở lần chạy sau, nên bỏ
        # một lần chụp là mất vĩnh viễn khả năng chấm vòng đó.
        from app.services.model_performance import (
            capture_captain_picks,
            capture_snapshots,
            fill_outcomes,
        )

        # Ghi lại phiên bản engine đang chạy. Bảng `model_versions` tồn tại từ
        # đầu nhưng chưa ai ghi vào — nghĩa là không có lịch sử để trả lời "dự báo
        # tháng trước do engine nào tạo ra", trong khi snapshot lại gắn nhãn phiên
        # bản. Ghi ở đây (idempotent) để hai thứ khớp nhau.
        result["model_version"] = _record_model_version(db)

        result["snapshot"] = capture_snapshots(db)
        result["captain_picks"] = capture_captain_picks(db)
        result["outcomes"] = fill_outcomes(db)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result

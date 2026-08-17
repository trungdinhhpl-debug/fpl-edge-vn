"""Player Explorer + detail services (spec §12)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExpertSignal,
    ExpertSource,
    Fixture,
    Player,
    PlayerGameweekStat,
    PlayerProjection,
)
from app.services.common import (
    horizon_xp,
    planning_start_gw,
    player_public,
    projections_for_gw,
    team_lookup,
    iso_utc,
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


def player_scorecard(db: Session, player_id: int, gw: int | None = None) -> dict | None:
    """Bộ chỉ số đầy đủ của một cầu thủ, gộp phân phối điểm với rủi ro.

    Gộp vào một chỗ vì chúng phải đọc CÙNG NHAU: xP 5.0 với P10 = 0 và rotation
    risk Cao không phải cùng một món hàng với xP 5.0 của người chắc suất. Trước đây
    các con số này nằm rải ở nhiều endpoint hoặc không tồn tại.
    """
    from app.models import ExpectedMinutes
    from app.services import player_risk as risk

    p = db.get(Player, player_id)
    if not p:
        return None

    gw = gw or planning_start_gw(db)
    proj = db.scalar(
        select(PlayerProjection).where(
            PlayerProjection.player_id == player_id,
            PlayerProjection.gameweek == gw,
        )
    )
    xm = db.scalar(
        select(ExpectedMinutes).where(
            ExpectedMinutes.player_id == player_id, ExpectedMinutes.gameweek == gw
        )
    )

    # mức người thay thế: tính trên toàn bộ cầu thủ cùng vòng, theo vị trí
    all_projs = db.scalars(
        select(PlayerProjection).where(PlayerProjection.gameweek == gw)
    ).all()
    pos_of = {
        row.id: row.element_type for row in db.scalars(select(Player)).all()
    }
    xp_by_pos: dict[int, list[float]] = {}
    for r in all_projs:
        pos = pos_of.get(r.player_id)
        if pos is not None:
            xp_by_pos.setdefault(pos, []).append(r.xp)
    repl = risk.replacement_level(xp_by_pos).get(p.element_type, 0.0)

    recent = [
        s.minutes
        for s in db.scalars(
            select(PlayerGameweekStat)
            .where(PlayerGameweekStat.player_id == player_id)
            .order_by(PlayerGameweekStat.gameweek)
        ).all()
    ]

    dist = None
    if proj:
        dist = {
            # xP giải tích là con số dùng để xếp hạng; mc_mean là trung bình của mô
            # phỏng. Hiện cả hai vì lệch nhau là dấu hiệu mô phỏng chưa khớp.
            "xp_mean": round(proj.xp, 2),
            "mc_mean": round(proj.mc_mean, 2),
            "median": round(proj.mc_median, 2),
            "p10": None if proj.mc_p10 is None else round(proj.mc_p10, 2),
            "p25": round(proj.mc_p25, 2),
            "p75": round(proj.mc_p75, 2),
            "p90": round(proj.mc_p90, 2),
            "p95_ceiling": round(proj.mc_ceiling, 2),
            "p_blank": proj.p_blank,
            "p_haul": proj.p_haul,
        }

    return {
        **player_public(p, team_lookup(db).get(p.team_id)),
        "gameweek": gw,
        "distribution": dist,
        "minutes": {
            "xmins": None if not proj else round(proj.xmins, 1),
            "p_start": None if not proj else proj.p_start,
            # P(không ra sân) chỉ có ở bảng expected_minutes, không ở projection
            "p_dnp": None if not xm else round(xm.p_no_play, 3),
            "p_60_plus": None if not xm else round(xm.p_60_plus, 3),
            "ci_low": None if not xm else round(xm.ci_low, 1),
            "ci_high": None if not xm else round(xm.ci_high, 1),
            "reason": None if not xm else xm.reason,
        },
        "vorp": risk.vorp(proj.xp if proj else 0.0, repl),
        "rotation_risk": risk.rotation_risk(proj.p_start if proj else 0.0, recent),
        "injury_risk": risk.injury_risk(
            p.status, p.chance_of_playing_next_round, p.news, p.news_added
        ),
        "price_risk": risk.price_risk(
            p.transfers_in_event, p.transfers_out_event, p.selected_by_percent
        ),
        "source_freshness": risk.source_freshness(db, p),
        "model_confidence": {
            "value": None if not proj else round(proj.confidence, 3),
            "label": None if not xm else xm.confidence,
            "model_version": None if not proj else proj.model_version,
            "data_cutoff": (
                iso_utc(proj.data_cutoff) if proj and proj.data_cutoff else None
            ),
        },
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
            # Chuyển nhượng ròng vòng này — ĐỘNG LƯỢNG, không phải dự báo đổi giá.
            # Ngưỡng đổi giá của FPL không công khai và co giãn theo tỷ lệ sở hữu,
            # nên con số duy nhất trung thực ở đây là dòng người thật, còn việc
            # nó có đủ để đổi giá hay không thì ta không biết (xem risk.price_risk).
            "net_transfers": (p.transfers_in_event or 0) - (p.transfers_out_event or 0),
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

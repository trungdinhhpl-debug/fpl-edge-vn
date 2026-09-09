"""Chi tiết của con số xP(1): mỗi trận một dòng, kèm nguồn và độ tin cậy.

xP(1) trên trang Cầu thủ là xP của **vòng sắp tới**, và nó là TỔNG qua các trận của
vòng đó — vòng đôi hai trận, vòng trắng không trận nào. Endpoint này trả về đúng cấu
trúc đó: `fixtures` là danh sách từng trận, `total` là tổng.

Nguyên tắc trình bày (đặc tả §6): thiếu dữ liệu thì nói **"Chưa đủ dữ liệu"**, không
bao giờ hiện 0%. Nên mọi trường ở đây là `None` khi chưa biết, và giao diện dựa vào
`None` để chọn câu chữ — không có chỗ nào điền 0 thay cho "không biết".
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.engine import goal_market as gm
from app.engine import roles as roles_mod
from app.models import (
    Fixture,
    Player,
    PlayerFixtureGoals,
    PlayerGoalOdds,
    PlayerProjection,
    Team,
)
from app.services.common import iso_utc, planning_start_gw, team_lookup


def _role_block(row: PlayerFixtureGoals) -> dict:
    """Vai trò chiến thuật + lợi thế vị trí. `None` ở đâu là chưa biết ở đó."""
    role = row.tactical_role
    return {
        "role": role,
        "role_label": roles_mod.ROLE_LABEL_VI.get(role) if role else None,
        "role_source": row.role_source,
        "role_confidence": row.role_confidence,
        "axis": row.weakness_axis,
        "axis_label": (
            roles_mod.AXIS_LABEL_VI.get(row.weakness_axis)
            if row.weakness_axis
            else None
        ),
        # axis_factor = None nghĩa là CHƯA ĐO ĐƯỢC điểm yếu trục đó (mẫu quá nhỏ
        # hoặc chưa có nguồn) — khác hẳn "đo được và bằng 1.0".
        "axis_factor": row.axis_factor,
        "position_factor": row.position_factor,
        "finishing_share": row.finishing_share,
        "sample_90s": row.weakness_sample_90s,
        "reliability": row.weakness_reliability,
        "note": row.weakness_note,
        "has_data": row.axis_factor is not None,
    }


def _market_block(row: PlayerFixtureGoals, odds: PlayerGoalOdds | None) -> dict:
    """Xác suất ghi bàn từ thị trường + điều kiện áp dụng + nguồn + thời điểm."""
    cond = row.market_condition or (odds.condition if odds else None)
    return {
        "prob": row.market_prob,
        "prob_raw": odds.prob_raw if odds else None,
        "expected_goals": row.market_exp_goals,
        "goals_per_90_on_pitch": row.market_r90,
        "condition": cond,
        "condition_label": gm.CONDITION_LABEL_VI.get(cond) if cond else None,
        # usable_for_xp = False nghĩa là có giá nhưng KHÔNG được đưa vào xP (chưa
        # loại biên, hoặc điều kiện áp dụng chưa xác minh).
        "usable_for_xp": row.market_usable,
        "weight_applied": row.market_weight,
        "source": row.market_source,
        "bookmakers": odds.bookmaker_names if odds else None,
        "n_bookmakers": row.market_n_bookmakers,
        "n_devigged": odds.n_devigged if odds else None,
        "best_price_yes": odds.best_price_yes if odds else None,
        "best_price_no": odds.best_price_no if odds else None,
        "market_updated_at": iso_utc(row.market_updated_at),
        "fetched_at": iso_utc(odds.fetched_at) if odds else None,
        "note": row.market_note,
        "has_data": row.market_prob is not None,
    }


def xp_goal_detail(db: Session, player_id: int, gw: int | None = None) -> dict | None:
    """Chi tiết thành phần ghi bàn của xP(1) cho một cầu thủ."""
    p = db.get(Player, player_id)
    if not p:
        return None

    gw = gw or planning_start_gw(db)
    teams: dict[int, Team] = team_lookup(db)

    proj = db.scalar(
        select(PlayerProjection).where(
            PlayerProjection.player_id == player_id,
            PlayerProjection.gameweek == gw,
        )
    )
    rows = db.scalars(
        select(PlayerFixtureGoals)
        .where(
            PlayerFixtureGoals.player_id == player_id,
            PlayerFixtureGoals.gameweek == gw,
        )
        .order_by(PlayerFixtureGoals.fixture_id)
    ).all()
    odds_rows = {
        r.fixture_id: r
        for r in db.scalars(
            select(PlayerGoalOdds).where(
                PlayerGoalOdds.player_id == player_id, PlayerGoalOdds.gameweek == gw
            )
        ).all()
    }
    kickoff = {
        f.id: f.kickoff_time
        for f in db.scalars(select(Fixture).where(Fixture.event == gw)).all()
    }

    fixtures = []
    for r in rows:
        opp = teams.get(r.opponent_team) if r.opponent_team else None
        fixtures.append({
            "fixture_id": r.fixture_id,
            "opponent": opp.short_name if opp else None,
            "opponent_name": opp.name if opp else None,
            "was_home": r.was_home,
            "kickoff_time": iso_utc(kickoff.get(r.fixture_id)),
            "minutes": {
                "xmins": r.xmins,
                "p_start": r.p_start,
                "p_sub": r.p_sub,
                "minutes_if_start": r.minutes_if_start,
                "minutes_if_sub": r.minutes_if_sub,
            },
            "goals": {
                "model": r.exp_goals_model,
                "open_play": r.exp_goals_open,
                "penalty": r.exp_goals_penalty,
                # Bóng chết ngoài 11m: FPL không phát số tách riêng -> None.
                "set_piece": r.exp_goals_set_piece,
                "after_position": r.exp_goals_adjusted,
                "final": r.exp_goals_final,
            },
            "position": _role_block(r),
            "market": _market_block(r, odds_rows.get(r.fixture_id)),
            "points_per_goal": r.goal_points,
            "xp_before": r.xp_before,
            "xp_after": r.xp_after,
            "xp_delta": r.xp_delta,
            "model_version": r.model_version,
            "data_cutoff": iso_utc(r.data_cutoff),
        })

    n_fixtures = proj.n_fixtures if proj else len(rows)
    delta_total = round(sum(f["xp_delta"] for f in fixtures), 3)
    xp_after = round(proj.xp, 3) if proj else None
    # xP "trước" suy từ xP hiện tại trừ phần điều chỉnh — KHÔNG chạy lại engine.
    # Hai con số vì thế luôn nhất quán với nhau, kể cả khi engine đổi.
    xp_before = None if xp_after is None else round(xp_after - delta_total, 3)

    return {
        "player_id": player_id,
        "player": p.web_name,
        "element_type": p.element_type,
        "gameweek": gw,
        "n_fixtures": n_fixtures,
        # Vòng trắng: không có trận nào -> xP của vòng bằng 0 và không có gì để điều
        # chỉnh. Nói thẳng thay vì hiện một bảng rỗng.
        "is_blank": n_fixtures == 0,
        "is_double": (n_fixtures or 0) >= 2,
        "xp_before": xp_before,
        "xp_after": xp_after,
        "xp_delta": delta_total,
        "fixtures": fixtures,
        "status": source_status(db, gw),
    }


def source_status(db: Session, gw: int | None = None) -> dict:
    """Trạng thái ba nguồn mà phần nâng cấp này dựa vào — có hay chưa có, và cần gì.

    Được trả kèm MỖI phản hồi chi tiết để giao diện không phải đoán vì sao một con
    số trống: trống vì chưa cấu hình nguồn, hay trống vì cầu thủ này không có kèo.

    Phần điểm yếu đối thủ được đọc từ **những gì engine đã ghi** (`axis_factor` trong
    `player_fixture_goals`), không đo lại. Hai lý do: đo lại phải nạp toàn bộ
    `player_gameweek_stats` cho mỗi request, và — quan trọng hơn — bản đo lại ở đây
    thiếu hiệu chỉnh sức mạnh đội đã gặp (nó cần mô hình sức mạnh đội mà chỉ engine
    dựng), nên nó sẽ mô tả SAI chính con số đang hiện bên cạnh.
    """
    players = db.scalars(select(Player)).all()
    role_table = roles_mod.load_roles(players, db=db)

    gw = gw or planning_start_gw(db)
    rows = db.scalars(
        select(PlayerFixtureGoals).where(PlayerFixtureGoals.gameweek == gw)
    ).all()
    measured = [r for r in rows if r.axis_factor is not None]
    opponents = {r.opponent_team for r in measured if r.opponent_team}
    reliab: dict[str, int] = {}
    for r in measured:
        key = r.weakness_reliability or "—"
        reliab[key] = reliab.get(key, 0) + 1
    if measured:
        detail = (
            f"{len(measured)} bản ghi cầu thủ-trận có hệ số trục, {len(opponents)} "
            "đối thủ đo được · độ tin cậy: "
            + ", ".join(f"{k} {v}" for k, v in sorted(reliab.items()))
        )
        needs = None
    elif not role_table.configured:
        detail = (
            "Chưa có nguồn vai trò chiến thuật nên không thể gán quan sát vào trục "
            "nào — hệ số điều chỉnh theo vị trí giữ ở 1.0."
        )
        needs = role_table.status().get("needs")
    else:
        detail = (
            "Có nguồn vai trò nhưng engine chưa đo được điểm yếu trục nào cho vòng "
            "này (thiếu xG theo từng trận, hoặc mẫu quá nhỏ) — hệ số giữ ở 1.0."
        )
        needs = (
            "player_gameweek_stats đã nạp (SYNC_PLAYERS_DETAIL=true) cho các vòng đã "
            "đá, và vai trò cho chính những cầu thủ đã ra sân."
        )

    n_odds = db.scalar(select(PlayerGoalOdds.id).limit(1))
    cond = settings.odds_player_goals_condition
    return {
        "roles": role_table.status(),
        "opponent_weakness": {
            "configured": bool(measured),
            "measured_rows": len(measured),
            "teams_with_data": len(opponents),
            "reliability_counts": reliab,
            "detail": detail,
            "needs": needs,
            "method": (
                "xG bị cho phép theo trục vị trí, chia cho mức tổng của chính đội đó "
                "(nên hàng phòng ngự rò rỉ đều ra 1.0 — phần đó đã nằm trong λ), "
                "hiệu chỉnh theo sức mạnh tấn công của các đội đã gặp, kéo về 1.0 "
                "theo cỡ mẫu n/(n+20) lượt-90."
            ),
        },
        "market": {
            "enabled": bool(settings.odds_api_key) and settings.odds_player_goals_enabled,
            "has_rows": n_odds is not None,
            "market_key": "player_goal_scorer_anytime",
            "condition": cond,
            "condition_label": gm.CONDITION_LABEL_VI.get(cond),
            "usable_for_xp": cond in (gm.COND_APPEARS, gm.COND_ANY),
            "weight": settings.odds_player_goals_weight,
            "weight_is_assumption": True,
            "needs": None if cond in (gm.COND_APPEARS, gm.COND_ANY) else (
                "Xác minh điều kiện thanh toán kèo ghi bàn với nhà cái (hoàn tiền "
                "nếu cầu thủ không ra sân?) rồi đặt ODDS_PLAYER_GOALS_CONDITION. "
                "The Odds API không phát hành luật thanh toán."
            ),
        },
        "positional_adjustment_enabled": settings.positional_adjustment_enabled,
    }


def goal_delta_by_player(db: Session, gw: int) -> dict[int, float]:
    """{player_id: tổng xp_delta của vòng} — cộng qua các trận (vòng đôi = hai trận)."""
    out: dict[int, float] = {}
    for r in db.scalars(
        select(PlayerFixtureGoals).where(PlayerFixtureGoals.gameweek == gw)
    ).all():
        out[r.player_id] = out.get(r.player_id, 0.0) + (r.xp_delta or 0.0)
    return {k: round(v, 3) for k, v in out.items()}

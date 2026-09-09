"""Nâng cấp thành phần ghi bàn của xP(1): toán kèo, vai trò, điểm yếu, tích hợp.

Các test ở đây khoá lại những chỗ **dễ sai một cách âm thầm**:

* xác suất ghi ít nhất một bàn bị dùng lẫn với số bàn kỳ vọng;
* `1/oddsYes` bị coi là đã loại biên;
* chuyển đổi kèo nhân thêm phút trong khi giá đã bao gồm rủi ro ra sân;
* hệ số vị trí bị áp cho cả quả 11m, hoặc cho toàn bộ xP;
* điều chỉnh bị cộng dồn mỗi lần chạy lại engine;
* hàng phòng ngự rò rỉ ĐỀU bị tính hai lần (λ đã tính rồi).
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.engine import goal_adjust as ga
from app.engine import goal_market as gm
from app.engine import opponent_profile as op_mod
from app.engine import roles as roles_mod
from app.scoring import RULES


# ============================================================ loại biên ======
def test_devig_uses_both_sides_of_the_same_book():
    """p = (1/Yes) / [(1/Yes) + (1/No)] — biên bị loại trong nội bộ một nhà cái."""
    p, devigged = gm.devig_yes_no(2.0, 2.0)
    assert devigged is True
    assert p == pytest.approx(0.5)

    # Biên thật: 1/2.10 + 1/2.10 = 0.952 -> tổng nhỏ hơn 1 là không thể với cặp
    # hai cửa, nên lấy cặp có biên dương.
    p, devigged = gm.devig_yes_no(1.90, 2.00)
    assert devigged is True
    assert p == pytest.approx((1 / 1.90) / (1 / 1.90 + 1 / 2.00))
    # và nó phải NHỎ HƠN xác suất thô 1/1.90
    assert p < 1 / 1.90


def test_yes_only_price_is_not_devigged():
    """Chỉ có cửa Yes thì `1/oddsYes` là xác suất CÓ BIÊN — phải bị đánh dấu."""
    p, devigged = gm.devig_yes_no(2.50, None)
    assert devigged is False
    assert p == pytest.approx(0.4)

    # và nó không được phép đi vào xP
    out = gm.market_expected_goals(
        p_market=0.4, p_start=0.9, p_sub=0.05,
        minutes_if_start=84, minutes_if_sub=20,
        condition=gm.COND_ANY, devigged=False,
    )
    assert out.usable_for_xp is False
    assert out.expected_goals is None


def test_consensus_is_median_and_never_normalised_across_players():
    """Trung vị giữa nhà cái; và hai cầu thủ có thể cùng có p cao — không chuẩn hoá."""
    assert gm.consensus_probability([0.30, 0.32, 0.95]) == pytest.approx(0.32)

    # Ba tiền đạo cùng trận, tổng xác suất 1.5 > 1: hợp lệ, vì nhiều người cùng
    # ghi bàn được. Hàm đồng thuận không biết gì về các cầu thủ khác, nên không có
    # chỗ nào để chuẩn hoá — đó chính là điều cần khoá.
    probs = [0.6, 0.5, 0.4]
    assert sum(gm.consensus_probability([x]) for x in probs) == pytest.approx(1.5)


# ======================================================= p -> số bàn =========
def test_at_least_one_goal_is_not_expected_goals():
    """p = 0.6 -> λ = 0.916, không phải 0.6. Lẫn hai cái là sai ~50% ở nhóm đầu."""
    lam = gm.poisson_lambda_from_prob(0.6)
    assert lam == pytest.approx(-math.log(0.4), abs=1e-9)
    assert lam > 0.9
    assert 1 - math.exp(-lam) == pytest.approx(0.6)


def test_scenario_conversion_reproduces_the_market_price():
    """r90 đã khớp phải tái tạo đúng giá thị trường qua hai kịch bản phút."""
    p_start, p_sub = 0.70, 0.20
    m_start, m_sub = 84.0, 20.0
    out = gm.market_expected_goals(
        p_market=0.35, p_start=p_start, p_sub=p_sub,
        minutes_if_start=m_start, minutes_if_sub=m_sub,
        condition=gm.COND_ANY,
    )
    lam_s = out.r90 * m_start / 90
    lam_b = out.r90 * m_sub / 90
    rebuilt = p_start * (1 - math.exp(-lam_s)) + p_sub * (1 - math.exp(-lam_b))
    assert rebuilt == pytest.approx(0.35, abs=1e-6)
    # E[G] lấy trong TỪNG kịch bản rồi cộng theo xác suất kịch bản
    assert out.expected_goals == pytest.approx(
        p_start * lam_s + p_sub * lam_b, abs=1e-9
    )


def test_condition_changes_the_answer():
    """"Hoàn tiền nếu không ra sân" là xác suất CÓ ĐIỀU KIỆN — số bàn phải khác."""
    kw = dict(
        p_market=0.30, p_start=0.55, p_sub=0.15,
        minutes_if_start=84.0, minutes_if_sub=20.0,
    )
    any_cond = gm.market_expected_goals(**kw, condition=gm.COND_ANY)
    appears = gm.market_expected_goals(**kw, condition=gm.COND_APPEARS)
    assert any_cond.usable_for_xp and appears.usable_for_xp
    # P(ghi | ra sân) = 0.30 với P(ra sân) = 0.70 nghĩa là xác suất không điều kiện
    # chỉ 0.21 -> ít bàn hơn hẳn cách hiểu kia.
    assert appears.expected_goals < any_cond.expected_goals


def test_unknown_condition_is_reference_only():
    """Không rõ điều kiện -> vẫn tính để HIỂN THỊ, nhưng không được vào xP."""
    out = gm.market_expected_goals(
        p_market=0.40, p_start=0.9, p_sub=0.05,
        minutes_if_start=84.0, minutes_if_sub=20.0,
        condition=gm.COND_UNKNOWN,
    )
    assert out.expected_goals is not None
    assert out.usable_for_xp is False
    assert "chưa xác minh" in out.reason.lower()


def test_minutes_are_not_counted_twice():
    """Giá thị trường đã gồm rủi ro ra sân -> KHÔNG nhân thêm phút lần nữa.

    Phép sai phổ biến là `E[G] = −ln(1−p) × xmins/90`. Với cầu thủ chắc suất hai
    cách gần như trùng nhau; với cầu thủ rủi ro thì cách sai ra thấp hơn nhiều vì
    phút bị trừ hai lần. Test khoá chiều lệch đó.
    """
    p = 0.25
    p_start, p_sub = 0.50, 0.20
    m_start, m_sub = 84.0, 20.0
    out = gm.market_expected_goals(
        p_market=p, p_start=p_start, p_sub=p_sub,
        minutes_if_start=m_start, minutes_if_sub=m_sub, condition=gm.COND_ANY,
    )
    xmins = p_start * m_start + p_sub * m_sub
    naive = gm.poisson_lambda_from_prob(p) * xmins / 90.0
    assert out.expected_goals > naive * 1.2


def test_no_appearance_means_no_conversion():
    out = gm.market_expected_goals(
        p_market=0.3, p_start=0.0, p_sub=0.0,
        minutes_if_start=84.0, minutes_if_sub=20.0, condition=gm.COND_ANY,
    )
    assert out.expected_goals is None and out.usable_for_xp is False


def test_blend_weight_zero_and_one():
    assert gm.blend(0.4, 0.9, 0.0) == pytest.approx(0.4)
    assert gm.blend(0.4, 0.9, 1.0) == pytest.approx(0.9)
    assert gm.blend(0.4, None, 0.7) == pytest.approx(0.4)   # thiếu kèo -> mô hình


# =========================================================== vai trò =========
@dataclass
class _P:
    id: int
    element_type: int
    web_name: str
    first_name: str = ""
    second_name: str = ""


def _write_roles(tmpdir, roles: dict, source="nguồn thử") -> str:
    path = os.path.join(tmpdir, "roles.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {"source": source, "updated_at": "2026-09-01T00:00:00Z", "roles": roles},
            fh, ensure_ascii=False,
        )
    return path


def test_roles_are_never_inferred_from_position_label():
    """Không có nguồn -> vai trò None, hệ số 1.0. Không đoán từ MID/FWD/DEF."""
    table = roles_mod.load_roles([_P(1, 3, "Saka")], path="")
    assert table.configured is False
    assert table.get(1).role is None
    assert table.get(1).finishing_share == 0.0
    assert table.status()["needs"]


def test_role_file_rejects_impossible_roles():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_roles(tmp, {"1": "ST", "2": "RW"})
        table = roles_mod.load_roles(
            [_P(1, 1, "Keeper"), _P(2, 3, "Saka")], path=path
        )
    # Thủ môn không thể là ST -> bị bác, KHÔNG bị thay bằng phỏng đoán
    assert table.get(1).role is None
    assert table.rejected == 1
    assert table.get(2).role == "RW"


def test_ambiguous_name_key_is_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_roles(tmp, {"silva": "CM"})
        table = roles_mod.load_roles(
            [_P(1, 3, "Silva"), _P(2, 3, "Silva")], path=path
        )
    assert table.by_player == {}
    assert table.unmatched == 1


def test_role_axis_mapping_is_crossed():
    """Tấn công bên PHẢI đối đầu cánh TRÁI phòng ngự, không phải cùng bên."""
    assert roles_mod.role_axis("RW") == roles_mod.AXIS_LEFT_DEF
    assert roles_mod.role_axis("LW") == roles_mod.AXIS_RIGHT_DEF
    assert roles_mod.role_axis("ST") == roles_mod.AXIS_CENTRAL
    assert roles_mod.role_axis("AM") == roles_mod.AXIS_CENTRAL
    assert roles_mod.role_axis("CB") == roles_mod.AXIS_AERIAL


def test_creator_roles_get_less_of_the_axis_than_finishers():
    """Đối thủ yếu cánh trái không tự động làm MỌI cầu thủ cánh phải ghi nhiều hơn."""
    assert roles_mod.FINISHING_SHARE["RB"] < roles_mod.FINISHING_SHARE["RW"]
    assert roles_mod.FINISHING_SHARE["RW"] < roles_mod.FINISHING_SHARE["ST"]
    assert roles_mod.FINISHING_SHARE["ST"] == 1.0


# ================================================== điểm yếu đối thủ ========
@dataclass
class _Stat:
    player_id: int
    opponent_team: int
    minutes: int
    expected_goals: float


def _role_table(mapping: dict[int, str]) -> roles_mod.RoleTable:
    return roles_mod.RoleTable(
        by_player={
            pid: roles_mod.RoleAssignment(role=r, source="thử")
            for pid, r in mapping.items()
        },
        source="thử", configured=True,
    )


def _many(pid: int, opp: int, xg: float, n: int) -> list[_Stat]:
    return [_Stat(pid, opp, 90, xg) for _ in range(n)]


def test_uniformly_leaky_defence_gives_factor_one():
    """Rò rỉ ĐỀU khắp -> 1.0 ở mọi trục: phần đó đã nằm trong λ, không tính hai lần."""
    # đội 1 cho phép gấp đôi đội 2 trên MỌI trục
    stats = (
        _many(10, 1, 0.40, 40) + _many(20, 1, 0.40, 40)        # RW, ST vs đội 1
        + _many(10, 2, 0.20, 40) + _many(20, 2, 0.20, 40)      # RW, ST vs đội 2
    )
    table = op_mod.build_weakness_table(
        stats=stats, role_table=_role_table({10: "RW", 20: "ST"}),
        player_team={10: 5, 20: 6},
    )
    for team_id in (1, 2):
        prof = table.profile(team_id)
        for axis in (roles_mod.AXIS_LEFT_DEF, roles_mod.AXIS_CENTRAL):
            assert prof.by_axis[axis].factor == pytest.approx(1.0, abs=0.02)


def test_lopsided_defence_is_detected_on_the_right_axis():
    """Đội yếu riêng một cánh -> hệ số > 1 đúng ở trục đó, < 1 ở trục kia."""
    stats = (
        _many(10, 1, 0.60, 40) + _many(20, 1, 0.15, 40)     # đội 1 rò cánh trái
        + _many(10, 2, 0.30, 40) + _many(20, 2, 0.30, 40)   # đội 2 đều
    )
    table = op_mod.build_weakness_table(
        stats=stats, role_table=_role_table({10: "RW", 20: "ST"}),
        player_team={10: 5, 20: 6},
    )
    prof = table.profile(1)
    assert prof.by_axis[roles_mod.AXIS_LEFT_DEF].factor > 1.05
    assert prof.by_axis[roles_mod.AXIS_CENTRAL].factor < 0.95


def test_small_sample_is_pulled_back_to_one():
    """Mẫu bé -> hệ số gần 1.0 hơn mẫu lớn, cùng một mức lệch."""
    def factor(n: int) -> float:
        stats = (
            _many(10, 1, 0.60, n) + _many(20, 1, 0.15, n)
            + _many(10, 2, 0.30, 40) + _many(20, 2, 0.30, 40)
        )
        t = op_mod.build_weakness_table(
            stats=stats, role_table=_role_table({10: "RW", 20: "ST"}),
            player_team={10: 5, 20: 6},
        )
        return t.profile(1).by_axis[roles_mod.AXIS_LEFT_DEF].factor

    small, big = factor(5), factor(60)
    assert abs(small - 1.0) < abs(big - 1.0)


def test_tiny_sample_reports_no_data_not_a_number():
    stats = _many(10, 1, 0.6, 2) + _many(20, 2, 0.3, 40)
    table = op_mod.build_weakness_table(
        stats=stats, role_table=_role_table({10: "RW", 20: "ST"}),
        player_team={10: 5, 20: 6},
    )
    m = table.profile(1).by_axis[roles_mod.AXIS_LEFT_DEF]
    assert m.n_90s < op_mod.MIN_90S_TO_REPORT
    assert m.has_data is False
    assert m.reliability == "Chưa đủ dữ liệu"


def test_no_roles_means_not_configured_and_factor_one():
    table = op_mod.build_weakness_table(
        stats=_many(10, 1, 0.6, 40),
        role_table=roles_mod.RoleTable(by_player={}, source=None, configured=False),
        player_team={10: 5},
    )
    assert table.configured is False
    adj = op_mod.positional_adjustment(
        assignment=roles_mod.UNKNOWN_ROLE, table=table, opponent_team_id=1
    )
    assert adj.factor == 1.0
    assert adj.has_data is False


def test_strength_of_schedule_changes_the_verdict():
    """Cho phép một đội MẠNH 0.5 xG khác với cho phép một đội YẾU 0.5 xG."""
    stats = (
        _many(10, 1, 0.40, 40) + _many(20, 1, 0.20, 40)
        + _many(10, 2, 0.40, 40) + _many(20, 2, 0.20, 40)
    )
    # cầu thủ 10 thuộc đội 5 (mạnh), cầu thủ 20 thuộc đội 6 (yếu)
    plain = op_mod.build_weakness_table(
        stats=stats, role_table=_role_table({10: "RW", 20: "ST"}),
        player_team={10: 5, 20: 6},
    )
    adjusted = op_mod.build_weakness_table(
        stats=stats, role_table=_role_table({10: "RW", 20: "ST"}),
        player_team={10: 5, 20: 6},
        team_attack_strength={5: 2.2, 6: 0.9},
    )
    a = plain.profile(1).by_axis[roles_mod.AXIS_LEFT_DEF].factor
    b = adjusted.profile(1).by_axis[roles_mod.AXIS_LEFT_DEF].factor
    # sau hiệu chỉnh, 0.40 xG của một đội mạnh không còn là bằng chứng yếu như trước
    assert b < a


# ===================================================== tích hợp vào xP ======
def _adj(**kw):
    base = dict(
        goal_points=4, exp_goals_model=0.50, exp_goals_penalty=0.0,
        p_start=0.9, p_sub=0.05, xmins=78.0,
    )
    base.update(kw)
    return ga.adjust_fixture_goals(**base)


def test_no_sources_means_no_change():
    out = _adj()
    assert out.position_factor == 1.0
    assert out.exp_goals_final == pytest.approx(0.50)
    assert out.xp_delta == pytest.approx(0.0)
    assert out.changed is False


def test_formula_is_delta_goals_times_points_per_goal():
    """xP_mới − xP_cũ = (bàn_mới − bàn_cũ) × điểm_mỗi_bàn, không gì khác."""
    out = _adj(
        goal_points=7, market_prob=0.40, market_condition=gm.COND_ANY,
        market_weight=1.0,
    )
    assert out.xp_delta == pytest.approx(
        (out.exp_goals_final - out.exp_goals_model) * 7
    )


def test_goal_points_come_from_the_rules_not_a_constant():
    """Thủ môn ghi bàn 10 điểm ở mùa này — phải đọc từ cấu hình luật."""
    gk_points = RULES.goal_points[1]
    out = _adj(
        goal_points=gk_points, market_prob=0.08,
        market_condition=gm.COND_ANY, market_weight=1.0,
    )
    assert out.goal_points == gk_points
    assert out.xp_delta == pytest.approx(
        (out.exp_goals_final - out.exp_goals_model) * gk_points
    )


def test_position_factor_never_touches_the_penalty_part():
    """Hệ số bóng sống KHÔNG được áp lên chấm 11m."""
    stats = (
        _many(10, 1, 0.60, 60) + _many(20, 1, 0.15, 60)
        + _many(10, 2, 0.30, 60) + _many(20, 2, 0.30, 60)
    )
    table = op_mod.build_weakness_table(
        stats=stats, role_table=_role_table({10: "RW", 20: "ST"}),
        player_team={10: 5, 20: 6},
    )
    assignment = roles_mod.RoleAssignment(role="RW", source="thử")
    out = ga.adjust_fixture_goals(
        goal_points=5, exp_goals_model=0.50, exp_goals_penalty=0.20,
        p_start=0.9, p_sub=0.05, xmins=78.0,
        assignment=assignment, weakness=table, opponent_team_id=1,
    )
    f = out.position_factor
    assert f > 1.0                                    # đối thủ yếu cánh trái
    # bóng sống 0.30 được co giãn, penalty 0.20 giữ nguyên
    assert out.exp_goals_adjusted == pytest.approx(0.30 * f + 0.20)
    assert out.exp_goals_penalty == pytest.approx(0.20)


def test_unknown_role_gives_exactly_one():
    stats = _many(10, 1, 0.60, 60) + _many(20, 2, 0.30, 60)
    table = op_mod.build_weakness_table(
        stats=stats, role_table=_role_table({10: "RW", 20: "ST"}),
        player_team={10: 5, 20: 6},
    )
    out = ga.adjust_fixture_goals(
        goal_points=5, exp_goals_model=0.5, exp_goals_penalty=0.0,
        p_start=0.9, p_sub=0.05, xmins=78.0,
        assignment=roles_mod.UNKNOWN_ROLE, weakness=table, opponent_team_id=1,
    )
    assert out.position_factor == 1.0
    assert out.xp_delta == pytest.approx(0.0)


def test_unverified_condition_keeps_xp_unchanged_but_still_reports():
    """Kèo chưa xác minh điều kiện: hiện số để đọc, nhưng xP đứng nguyên."""
    out = _adj(market_prob=0.45, market_condition=gm.COND_UNKNOWN, market_weight=0.7)
    assert out.market_prob == 0.45
    assert out.market_exp_goals is not None          # vẫn có số để hiển thị
    assert out.market_usable is False
    assert out.xp_delta == pytest.approx(0.0)


def test_undevigged_price_keeps_xp_unchanged():
    out = _adj(
        market_prob=0.45, market_devigged=False,
        market_condition=gm.COND_ANY, market_weight=0.7,
    )
    assert out.market_usable is False
    assert out.xp_delta == pytest.approx(0.0)


def test_broken_price_is_rejected_not_read_as_zero():
    """Giá 0 / 1 / âm là dữ liệu HỎNG, không phải "xác suất bằng 0"."""
    for bad in (0.0, 1.0, -0.2, 1.4):
        out = gm.market_expected_goals(
            p_market=bad, p_start=0.9, p_sub=0.05,
            minutes_if_start=84.0, minutes_if_sub=20.0, condition=gm.COND_ANY,
        )
        assert out.usable_for_xp is False
        assert out.expected_goals is None
        assert "không hợp lệ" in out.reason

    # và nó không được kéo số bàn kỳ vọng xuống qua đường pha trộn
    adj = _adj(market_prob=0.0, market_condition=gm.COND_ANY, market_weight=1.0)
    assert adj.exp_goals_final == pytest.approx(adj.exp_goals_model)
    assert adj.xp_delta == pytest.approx(0.0)


def test_stale_odds_are_shown_but_not_used():
    out = _adj(
        market_prob=0.45, market_condition=gm.COND_ANY, market_weight=0.7,
        market_blocked_reason="Kèo lấy về 100 giờ trước, quá mốc 72 giờ",
    )
    assert out.market_prob == 0.45          # vẫn hiển thị được
    assert out.market_usable is False
    assert out.xp_delta == pytest.approx(0.0)
    assert "quá mốc" in (out.market_note or "")


def test_engine_ignores_stale_odds(db):
    """Kèo cũ hơn ngưỡng thì engine không dùng — xP giống như khi không có kèo."""
    from datetime import timedelta

    from app.config import settings
    from app.engine.projections import build_projections, get_planning_start_gw
    from app.models import PlayerGoalOdds, PlayerProjection

    gw = get_planning_start_gw(db)

    def xp_map():
        return {
            r.player_id: r.xp
            for r in db.scalars(
                select(PlayerProjection).where(PlayerProjection.gameweek == gw)
            ).all()
        }

    try:
        _clear_goal_odds(db)
        build_projections(db, horizon=1, mc_iterations=300)
        before = xp_map()

        _seed_goal_odds(db, gw, prob=0.45, condition=gm.COND_ANY)
        old_time = datetime.now(timezone.utc) - timedelta(
            hours=settings.odds_player_goals_max_age_hours + 24
        )
        for row in db.scalars(select(PlayerGoalOdds)).all():
            row.fetched_at = old_time
        db.commit()
        db.expire_all()
        build_projections(db, horizon=1, mc_iterations=300)
        after = xp_map()

        assert before and before.keys() == after.keys()
        for pid, xp in before.items():
            assert after[pid] == pytest.approx(xp, abs=1e-6)
    finally:
        _clear_goal_odds(db)
        db.expire_all()
        build_projections(db, horizon=2, mc_iterations=300)


def test_player_who_will_not_play_gets_no_adjustment():
    out = _adj(
        p_start=0.0, p_sub=0.0, xmins=0.0, exp_goals_model=0.0,
        market_prob=0.4, market_condition=gm.COND_ANY, market_weight=1.0,
    )
    assert out.market_usable is False
    assert out.xp_delta == pytest.approx(0.0)


def test_positional_adjustment_can_be_switched_off():
    stats = _many(10, 1, 0.60, 60) + _many(20, 2, 0.30, 60)
    table = op_mod.build_weakness_table(
        stats=stats, role_table=_role_table({10: "RW", 20: "ST"}),
        player_team={10: 5, 20: 6},
    )
    out = ga.adjust_fixture_goals(
        goal_points=5, exp_goals_model=0.5, exp_goals_penalty=0.0,
        p_start=0.9, p_sub=0.05, xmins=78.0,
        assignment=roles_mod.RoleAssignment(role="RW", source="thử"),
        weakness=table, opponent_team_id=1, positional_enabled=False,
    )
    assert out.position_factor == 1.0
    assert out.xp_delta == pytest.approx(0.0)


def test_scenario_minutes_match_xmins():
    m_start, m_sub = ga.scenario_minutes(xmins=60.0, p_start=0.7, p_sub=0.1)
    assert 0.7 * m_start + 0.1 * m_sub == pytest.approx(60.0)
    assert m_start <= 90.0 and m_sub < m_start


# ============================================= adapter The Odds API =========
def test_parse_event_market_reads_description_as_player():
    from app.providers import player_goals as pg

    payload = {
        "id": "ev1", "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": "2026-09-12T14:00:00Z",
        "bookmakers": [{
            "title": "Book A", "key": "a",
            "markets": [{
                "key": "player_goal_scorer_anytime",
                "last_update": "2026-09-11T10:00:00Z",
                "outcomes": [
                    {"name": "Yes", "description": "Bukayo Saka", "price": 2.5},
                    {"name": "No", "description": "Bukayo Saka", "price": 1.55},
                    {"name": "Yes", "description": "Cole Palmer", "price": 3.2},
                ],
            }],
        }],
    }
    ev = pg.parse_event_market(payload)
    by_name = {q.player_name: q for q in ev.quotes}
    assert by_name["Bukayo Saka"].devigged is True
    assert by_name["Cole Palmer"].devigged is False      # chỉ có cửa Yes
    assert by_name["Bukayo Saka"].last_update is not None


def test_parse_event_market_ignores_other_markets_and_bad_prices():
    from app.providers import player_goals as pg

    ev = pg.parse_event_market({
        "id": "ev2", "home_team": "A", "away_team": "B",
        "bookmakers": [{
            "title": "B", "markets": [
                {"key": "h2h", "outcomes": [{"name": "A", "price": 2.0}]},
                {"key": "player_goal_scorer_anytime", "outcomes": [
                    {"name": "Yes", "description": "X", "price": 1.0},
                    {"name": "Yes", "description": "Y", "price": "bad"},
                ]},
            ],
        }],
    })
    assert ev.quotes == []


def test_player_name_matching_refuses_ambiguity():
    from app.providers import player_goals as pg

    cands = [
        _P(1, 4, "Haaland", "Erling", "Haaland"),
        _P(2, 3, "Silva", "Bernardo", "Silva"),
        _P(3, 2, "Silva", "Thiago", "Silva"),
    ]
    assert pg.match_player_id("Erling Haaland", cands) == 1
    assert pg.match_player_id("Haaland", cands) == 1
    # hai Silva trong cùng một trận -> không đoán
    assert pg.match_player_id("Silva", cands) is None
    assert pg.match_player_id("Không Tồn Tại", cands) is None


def test_name_matching_is_by_word_not_substring():
    """"ola" nằm trong "nicolas" — khớp theo chuỗi con sẽ gán giá cho nhầm người."""
    from app.providers import player_goals as pg

    cands = [
        _P(1, 3, "Ola", "Ola", "Aina"),
        _P(2, 4, "Jackson", "Nicolas", "Jackson"),
    ]
    # phải ra Jackson, tuyệt đối không phải Ola
    assert pg.match_player_id("Nicolas Jackson", cands) == 2

    # và tên ngắn vẫn khớp khi nó là MỘT TỪ trọn vẹn trong tên đầy đủ
    cands2 = [_P(3, 3, "Garnacho", "Alejandro", "Garnacho Ferreyra")]
    assert pg.match_player_id("Alejandro Garnacho", cands2) == 3

    # một cái tên chỉ trùng chuỗi con thì KHÔNG được khớp
    assert pg.match_player_id("Nicolas Something", [cands[0]]) is None


def test_accents_do_not_block_matching():
    """Tên nhà cái thường bỏ dấu: "Buendia" phải khớp "Buendía"."""
    from app.providers import player_goals as pg

    cands = [_P(1, 3, "Buendía", "Emiliano", "Buendía Stati")]
    assert pg.match_player_id("Emiliano Buendia Stati", cands) == 1
    assert pg.match_player_id("Daniel Munoz", [_P(2, 2, "Muñoz", "Daniel", "Muñoz")]) == 2


def test_budget_stops_when_quota_is_low(monkeypatch):
    """Hết quota thì DỪNG, để kèo đội (ảnh hưởng λ mọi cầu thủ) không bị ăn hết."""
    from app.providers import player_goals as pg

    class _Fake(pg.PlayerGoalsProvider):
        def __init__(self):
            super().__init__("k")
            self.calls = 0
            self.requests_remaining = 5      # dưới mức dự trữ mặc định (30)

        def list_events(self):
            return [
                {"id": "1", "home_team": "Arsenal", "away_team": "Chelsea",
                 "commence_time": "2026-09-12T14:00:00Z"},
            ]

        def event_goal_market(self, event):
            self.calls += 1
            return pg.EventGoalMarket("1", "Arsenal", "Chelsea", "")

    prov = _Fake()
    report = pg.fetch_player_goal_markets(
        prov, wanted={("arsenal", "chelsea"): object()},
    )
    assert prov.calls == 0
    assert report.skipped_budget == 1
    assert report.credits_used == 0


def test_fresh_fixtures_are_not_refetched():
    from app.providers import player_goals as pg

    class _Fake(pg.PlayerGoalsProvider):
        def __init__(self):
            super().__init__("k")
            self.calls = 0
            self.requests_remaining = 400

        def list_events(self):
            return [{"id": "1", "home_team": "Arsenal", "away_team": "Chelsea",
                     "commence_time": "2026-09-12T14:00:00Z"}]

        def event_goal_market(self, event):
            self.calls += 1
            return pg.EventGoalMarket("1", "Arsenal", "Chelsea", "")

    prov = _Fake()
    key = ("arsenal", "chelsea")
    report = pg.fetch_player_goal_markets(
        prov, wanted={key: object()}, fresh_until={key: True}
    )
    assert prov.calls == 0 and report.skipped_fresh == 1


def test_network_failure_is_no_data_not_an_exception():
    from app.providers import player_goals as pg

    class _Broken(pg.PlayerGoalsProvider):
        def list_events(self):
            raise RuntimeError("503 upstream")

    report = pg.fetch_player_goal_markets(
        pg.PlayerGoalsProvider("k") if False else _Broken("k"), wanted={}
    )
    assert report.events == [] and report.errors


# =============================================== end-to-end qua engine ======
def _seed_goal_odds(db, gw: int, prob: float, condition: str, n_devigged: int = 3):
    """Gắn kèo ghi bàn cho MỌI cầu thủ có trận ở vòng `gw` trong DB demo."""
    from app.models import Fixture, Player, PlayerGoalOdds

    db.execute(
        PlayerGoalOdds.__table__.delete()
    )
    fixtures = db.scalars(select(Fixture).where(Fixture.event == gw)).all()
    players = db.scalars(select(Player)).all()
    by_team: dict[int, list] = {}
    for p in players:
        by_team.setdefault(p.team_id, []).append(p)
    n = 0
    now = datetime.now(timezone.utc)
    for f in fixtures:
        for team_id in (f.team_h, f.team_a):
            for p in by_team.get(team_id, []):
                db.add(PlayerGoalOdds(
                    fixture_id=f.id, player_id=p.id, gameweek=gw, prob=prob,
                    prob_raw=prob, n_bookmakers=4, n_devigged=n_devigged,
                    best_price_yes=1 / max(prob, 0.01), best_price_no=2.0,
                    condition=condition, source_name="test",
                    bookmaker_names="Book A, Book B", fetched_at=now,
                ))
                n += 1
    db.commit()
    return n


def _clear_goal_odds(db):
    from app.models import PlayerGoalOdds

    db.execute(PlayerGoalOdds.__table__.delete())
    db.commit()


def test_engine_end_to_end_market_moves_xp_by_exactly_the_formula(db):
    """Chạy thật build_projections: xP đổi đúng (Δbàn × điểm/bàn), không hơn."""
    from app.engine.projections import build_projections, get_planning_start_gw
    from app.models import PlayerFixtureGoals, PlayerProjection

    gw = get_planning_start_gw(db)
    try:
        _seed_goal_odds(db, gw, prob=0.45, condition=gm.COND_ANY)
        build_projections(db, horizon=1, mc_iterations=400)

        rows = db.scalars(
            select(PlayerFixtureGoals).where(PlayerFixtureGoals.gameweek == gw)
        ).all()
        assert rows, "phải có bản ghi theo từng trận"

        moved = [r for r in rows if abs(r.xp_delta) > 1e-6]
        assert moved, "kèo đã xác minh điều kiện thì phải dịch được xP"

        for r in moved:
            # công thức, từng trận
            assert r.xp_delta == pytest.approx(
                (r.exp_goals_final - r.exp_goals_model) * r.goal_points, abs=1e-3
            )
            assert r.xp_after == pytest.approx(r.xp_before + r.xp_delta, abs=1e-3)
            assert r.market_usable is True
            # không có vai trò -> hệ số vị trí đúng 1.0, và bàn sau điều chỉnh vị
            # trí phải bằng bàn của mô hình
            assert r.position_factor == pytest.approx(1.0)
            assert r.exp_goals_adjusted == pytest.approx(r.exp_goals_model, abs=1e-6)

        # tổng theo cầu thủ khớp với xP đã ghi
        per_player: dict[int, float] = {}
        for r in rows:
            per_player[r.player_id] = per_player.get(r.player_id, 0.0) + r.xp_after
        pid = moved[0].player_id
        proj = db.scalar(
            select(PlayerProjection).where(
                PlayerProjection.player_id == pid, PlayerProjection.gameweek == gw
            )
        )
        assert proj.xp == pytest.approx(per_player[pid], abs=0.02)
    finally:
        _clear_goal_odds(db)
        build_projections(db, horizon=2, mc_iterations=400)


def test_engine_end_to_end_positional_advantage(db, monkeypatch):
    """Đường vai trò + điểm yếu chạy thật qua engine, không cần kèo.

    Tệp vai trò ở đây là **dữ liệu thử tổng hợp** đứng thay cho một nguồn thật; nó
    được gán theo `element_type` CHỈ VÌ đây là test cần một bảng vai trò nào đó —
    bản thân code không bao giờ làm phép gán đó (xem app/engine/roles.py).
    """
    from app.config import settings
    from app.engine.projections import build_projections, get_planning_start_gw
    from app.models import Fixture, Player, PlayerFixtureGoals, PlayerGameweekStat

    gw = get_planning_start_gw(db)
    players = db.scalars(select(Player)).all()
    role_of = {1: "GK", 2: "CB", 3: "RW", 4: "ST"}
    tmpdir = tempfile.mkdtemp()
    path = _write_roles(
        tmpdir,
        {str(p.id): role_of[p.element_type] for p in players},
        source="dữ liệu thử",
    )

    # Lịch sử tổng hợp: đội `weak_team` cho phép cánh (RW) nhiều xG hơn hẳn mức
    # tổng của chính nó, các đội khác thì đều. Đó là dạng LỆCH mà module phải bắt.
    finished = db.scalars(
        select(Fixture).where(Fixture.event < gw).order_by(Fixture.id)
    ).all()
    assert finished, "DB demo phải có trận đã đá"
    # Đội "yếu cánh trái" phải là đội CÓ ĐÁ ở vòng đang xét, nếu không thì không có
    # trận nào để hệ số xuất hiện.
    upcoming = db.scalars(
        select(Fixture).where(Fixture.event == gw).order_by(Fixture.id)
    ).all()
    weak_team = upcoming[0].team_a
    db.execute(PlayerGameweekStat.__table__.delete())
    for f in finished:
        for team_id, opp_id in ((f.team_h, f.team_a), (f.team_a, f.team_h)):
            for p in (x for x in players if x.team_id == team_id):
                role = role_of[p.element_type]
                if role == "RW":
                    xg = 0.55 if opp_id == weak_team else 0.25
                else:
                    xg = 0.25
                db.add(PlayerGameweekStat(
                    player_id=p.id, gameweek=f.event, fixture_id=f.id,
                    opponent_team=opp_id, was_home=(team_id == f.team_h),
                    minutes=90, expected_goals=xg, expected_assists=0.1,
                ))
    db.commit()
    monkeypatch.setattr(settings, "player_roles_json", path)

    try:
        _clear_goal_odds(db)
        build_projections(db, horizon=1, mc_iterations=300)
        rows = db.scalars(
            select(PlayerFixtureGoals).where(
                PlayerFixtureGoals.gameweek == gw,
                PlayerFixtureGoals.tactical_role == "RW",
                PlayerFixtureGoals.opponent_team == weak_team,
            )
        ).all()
        assert rows, "phải có tiền vệ cánh phải gặp đội yếu cánh trái"
        boosted = [r for r in rows if r.position_factor > 1.0]
        assert boosted, "hệ số phải lớn hơn 1 trước đối thủ lệch cánh"
        for r in boosted:
            # trục đúng: cánh PHẢI tấn công đối đầu cánh TRÁI phòng ngự
            assert r.weakness_axis == roles_mod.AXIS_LEFT_DEF
            assert r.weakness_reliability in ("Thấp", "Trung bình", "Cao")
            assert r.weakness_sample_90s and r.weakness_sample_90s > 0
            # chỉ phần bóng sống được co giãn
            assert r.exp_goals_adjusted == pytest.approx(
                r.exp_goals_open * r.position_factor + r.exp_goals_penalty, abs=1e-5
            )
            # và xP đổi đúng theo công thức
            assert r.xp_delta == pytest.approx(
                (r.exp_goals_final - r.exp_goals_model) * r.goal_points, abs=1e-3
            )
        # thủ môn có vai trò GK: finishing_share = 0 nên hệ số luôn đúng 1.0
        gks = db.scalars(
            select(PlayerFixtureGoals).where(
                PlayerFixtureGoals.gameweek == gw,
                PlayerFixtureGoals.tactical_role == "GK",
            )
        ).all()
        for r in gks:
            assert r.position_factor == pytest.approx(1.0)
    finally:
        db.execute(PlayerGameweekStat.__table__.delete())
        db.commit()
        monkeypatch.setattr(settings, "player_roles_json", "")
        db.expire_all()
        build_projections(db, horizon=2, mc_iterations=300)


def test_refresh_twice_does_not_double_count(db):
    """Chạy lại engine phải ra CÙNG một con số — điều chỉnh không cộng dồn."""
    from app.engine.projections import build_projections, get_planning_start_gw
    from app.models import PlayerProjection

    gw = get_planning_start_gw(db)
    try:
        _seed_goal_odds(db, gw, prob=0.45, condition=gm.COND_ANY)
        build_projections(db, horizon=1, mc_iterations=300)
        first = {
            r.player_id: r.xp
            for r in db.scalars(
                select(PlayerProjection).where(PlayerProjection.gameweek == gw)
            ).all()
        }
        db.expire_all()
        build_projections(db, horizon=1, mc_iterations=300)
        second = {
            r.player_id: r.xp
            for r in db.scalars(
                select(PlayerProjection).where(PlayerProjection.gameweek == gw)
            ).all()
        }
        assert first and first.keys() == second.keys()
        for pid, xp in first.items():
            assert second[pid] == pytest.approx(xp, abs=1e-6)
    finally:
        _clear_goal_odds(db)
        build_projections(db, horizon=2, mc_iterations=300)


def test_unverified_condition_leaves_engine_output_untouched(db):
    """Mặc định (điều kiện chưa xác minh): xP giống y như khi không có kèo."""
    from app.engine.projections import build_projections, get_planning_start_gw
    from app.models import PlayerProjection

    gw = get_planning_start_gw(db)

    def xp_map():
        return {
            r.player_id: r.xp
            for r in db.scalars(
                select(PlayerProjection).where(PlayerProjection.gameweek == gw)
            ).all()
        }

    try:
        _clear_goal_odds(db)
        build_projections(db, horizon=1, mc_iterations=300)
        before = xp_map()

        _seed_goal_odds(db, gw, prob=0.45, condition=gm.COND_UNKNOWN)
        db.expire_all()
        build_projections(db, horizon=1, mc_iterations=300)
        after = xp_map()

        assert before and before.keys() == after.keys()
        for pid, xp in before.items():
            assert after[pid] == pytest.approx(xp, abs=1e-6)
    finally:
        _clear_goal_odds(db)
        build_projections(db, horizon=2, mc_iterations=300)


def test_xp_detail_endpoint_shape_and_blank_gameweek(db, monkeypatch):
    """API chi tiết: vòng đôi cộng từng trận, vòng trắng nói rõ là trắng."""
    from app.config import settings
    from app.engine.projections import get_planning_start_gw
    from app.models import Player
    from app.services.xp_detail import xp_goal_detail

    # Ghim cấu hình thay vì tin vào mặc định: máy của người chạy test có thể có
    # `.env` trỏ tới một tệp vai trò thật, và khi đó test này sẽ hỏng vì môi
    # trường chứ không phải vì code.
    monkeypatch.setattr(settings, "player_roles_json", "")
    gw = get_planning_start_gw(db)
    pid = db.scalars(select(Player.id)).first()
    data = xp_goal_detail(db, pid, gw)
    assert data["gameweek"] == gw
    assert "status" in data
    assert set(data["status"]) >= {"roles", "opponent_weakness", "market"}
    # chưa cấu hình nguồn vai trò -> nói thẳng, và có "needs"
    assert data["status"]["roles"]["configured"] is False
    assert data["status"]["roles"]["needs"]
    # tổng delta = tổng của từng trận
    assert data["xp_delta"] == pytest.approx(
        round(sum(f["xp_delta"] for f in data["fixtures"]), 3)
    )
    if data["xp_after"] is not None:
        assert data["xp_before"] == pytest.approx(
            round(data["xp_after"] - data["xp_delta"], 3), abs=1e-6
        )


def test_double_gameweek_sums_per_fixture(db):
    """Vòng đôi: hai dòng trong `fixtures`, delta là TỔNG của hai trận."""
    from app.engine.projections import build_projections
    from app.models import Fixture, Gameweek, Player, PlayerFixtureGoals
    from app.services.xp_detail import xp_goal_detail

    # DB demo đã có đội đá nhiều trận trong cùng một vòng — dùng luôn, không cần
    # dựng vòng đôi nhân tạo (và nhờ vậy test không phải sửa lịch rồi hoàn tác).
    gw = db.scalar(select(Gameweek.id).where(Gameweek.is_next.is_(True)))
    n_fix_by_team: dict[int, int] = {}
    for f in db.scalars(select(Fixture).where(Fixture.event == gw)).all():
        n_fix_by_team[f.team_h] = n_fix_by_team.get(f.team_h, 0) + 1
        n_fix_by_team[f.team_a] = n_fix_by_team.get(f.team_a, 0) + 1
    multi = [(tid, n) for tid, n in n_fix_by_team.items() if n >= 2]
    assert multi, "DB demo phải có ít nhất một đội đá nhiều trận trong một vòng"
    team_id, n_fixtures = sorted(multi)[0]
    try:
        _seed_goal_odds(db, gw, prob=0.40, condition=gm.COND_ANY)
        build_projections(db, horizon=1, mc_iterations=300)
        pid = db.scalars(select(Player.id).where(Player.team_id == team_id)).first()
        rows = db.scalars(
            select(PlayerFixtureGoals).where(
                PlayerFixtureGoals.player_id == pid,
                PlayerFixtureGoals.gameweek == gw,
            )
        ).all()
        # một bản ghi cho MỖI trận, và delta là tổng của chúng
        assert len(rows) == n_fixtures
        assert len({r.fixture_id for r in rows}) == n_fixtures
        data = xp_goal_detail(db, pid, gw)
        assert data["is_double"] is True
        assert len(data["fixtures"]) == n_fixtures
        assert data["xp_delta"] == pytest.approx(
            round(sum(r.xp_delta for r in rows), 3), abs=1e-3
        )
    finally:
        _clear_goal_odds(db)
        db.expire_all()
        build_projections(db, horizon=2, mc_iterations=300)


def test_blank_gameweek_has_no_fixtures_and_zero_delta(db):
    """Vòng trắng: không có bản ghi theo trận, delta 0, và nói rõ là trắng."""
    from app.engine.projections import build_projections
    from app.models import Fixture, Gameweek, Player, PlayerFixtureGoals
    from app.services.xp_detail import xp_goal_detail

    gw = db.scalar(select(Gameweek.id).where(Gameweek.is_next.is_(True)))
    playing: set[int] = set()
    for f in db.scalars(select(Fixture).where(Fixture.event == gw)).all():
        playing.add(f.team_h)
        playing.add(f.team_a)
    blank_team = db.scalars(
        select(Player.team_id).where(Player.team_id.notin_(playing or {-1}))
    ).first()
    if blank_team is None:
        pytest.skip("DB demo không có đội nào nghỉ ở vòng này")
    try:
        _seed_goal_odds(db, gw, prob=0.40, condition=gm.COND_ANY)
        build_projections(db, horizon=1, mc_iterations=300)
        pid = db.scalars(select(Player.id).where(Player.team_id == blank_team)).first()
        rows = db.scalars(
            select(PlayerFixtureGoals).where(
                PlayerFixtureGoals.player_id == pid,
                PlayerFixtureGoals.gameweek == gw,
            )
        ).all()
        assert rows == []
        data = xp_goal_detail(db, pid, gw)
        assert data["is_blank"] is True
        assert data["xp_delta"] == 0
        assert data["fixtures"] == []
    finally:
        _clear_goal_odds(db)
        db.expire_all()
        build_projections(db, horizon=2, mc_iterations=300)

"""Thay **đúng thành phần ghi bàn** của xP trong MỘT trận, không chạm gì khác.

Công thức duy nhất được phép dùng::

    xP_mới = xP_cũ + (bàn_kỳ_vọng_mới − bàn_kỳ_vọng_cũ) × điểm_mỗi_bàn

`điểm_mỗi_bàn` đọc từ `scoring.RULES.goal_points` — tức từ `game_config` mà FPL
phát ra, không ghi cứng (thủ môn ghi bàn 10 điểm ở mùa này, và con số đó đã từng bị
ghi cứng sai thành 6).

Ba điều module này **cố ý không làm**:

1. **Không nhân toàn bộ xP với hệ số vị trí.** Hệ số chỉ tác động lên phần bàn
   thắng bóng sống; kiến tạo, sạch lưới, bonus, defcon đứng nguyên.
2. **Không áp hệ số bóng sống lên penalty.** Một quả 11m đáng 0.79 bàn bất kể hàng
   phòng ngự đối thủ lệch về đâu — cùng lý do `engine/penalties.py` tách hai phần.
3. **Không cộng điểm vì "đối thủ yếu" hay vì thị trường "thích" cầu thủ này.** Mọi
   thay đổi đi qua đúng một kênh: số bàn kỳ vọng. Nếu số bàn không đổi thì xP không
   đổi, kể cả khi hệ số và kèo đều có mặt.

Hàm chính là **thuần** (không DB, không mạng) để test được từng trường hợp biên:
thiếu kèo, kèo lỗi, cầu thủ không thi đấu, vai trò không rõ, vòng trắng, vòng đôi.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.engine import goal_market as gm
from app.engine import opponent_profile as op_mod
from app.engine import roles as roles_mod
from app.engine.xmins import E_MIN_START, E_MIN_SUB


@dataclass
class GoalAdjustment:
    """Mọi thứ cần để ghi DB và để giải thích trên giao diện."""

    # số bàn kỳ vọng
    exp_goals_model: float
    exp_goals_open: float
    exp_goals_penalty: float
    exp_goals_adjusted: float
    exp_goals_final: float

    # điểm
    goal_points: int
    xp_delta: float

    # phút theo kịch bản
    minutes_if_start: float | None
    minutes_if_sub: float | None

    # vai trò & điểm yếu
    tactical_role: str | None
    role_source: str | None
    role_confidence: float | None
    weakness_axis: str | None
    axis_factor: float | None
    position_factor: float
    finishing_share: float | None
    weakness_sample_90s: float | None
    weakness_reliability: str | None
    weakness_note: str

    # thị trường
    market_prob: float | None
    market_exp_goals: float | None
    market_r90: float | None
    market_condition: str | None
    market_usable: bool | None
    market_weight: float | None
    market_source: str | None
    market_n_bookmakers: int | None
    market_updated_at: datetime | None
    market_note: str | None

    @property
    def changed(self) -> bool:
        return abs(self.xp_delta) > 1e-9


def scenario_minutes(
    xmins: float, p_start: float, p_sub: float
) -> tuple[float, float]:
    """(phút nếu đá chính, phút nếu vào từ ghế) khớp với `xmins` đã cho.

    Giữ đúng TỶ LỆ 84/20 của mô hình phút rồi co giãn để
    `p_start·m_chính + p_ghế·m_ghế == xmins`. Làm vậy vì `apply_fatigue` ở
    `projections.py` nhân trực tiếp vào `xmins`, nên lấy thẳng 84/20 sẽ mâu thuẫn
    với chính con số phút mà phần còn lại của engine đang dùng — và kèo thì được
    khớp qua hai kịch bản này.
    """
    base = p_start * E_MIN_START + p_sub * E_MIN_SUB
    if base <= 1e-9 or xmins <= 0:
        return E_MIN_START, E_MIN_SUB
    scale = xmins / base
    return min(90.0, E_MIN_START * scale), min(90.0, E_MIN_SUB * scale)


def adjust_fixture_goals(
    *,
    goal_points: int,
    exp_goals_model: float,
    exp_goals_penalty: float,
    p_start: float,
    p_sub: float,
    xmins: float,
    assignment: roles_mod.RoleAssignment = roles_mod.UNKNOWN_ROLE,
    weakness: op_mod.WeaknessTable | None = None,
    opponent_team_id: int | None = None,
    positional_enabled: bool = True,
    market_prob: float | None = None,
    market_devigged: bool = True,
    market_condition: str = gm.COND_UNKNOWN,
    market_weight: float = 0.0,
    market_source: str | None = None,
    market_n_bookmakers: int | None = None,
    market_updated_at: datetime | None = None,
    market_blocked_reason: str | None = None,
) -> GoalAdjustment:
    """Tính số bàn kỳ vọng mới và phần xP chênh lệch cho MỘT trận.

    `exp_goals_model` và `exp_goals_penalty` lấy từ `XPBreakdown.components`
    (`exp_goals` và `exp_pen_goals`), cả hai đã nhân `team_goal_scale`, nên phần
    bóng sống là hiệu của hai số — không tính lại từ đầu, để hai chỗ không thể lệch.
    """
    exp_goals_model = max(0.0, exp_goals_model)
    exp_pen = max(0.0, min(exp_goals_penalty, exp_goals_model))
    exp_open = max(0.0, exp_goals_model - exp_pen)

    # ---- 1. điểm yếu đối thủ theo trục vị trí (chỉ lên phần bóng sống) --------
    adj = op_mod.NO_ADJUSTMENT
    if positional_enabled and weakness is not None and opponent_team_id is not None:
        adj = op_mod.positional_adjustment(
            assignment=assignment, table=weakness, opponent_team_id=opponent_team_id
        )
    factor = adj.factor if positional_enabled else 1.0
    exp_goals_adjusted = exp_open * factor + exp_pen

    # ---- 2. thị trường --------------------------------------------------------
    m_start, m_sub = scenario_minutes(xmins, p_start, p_sub)
    market: gm.MarketGoals | None = None
    if market_prob is not None:
        market = gm.market_expected_goals(
            p_market=market_prob,
            p_start=p_start,
            p_sub=p_sub,
            minutes_if_start=m_start,
            minutes_if_sub=m_sub,
            condition=market_condition,
            devigged=market_devigged,
            blocked_reason=market_blocked_reason,
        )

    use_market = bool(market and market.usable_for_xp and market.expected_goals is not None)
    exp_goals_final = (
        gm.blend(exp_goals_adjusted, market.expected_goals, market_weight)
        if use_market
        else exp_goals_adjusted
    )

    xp_delta = (exp_goals_final - exp_goals_model) * goal_points

    return GoalAdjustment(
        exp_goals_model=exp_goals_model,
        exp_goals_open=exp_open,
        exp_goals_penalty=exp_pen,
        exp_goals_adjusted=exp_goals_adjusted,
        exp_goals_final=exp_goals_final,
        goal_points=goal_points,
        xp_delta=xp_delta,
        minutes_if_start=m_start,
        minutes_if_sub=m_sub,
        tactical_role=assignment.role,
        role_source=assignment.source,
        role_confidence=assignment.confidence,
        weakness_axis=adj.axis,
        axis_factor=adj.axis_factor,
        position_factor=factor,
        finishing_share=adj.finishing_share if adj.role else None,
        weakness_sample_90s=adj.n_90s if adj.has_data else None,
        weakness_reliability=adj.reliability,
        weakness_note=(
            adj.reason
            if positional_enabled
            else "Điều chỉnh theo vị trí đang TẮT (POSITIONAL_ADJUSTMENT_ENABLED=false)"
        ),
        market_prob=market_prob,
        market_exp_goals=None if market is None else market.expected_goals,
        market_r90=None if market is None else market.r90,
        market_condition=None if market is None else market.condition,
        market_usable=None if market is None else market.usable_for_xp,
        market_weight=market_weight if use_market else (0.0 if market else None),
        market_source=market_source,
        market_n_bookmakers=market_n_bookmakers,
        market_updated_at=market_updated_at,
        market_note=None if market is None else market.reason,
    )

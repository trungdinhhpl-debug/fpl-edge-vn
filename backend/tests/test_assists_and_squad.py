"""Kiến tạo theo thang FPL, và cầu thủ chấn thương không được lọt vào sức mạnh đội.

Bối cảnh (2026-10-20): Brighton ghi 23 bàn, thủng 5 sau 7 trận mà cầu thủ của họ bị
chấm thấp. Hai lỗi đo được:

* FPL trao ~0.92 kiến tạo mỗi bàn, còn xA chỉ ~0.63 mỗi xG (4 mùa liên tiếp), nên
  engine cộng thiếu ~30% điểm kiến tạo cho MỌI cầu thủ — nặng nhất ở người sáng tạo.
* Những cầu thủ đắt nhưng chấn thương từ đầu mùa (0 phút) vẫn được tính vào đội hình
  tham chiếu và vào "chất lượng đội hình".
"""
from types import SimpleNamespace

import pytest

from app.engine import xpoints
from app.engine.prior_strength import _squad_quality
from app.engine.xpoints import expected_points

_MID = dict(
    element_type=3, minutes_season=600, xg_season=1.0, xa_season=1.0,
    saves_season=0, dc_season=40.0, yellow_season=1, red_season=0, bps_season=110,
    cbi_season=10.0, xmins=85.0, p_start=0.98, p_appear=0.99, p_60_plus=0.93,
    lam_team_goals=1.5, lam_conceded=1.3, team_avg_gf=1.5, penalties_order=None,
    prior_xa90=0.15,
)


def test_defaults_keep_the_old_assist_value_exactly():
    """Không truyền gì mới -> đúng như trước, từng chữ số (lời gọi lẻ, test cũ)."""
    a = expected_points(**_MID)
    b = expected_points(**_MID, assist_xa_ratio=1.0, assist_actual_weight=0.0,
                        assists_season=9, team_assist_scale=None)
    assert a.assists == b.assists and a.xp == b.xp


def test_xa_is_converted_to_the_fpl_assist_scale():
    base = expected_points(**_MID)
    conv = expected_points(**_MID, assist_xa_ratio=1.4)
    assert conv.components["exp_assists"] == pytest.approx(
        base.components["exp_assists"] * 1.4, rel=1e-3
    )


def test_players_who_actually_assist_more_get_more_assist_value():
    """Hai người cùng xA: người có nhiều kiến tạo thật hơn được nhiều hơn — vừa phải."""
    kw = dict(assist_xa_ratio=1.4, assist_actual_weight=xpoints.ASSIST_ACTUAL_WEIGHT)
    hot = expected_points(**_MID, assists_season=6, **kw)
    cold = expected_points(**_MID, assists_season=0, **kw)
    assert hot.assists > cold.assists
    # chỉ 20% trọng số cho kiến tạo thật: 6 kiến tạo từ 1.0 xA không được nhân 6 lần
    assert hot.assists < 2.0 * cold.assists


def test_team_assist_scale_overrides_the_goal_scale_for_assists_only():
    a = expected_points(**_MID, team_goal_scale=1.0, team_assist_scale=1.5)
    b = expected_points(**_MID, team_goal_scale=1.0)
    assert a.components["exp_assists"] == pytest.approx(
        b.components["exp_assists"] * 1.5, rel=1e-3
    )
    assert a.goals == b.goals


def test_measured_assist_weight_stays_in_the_backtested_band():
    """0.1–0.3 đều tốt hơn hẳn 0 trong backtest; ngoài dải đó là đổi không bằng chứng."""
    assert 0.1 <= xpoints.ASSIST_ACTUAL_WEIGHT <= 0.3
    assert 1.3 <= xpoints.FALLBACK_ASSIST_XA_RATIO <= 1.5
    assert 0.85 <= xpoints.FALLBACK_ASSISTS_PER_GOAL <= 1.0


# ===================================================== chất lượng đội hình ====
def _pl(pid, tid, etype, cost, mins, status="a"):
    return SimpleNamespace(
        id=pid, team_id=tid, element_type=etype, now_cost=cost, minutes=mins,
        status=status, expected_goals=mins / 900.0, expected_goals_conceded=mins / 90.0,
    )


def _team(tid, base_cost=55):
    out = [_pl(tid * 100, tid, 1, base_cost - 10, 900)]
    out += [_pl(tid * 100 + i, tid, 2, base_cost - 5, 900) for i in range(1, 6)]
    out += [_pl(tid * 100 + 5 + i, tid, 3, base_cost, 900) for i in range(1, 6)]
    out += [_pl(tid * 100 + 10 + i, tid, 4, base_cost + 5, 900) for i in range(1, 4)]
    return out


def test_injured_stars_who_never_played_do_not_inflate_squad_quality():
    teams = [1, 2, 3]
    others = _team(2, 60) + _team(3, 50)
    plain, _ = _squad_quality(_team(1) + others, teams)
    stars = [_pl(190 + i, 1, 3, 95, 0, status="i") for i in range(3)]
    with_stars, _ = _squad_quality(_team(1) + stars + others, teams)
    assert with_stars[1] == pytest.approx(plain[1])


def test_preseason_squad_quality_still_ranks_by_price():
    """Chưa ai có phút thì giá là bằng chứng duy nhất — giữ hành vi tiền mùa."""
    teams = [1, 2]
    t1 = [_pl(p.id, 1, p.element_type, p.now_cost, 0) for p in _team(1)]
    t2 = [_pl(p.id, 2, p.element_type, p.now_cost, 0) for p in _team(2, 70)]
    q, _ = _squad_quality(t1 + t2, teams)
    assert q[2][0] > q[1][0], "đội đắt hơn phải ra chỉ số công cao hơn"

"""Hai đường tính (giải tích và Monte Carlo) phải nói cùng một điều.

Chúng mô hình cùng một đại lượng nên trung bình phải khớp; lệch ở thành phần nào là
lỗi ở đúng thành phần đó. Đo được trước khi sửa (GW1, xMins ≥ 20): thủ môn 88%,
hậu vệ 107%, tiền vệ 92%, **tiền đạo 79%** so với giải tích.

Bốn nguyên nhân, và điều đáng nói là **hai bên sai ở hai chỗ khác nhau**:

  * cứu thua và bàn thua — GIẢI TÍCH sai: FPL đếm theo mốc trọn (1 điểm mỗi 3 lần
    cứu, −1 mỗi 2 bàn), còn `λ/k` cho hai lần cứu thua thành 0.67 điểm;
  * điểm ra sân và tần suất bonus — MONTE CARLO sai: nó trao 2 điểm cho mọi người
    đá chính (luật đòi đủ 60 phút), và tần suất bonus của nó dựa trên giả định trung
    bình rút là 2 trong khi thực tế nhỏ hơn;
  * bảo toàn tổng bàn — GIẢI TÍCH sai: tổng bàn kỳ vọng của cả đội không khớp λ của
    mô hình sức mạnh đội (Chelsea 162%, Fulham 57%);
  * mẫu số của share — MONTE CARLO sai: gồm cả cầu thủ không được mô phỏng, nên tới
    21% xG của một đội biến thành bàn thắng thất lạc.
"""
import math

import numpy as np
import pytest

from app.engine.montecarlo import MCPlayer, simulate_fixture
from app.engine.xpoints import _expected_floor_div, expected_points
from app.scoring import RULES


# ------------------------------------------------------- mốc trọn, không tỷ lệ ---
def test_expected_floor_div_matches_brute_force():
    """E[floor(X/k)] tính bằng tổng đuôi phải khớp phép tính trực tiếp."""
    for lam in (0.3, 1.0, 2.5, 4.0, 7.0):
        for k in (2, 3):
            direct = sum(
                math.floor(i / k) * math.exp(-lam) * lam ** i / math.factorial(i)
                for i in range(0, 60)
            )
            assert _expected_floor_div(lam, k) == pytest.approx(direct, abs=1e-6)


def test_expected_floor_div_is_below_the_naive_ratio():
    """Đây là cả lý do phải có hàm này: `λ/k` luôn cao hơn thực tế."""
    for lam in (0.5, 1.0, 2.0, 3.0):
        assert _expected_floor_div(lam, 3) < lam / 3
        assert _expected_floor_div(lam, 2) < lam / 2
    # hai lần cứu thua kỳ vọng: luật cho 0 điểm, không phải 0.67
    assert _expected_floor_div(2.0, 3) < 0.5
    assert _expected_floor_div(0.0, 3) == 0.0


def test_analytic_saves_use_whole_thresholds():
    """Thủ môn: điểm cứu thua phải thấp hơn `số lần cứu / 3`."""
    common = dict(
        element_type=1, minutes_season=3000, xg_season=0.0, xa_season=0.0,
        saves_season=100, dc_season=0, yellow_season=1, red_season=0,
        bps_season=600, penalties_order=None, xmins=90, p_start=1.0,
        p_appear=1.0, p_60_plus=1.0, lam_team_goals=1.4, lam_conceded=1.4,
        team_avg_gf=1.4,
    )
    bd = expected_points(**common)
    saves90 = 100 / (3000 / 90)
    naive = saves90 / RULES.saves_per_point
    assert 0 < bd.saves < naive


def test_analytic_conceded_penalty_uses_whole_thresholds():
    """Hậu vệ: một bàn thua kỳ vọng KHÔNG được trừ 0.5 điểm."""
    common = dict(
        element_type=2, minutes_season=3000, xg_season=1.0, xa_season=1.0,
        saves_season=0, dc_season=300, yellow_season=4, red_season=0,
        bps_season=500, penalties_order=None, xmins=90, p_start=1.0,
        p_appear=1.0, p_60_plus=1.0, lam_team_goals=1.2, lam_conceded=1.0,
        team_avg_gf=1.2,
    )
    bd = expected_points(**common)
    # negative gồm cả thẻ; phần thủng lưới phải nhỏ hơn 0.5 điểm về độ lớn
    assert bd.negative > -0.5


# ----------------------------------------------------- bảo toàn tổng bàn ---------
def test_team_goal_scale_moves_expected_goals_proportionally():
    common = dict(
        element_type=4, minutes_season=2700, xg_season=18.0, xa_season=4.0,
        saves_season=0, dc_season=60, yellow_season=3, red_season=0,
        bps_season=700, penalties_order=None, xmins=85, p_start=0.9,
        p_appear=0.95, p_60_plus=0.85, lam_team_goals=1.8, lam_conceded=1.2,
        team_avg_gf=1.8,
    )
    base = expected_points(**common)
    halved = expected_points(**common, team_goal_scale=0.5)
    assert halved.goals == pytest.approx(base.goals * 0.5, rel=0.02)
    assert halved.assists == pytest.approx(base.assists * 0.5, rel=0.02)
    # mặc định phải là không chuẩn hoá, để mọi chỗ gọi lẻ giữ nguyên hành vi
    assert expected_points(**common, team_goal_scale=1.0).goals == base.goals


# ------------------------------------------------------- Monte Carlo đúng luật ---
def _players(n_players: int = 11, **over) -> list[MCPlayer]:
    base = dict(
        element_type=3, p_start=0.9, p_sub=0.05, p_60_plus=0.6,
        share_goal=1.0 / n_players, share_assist=1.0 / n_players,
        saves90=0.0, dc_hit_prob=0.0, yellow90=0.0, bonus_base=0.0,
    )
    base.update(over)
    return [MCPlayer(player_id=i, **base) for i in range(n_players)]


def test_mc_awards_two_points_only_for_sixty_minutes():
    """Người đá chính rồi bị thay ra phút 55 được 1 điểm, không phải 2.

    Bản trước trao 2 điểm cho mọi người đá chính. Với p_start 0.9 và p_60_plus 0.6
    thì 30% số lần là đá chính mà không đủ 60 phút — mỗi lần đội lên 1 điểm.
    """
    rng = np.random.default_rng(3)
    col: dict = {}
    simulate_fixture(_players(p_start=0.9, p_sub=0.0, p_60_plus=0.6),
                     1.4, 1.2, 20_000, rng, collect=col)
    app = np.mean([c["appearance"] for c in col.values()])
    expected = 0.6 * RULES.points_play_60_plus + 0.3 * RULES.points_play_under_60
    assert app == pytest.approx(expected, abs=0.05)


def test_mc_bonus_mean_matches_the_analytic_expectation():
    """Tần suất bonus phải khớp kỳ vọng giải tích, cả khi phải HẠ và khi phải NÂNG.

    Bản trước dùng `bonus_base / 2` (giả định trung bình rút là 2) rồi chặn ở 1.0.
    Giả định đó sai vì mảng rút chỉ là 1..3 khi cầu thủ có bàn/kiến tạo; và cái chặn
    khiến cầu thủ kỳ vọng bonus cao không bao giờ đạt tới — đo được tiền đạo đầu
    bảng chỉ đạt 83% mức giải tích.
    """
    for target in (0.15, 0.6, 1.6, 2.5):
        rng = np.random.default_rng(11)
        col: dict = {}
        simulate_fixture(
            _players(bonus_base=target, share_goal=0.09, share_assist=0.09),
            1.6, 1.2, 30_000, rng, collect=col,
        )
        got = np.mean([c["bonus"] for c in col.values()])
        assert got == pytest.approx(target, abs=0.06), f"target {target} -> {got:.3f}"


def test_mc_bonus_never_exceeds_the_rule_cap():
    rng = np.random.default_rng(5)
    col: dict = {}
    simulate_fixture(_players(bonus_base=99.0), 1.6, 1.2, 5_000, rng, collect=col)
    assert all(c["bonus"] <= RULES.max_bonus + 1e-6 for c in col.values())


def test_mc_clean_sheet_requires_sixty_minutes():
    """Sạch lưới cần đủ 60 phút — cùng điều kiện với phần giải tích."""
    rng = np.random.default_rng(9)
    col: dict = {}
    simulate_fixture(
        _players(element_type=2, p_start=1.0, p_sub=0.0, p_60_plus=0.5),
        1.2, 0.0001, 20_000, rng, collect=col,
    )
    cs = np.mean([c["clean_sheet"] for c in col.values()])
    # gần như chắc chắn sạch lưới, nhưng chỉ nửa số lần đủ 60 phút
    assert cs == pytest.approx(0.5 * RULES.clean_sheet_points[2], abs=0.1)


def test_mc_allocates_the_whole_team_goal_total():
    """Tổng bàn chia ra không được hụt so với tổng bàn của đội.

    Mẫu số của share phải là nhóm ĐƯỢC MÔ PHỎNG. Nếu gồm cả người không mô phỏng
    thì các share cộng lại nhỏ hơn 1 và phần thiếu thành bàn thắng thất lạc.
    """
    rng = np.random.default_rng(2)
    lam = 2.0
    players = _players(11, share_goal=1.0 / 11, p_start=1.0, p_sub=0.0, p_60_plus=1.0)
    col: dict = {}
    simulate_fixture(players, lam, 1.0, 30_000, rng, collect=col)
    goal_points = sum(c["goals"] for c in col.values())
    goals = goal_points / RULES.goal_points[3]
    assert goals == pytest.approx(lam, rel=0.06), f"chia được {goals:.3f} / {lam}"

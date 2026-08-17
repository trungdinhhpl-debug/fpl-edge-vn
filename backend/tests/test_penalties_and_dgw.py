"""Hai giới hạn từng bỏ ngỏ: tách chấm 11m, và xoay tua trong vòng đôi.

Cả hai đều là chỗ dễ "sửa" bằng một hằng số bịa ra rồi tự tin là đã xong, nên test
ở đây khoá lại chính những tính chất chứng minh phép sửa là đúng chứ không chỉ là
khác đi: biên duyên phải giữ nguyên, kỳ vọng không được dịch, và người không liên
quan không được đụng tới.
"""
import numpy as np
import pytest

from app.engine import penalties as pen
from app.engine.montecarlo import MCPlayer, rotation_start_prob, simulate_fixture
from app.engine.projections import apply_fatigue, fatigue_factor
from app.engine.xpoints import expected_points


class _P:
    """Cầu thủ tối giản, chỉ đủ trường cho `league_penalty_rate`."""

    def __init__(self, missed):
        self.penalties_missed = missed


# ============================================ tỷ lệ penalty của giải =========
def test_penalty_rate_is_measured_from_league_totals():
    """Tỷ lệ suy từ TỔNG số quả hỏng cả giải, không phải từ từng cầu thủ.

    Đây là toàn bộ lý do phép tách này đứng được: 14 quả hỏng trên 760 trận-đội là
    một mẫu dùng được, còn 0–2 quả cho mỗi cầu thủ thì không.
    """
    r = pen.league_penalty_rate([_P(1)] * 14, team_matches=760)
    assert r.measured
    # 14 hỏng / 0.21 = 67 quả đã đá; × 0.79 = 53 bàn; / 760 = 0.069
    assert r.goals_per_team_match == pytest.approx(0.0693, abs=0.002)
    assert "14 quả hỏng" in r.detail


def test_penalty_rate_falls_back_when_sample_is_thin():
    """Vài quả hỏng không đủ để đo — dùng mức nền và KHAI rõ là mức nền."""
    r = pen.league_penalty_rate([_P(1)] * 2, team_matches=40)
    assert not r.measured
    assert r.goals_per_team_match == pen.FALLBACK_PENALTY_GOALS_PER_TEAM_MATCH
    assert "chưa đủ" in r.detail


def test_second_taker_only_gets_penalties_when_the_first_is_absent():
    """Ai đá suy từ mô hình phút, không từ một tỷ lệ chia ghi cứng."""
    r = 0.069
    lead = pen.penalty_xg90(rate_per_team_match=r, penalties_order=1)
    blocked = pen.penalty_xg90(rate_per_team_match=r, penalties_order=2,
                               lead_taker_on_pitch=0.95)
    free = pen.penalty_xg90(rate_per_team_match=r, penalties_order=2,
                            lead_taker_on_pitch=0.30)
    assert lead > free > blocked > 0
    # người không nằm trong danh sách đá 11m: đúng bằng 0, không phải "một chút"
    assert pen.penalty_xg90(rate_per_team_match=r, penalties_order=None) == 0.0
    assert pen.penalty_xg90(rate_per_team_match=r, penalties_order=4) == 0.0


def test_penalties_respond_less_to_fixture_difficulty_than_open_play():
    """Một quả 11m đáng 0.79 bàn dù đối thủ là ai — nó không co như bóng sống."""
    easy = pen.fixture_scaled_penalty(0.069, 0.9, 1.8)
    hard = pen.fixture_scaled_penalty(0.069, 0.9, 0.5)
    assert easy > hard, "phải vẫn phản ứng chút ít — đội áp đảo được hưởng nhiều 11m hơn"
    # nhưng phản ứng YẾU hơn hẳn bóng sống, vốn co giãn đúng theo fixture_adj
    assert (easy / hard) < (1.8 / 0.5), "đang co giãn mạnh như bóng sống"


def test_low_xg_penalty_taker_is_not_inflated_out_of_thin_air():
    """Người đá 11m mà xG thấp KHÔNG được phồng lên.

    Regression đo được: bản đầu chặn phần trừ ở 45% xG, nên Robinson (Fulham, hậu
    vệ đá 11m, xg90 = 0.065) chỉ bị trừ 0.029 nhưng vẫn được cộng lại 0.069 —
    **phồng 62% mối đe doạ ghi bàn từ không khí**, và đẩy anh ta lên trong bài toán
    tối ưu. Chặn ở chính xg90 thì phần cộng vào không bao giờ vượt phần trừ đi.
    """
    open_play, pen_part = pen.split_open_play(0.065, 0.069)
    assert open_play >= 0.0, "xG bóng sống không được âm"
    assert pen_part <= 0.065, "trừ nhiều hơn cả sản lượng anh ta có"
    assert open_play + pen_part == pytest.approx(0.065, abs=1e-12), "tổng phải bảo toàn"
    # người xG cao thì phần 11m chỉ là một lát nhỏ
    hi_open, hi_pen = pen.split_open_play(0.78, 0.069)
    assert hi_pen == pytest.approx(0.069)
    assert hi_open + hi_pen == pytest.approx(0.78, abs=1e-12)
    # không đá 11m -> không tách gì
    assert pen.split_open_play(0.5, 0.0) == (0.5, 0.0)


# ================================================ tác động lên xP ============
_BASE = dict(
    element_type=4, minutes_season=2900, xg_season=24.0, xa_season=5.0,
    saves_season=0, dc_season=90.0, yellow_season=4, red_season=0, bps_season=700,
    cbi_season=40.0, xmins=80.0, p_start=0.92, p_appear=0.95, p_60_plus=0.88,
    lam_conceded=1.2, team_avg_gf=1.42,
)


def test_non_taker_is_completely_unaffected_by_the_penalty_split():
    """Phép tách chỉ được đụng tới người đá 11m. Ai khác đổi là lỗi."""
    for lam in (0.85, 1.42, 2.3):
        off = expected_points(penalties_order=None, penalty_rate=0.0,
                              lam_team_goals=lam, **_BASE)
        on = expected_points(penalties_order=None, penalty_rate=0.069,
                             lam_team_goals=lam, **_BASE)
        assert on.goals == pytest.approx(off.goals, abs=1e-9)
        assert on.xp == pytest.approx(off.xp, abs=1e-9)


def test_penalty_taker_keeps_more_value_in_a_hard_fixture():
    """Tính chất cốt lõi: phần 11m giữ được giá trị khi lịch xấu.

    Đo bằng tỷ số dễ/khó — nếu tách đúng thì tỷ số đó phải NHỎ hơn của người không
    đá 11m, vì một phần sản lượng của anh ta không co theo đối thủ.
    """
    def ratio(order, rate):
        easy = expected_points(penalties_order=order, penalty_rate=rate,
                               lam_team_goals=2.3, **_BASE).goals
        hard = expected_points(penalties_order=order, penalty_rate=rate,
                               lam_team_goals=0.85, **_BASE).goals
        return easy / hard

    assert ratio(1, 0.069) < ratio(None, 0.069)


def test_penalty_component_is_reported_separately():
    bd = expected_points(penalties_order=1, penalty_rate=0.069,
                         lam_team_goals=1.6, **_BASE)
    assert bd.components["exp_pen_goals"] > 0
    assert bd.components["xg90_open"] < bd.components["xg90"]
    assert bd.components["exp_pen_goals"] < bd.components["exp_goals"]


# ============================================== xoay tua vòng đôi ============
def test_rotation_preserves_the_marginal_exactly():
    """Dù xoay tua mạnh đến đâu, xác suất đá chính cộng lại không đổi.

    Đây là điều khiến phép sửa này an toàn: nó **không** đụng vào xP, chỉ đụng vào
    phương sai. Một cách sửa làm dịch cả kỳ vọng sẽ âm thầm đổi mọi khuyến nghị.
    """
    for p_prev in (0.2, 0.5, 0.8, 0.95):
        for p_now in (0.2, 0.5, 0.8, 0.95):
            for rho in (-0.9, -0.5, -0.25, 0.0):
                hi, lo = rotation_start_prob(p_prev, p_now, rho)
                assert 0.0 <= hi <= 1.0 and 0.0 <= lo <= 1.0
                assert p_prev * hi + (1 - p_prev) * lo == pytest.approx(p_now, abs=1e-12)


def test_nailed_players_cannot_be_rotated_much():
    """Giới hạn khả thi là tính năng: không có chỗ nào để xoay một trụ cột 95%."""
    gap_nailed = np.subtract(*rotation_start_prob(0.95, 0.95, -0.25))
    gap_rotated = np.subtract(*rotation_start_prob(0.60, 0.60, -0.25))
    assert abs(gap_nailed) < 0.10, "trụ cột chắc suất vẫn bị xoay mạnh"
    assert abs(gap_rotated) > 3 * abs(gap_nailed)
    # rho = 0 phải là phép đồng nhất
    assert rotation_start_prob(0.7, 0.7, 0.0) == (0.7, 0.7)


def _mc_player(p_start, p60):
    return MCPlayer(
        player_id=1, element_type=3, p_start=p_start, p_sub=0.05, p_60_plus=p60,
        share_goal=0.18, share_assist=0.15, saves90=0.0, dc_hit_prob=0.25,
        yellow90=0.15, bonus_base=0.5,
    )


def test_double_gameweek_rotation_narrows_the_distribution_without_moving_the_mean():
    """Bản trước rút hai trận độc lập (tương quan +0.0003) nên thổi phồng cả trần
    lẫn sàn của cầu thủ vòng đôi — đúng hai con số mà Bench Boost và Triple Captain
    dựa vào."""
    pl = [_mc_player(0.60, 0.50)]
    n = 60_000
    rng = np.random.default_rng(11)
    first_mask: dict[int, np.ndarray] = {}
    first = simulate_fixture(pl, 1.6, 1.2, n, rng, out_started=first_mask)
    independent = first[1] + simulate_fixture(pl, 1.6, 1.2, n, rng)[1]
    rotated = first[1] + simulate_fixture(
        pl, 1.6, 1.2, n, rng, prior_started=first_mask, prior_p_start={1: 0.60}
    )[1]

    assert rotated.mean() == pytest.approx(independent.mean(), rel=0.02)
    assert rotated.std() < independent.std(), "xoay tua phải THU HẸP phân phối"
    assert rotated.std() > 0.9 * independent.std(), "thu hẹp quá đà"


def test_rotation_is_off_without_a_prior_fixture():
    """Vòng đơn phải chạy y hệt như trước — không có trận trước thì không xoay gì."""
    pl = [_mc_player(0.7, 0.6)]
    a = simulate_fixture(pl, 1.5, 1.1, 5000, np.random.default_rng(3))
    b = simulate_fixture(pl, 1.5, 1.1, 5000, np.random.default_rng(3), prior_started=None)
    assert np.array_equal(a[1], b[1])


# ==================================================== mệt mỏi ================
def test_fatigue_scales_with_the_gap_between_the_two_matches():
    assert fatigue_factor(None) == 1.0, "không biết giờ đá thì không được phạt"
    assert fatigue_factor(7.0) == 1.0
    assert fatigue_factor(4.0) == 1.0
    assert fatigue_factor(3.0) < 1.0
    assert fatigue_factor(2.0) < fatigue_factor(3.0)
    assert fatigue_factor(0.0) >= fatigue_factor.__globals__["FATIGUE_FLOOR"]


def test_fatigue_moves_minutes_into_the_bench_not_into_thin_air():
    """Người bị rút sớm hiếm khi vắng mặt hẳn — phút mất đi phải đi đâu đó."""
    from app.engine.xmins import estimate_minutes

    est = estimate_minutes(
        element_type=3, status="a", chance_of_playing=None, season_starts=30,
        season_minutes=2700, team_matches_played=30,
    )
    tired = apply_fatigue(est, 0.9)
    assert tired.xmins < est.xmins
    assert tired.p_start < est.p_start
    assert tired.p_sub > est.p_sub, "suất đá chính mất đi phải chuyển một phần sang ghế"
    assert tired.p_start + tired.p_sub + tired.p_no_play == pytest.approx(1.0, abs=1e-9)
    assert apply_fatigue(est, 1.0) is est, "hệ số 1.0 phải là phép đồng nhất"


def test_double_gameweek_fixtures_are_ordered_by_kickoff():
    """"Trận thứ hai" chỉ có nghĩa khi biết trận nào đá trước."""
    from datetime import datetime, timedelta, timezone

    from app.engine.projections import _fixtures_by_gw

    t0 = datetime(2026, 12, 20, 15, tzinfo=timezone.utc)

    class F:
        def __init__(self, i, h, a, ko):
            self.id, self.team_h, self.team_a, self.kickoff_time = i, h, a, ko
            self.event = 18

    later, earlier = F(2, 1, 3, t0 + timedelta(days=3)), F(1, 1, 2, t0)
    out = _fixtures_by_gw([later, earlier])          # cố ý đưa vào sai thứ tự
    assert [f[2] for f in out[18][1]] == [1, 2]

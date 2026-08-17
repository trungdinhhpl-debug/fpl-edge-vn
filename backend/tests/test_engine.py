"""Projection engine unit tests (xMins, xP, Poisson helpers)."""
import math

import pytest

from app.engine.xmins import estimate_minutes
from app.engine.xpoints import _poisson_ge_k, expected_points


def test_poisson_ge_k_bounds():
    assert 0.0 <= _poisson_ge_k(2.0, 3) <= 1.0
    # more expected actions => higher chance of hitting threshold
    assert _poisson_ge_k(12, 10) > _poisson_ge_k(6, 10)


def test_nailed_starter_high_xmins():
    est = estimate_minutes(
        element_type=3, status="a", chance_of_playing=None,
        season_starts=10, season_minutes=880, team_matches_played=10,
        recent_minutes=[90, 90, 88, 90, 85],
    )
    assert est.p_start > 0.85
    assert est.xmins > 70
    assert est.confidence in ("High", "Medium")


def test_injured_player_low_xmins():
    est = estimate_minutes(
        element_type=4, status="i", chance_of_playing=0,
        season_starts=8, season_minutes=700, team_matches_played=10,
    )
    assert est.p_start < 0.2
    assert est.p_no_play > 0.7


def test_xp_scales_with_minutes():
    common = dict(
        element_type=4, minutes_season=900, xg_season=5.0, xa_season=2.0,
        saves_season=0, dc_season=20, yellow_season=2, red_season=0,
        bps_season=200, penalties_order=None, p_start=0.9, p_appear=0.95,
        p_60_plus=0.85, lam_team_goals=1.6, lam_conceded=1.1, team_avg_gf=1.5,
    )
    full = expected_points(xmins=85, **common)
    half = expected_points(xmins=45, **common)
    assert full.xp > half.xp                       # more minutes => more xP
    assert full.goals >= half.goals


def test_defender_gets_clean_sheet_and_defcon_ev():
    bd = expected_points(
        element_type=2, minutes_season=900, xg_season=0.5, xa_season=1.0,
        saves_season=0, dc_season=110, yellow_season=2, red_season=0,
        bps_season=200, penalties_order=None, xmins=88, p_start=0.95,
        p_appear=0.97, p_60_plus=0.9, lam_team_goals=1.4, lam_conceded=0.8,
        team_avg_gf=1.4,
    )
    assert bd.clean_sheet > 0        # good CS fixture
    assert bd.defcon > 0             # high defensive-contribution rate
    assert bd.negative <= 0


def test_preseason_fringe_player_not_nailed():
    """Pre-season (0 current games): a 2-start/135-min player must NOT read as
    a nailed 83' starter (regression for the matches_played=0 bug)."""
    est = estimate_minutes(
        element_type=2, status="a", chance_of_playing=None,
        season_starts=2, season_minutes=135, team_matches_played=0,
    )
    assert est.p_start < 0.2
    assert est.xmins < 25


def test_preseason_nailed_player_still_high():
    est = estimate_minutes(
        element_type=2, status="a", chance_of_playing=None,
        season_starts=36, season_minutes=3200, team_matches_played=0,
    )
    assert est.p_start > 0.85
    assert est.xmins > 70


# ---------------------------------------------------------- market odds ------
RHO = -0.13


def _synthetic_market(lam_h, lam_a, total_lines=(2.5, 3.5), handicaps=(-0.75, -0.5)):
    """The exact prices a book would hang if it believed (lam_h, lam_a, RHO)."""
    from app.providers.probability import (
        _distributions, _no_push_prob, _outcome_probs, _score_grid, _split,
    )

    grid = _score_grid(lam_h, lam_a, RHO)
    totals_dist, margin_dist = _distributions(grid)
    return (
        _outcome_probs(lam_h, lam_a, RHO),
        [(L, _no_push_prob(*_split(totals_dist, 0, L))) for L in total_lines],
        # home covers when (home - away) > -point
        [(h, _no_push_prob(*_split(margin_dist, -10, -h))) for h in handicaps],
    )


def test_odds_inversion_recovers_lambdas():
    """1X2 + totals + handicap -> expected goals must round-trip exactly."""
    from app.providers.probability import fit_lambdas

    for lam_h, lam_a in [(2.1, 0.9), (1.2, 1.3), (3.0, 0.6), (0.9, 1.8)]:
        p_1x2, totals, handicaps = _synthetic_market(lam_h, lam_a)
        fit_h, fit_a, err = fit_lambdas(p_1x2, totals, handicaps, rho=RHO)
        assert abs(fit_h - lam_h) < 0.01
        assert abs(fit_a - lam_a) < 0.01
        assert err < 1e-8          # all three markets satisfied at once


def test_odds_inversion_survives_missing_markets():
    """A market the book does not quote drops out; no default is substituted.

    Regression: the old code froze the total at a league average whenever the
    totals market was absent, which discarded what 1X2 already says about it.
    """
    from app.providers.probability import fit_lambdas

    lam_h, lam_a = 2.0, 1.1
    p_1x2, totals, handicaps = _synthetic_market(lam_h, lam_a)
    for label, args in [
        ("1x2+ou+ah", (p_1x2, totals, handicaps)),
        ("1x2+ou", (p_1x2, totals, [])),
        ("1x2+ah", (p_1x2, [], handicaps)),
        ("1x2 only", (p_1x2, [], [])),
        ("ou+ah only", (None, totals, handicaps)),
    ]:
        fit_h, fit_a, _ = fit_lambdas(*args, rho=RHO)
        assert abs(fit_h - lam_h) < 0.05, label
        assert abs(fit_a - lam_a) < 0.05, label


def test_dixon_coles_lifts_low_scoring_draws():
    """rho < 0 must raise 0-0 and 1-1 and trim 1-0 and 0-1, mass conserved."""
    from app.providers.probability import _outcome_probs, _score_grid

    poisson = _score_grid(1.5, 1.2, 0.0)
    dc = _score_grid(1.5, 1.2, RHO)

    assert dc[0][0] > poisson[0][0]
    assert dc[1][1] > poisson[1][1]
    assert dc[1][0] < poisson[1][0]
    assert dc[0][1] < poisson[0][1]
    assert dc[2][1] == pytest.approx(poisson[2][1], rel=1e-9)   # untouched
    assert sum(sum(row) for row in dc) == pytest.approx(1.0)
    # the whole point: independent Poisson under-prices the draw
    assert _outcome_probs(1.5, 1.2, RHO)[1] > _outcome_probs(1.5, 1.2, 0.0)[1]


def test_dixon_coles_preserves_marginals():
    """tau moves joint mass only — each team's goal distribution is untouched.

    This is why `team expected goals = lambda` is exact rather than an
    approximation, and why xpoints.py may keep computing a clean sheet as
    exp(-lambda_conceded) even though the market fit now uses a DC grid.
    Algebraically it holds because P(1; mu) == mu * P(0; mu).
    """
    from app.providers.probability import _score_grid

    for lam_h, lam_a in [(2.34, 0.72), (1.5, 1.2), (0.8, 2.2)]:
        grid = _score_grid(lam_h, lam_a, RHO)
        for k in range(5):
            poisson_k = math.exp(-lam_h) * lam_h**k / math.factorial(k)
            assert sum(grid[k]) == pytest.approx(poisson_k, abs=5e-4)
        clean_sheet = sum(grid[i][0] for i in range(11))    # away fails to score
        assert clean_sheet == pytest.approx(math.exp(-lam_a), abs=5e-4)


def test_asian_handicap_quarter_lines_and_pushes():
    """Quarter lines split across neighbours; level lines can push."""
    from app.providers.probability import (
        _distributions, _no_push_prob, _score_grid, _split,
    )

    _, margin = _distributions(_score_grid(1.8, 1.1, RHO))
    level = _split(margin, -10, 0.0)        # home 0.0  -> push on a draw
    half = _split(margin, -10, 0.5)         # home -0.5 -> no push
    quarter = _split(margin, -10, 0.25)     # home -0.25 -> half of each

    assert level[1] > 0 and half[1] == pytest.approx(0.0)
    for k in range(3):
        assert quarter[k] == pytest.approx((level[k] + half[k]) / 2)
    # a push refunds the stake, so the fair price excludes it
    assert _no_push_prob(*level) == pytest.approx(level[0] / (level[0] + level[2]))
    assert _no_push_prob(*level) > level[0]


def test_odds_line_consensus_drops_thin_outliers():
    """One book hanging an odd line must not drag the fit."""
    from app.providers.probability import _consensus_lines

    quotes = [(2.5, 0.50), (2.5, 0.52), (2.5, 0.51), (2.5, 0.49), (4.5, 0.10)]
    lines = _consensus_lines(quotes)
    assert [l for l, _ in lines] == [2.5]
    assert lines[0][1] == pytest.approx(0.505)

    # a genuine split between two lines is kept — both are real observations
    split = _consensus_lines([(2.5, 0.50), (2.5, 0.52), (3.0, 0.40), (3.0, 0.42)])
    assert [l for l, _ in split] == [2.5, 3.0]

    # a thin market where every line has one book: keep what little there is
    assert len(_consensus_lines([(2.5, 0.5), (3.0, 0.4)])) == 2


def test_odds_team_name_matching():
    from app.providers.probability import match_team_id

    fpl = {1: "Arsenal", 11: "Man Utd", 13: "Nott'm Forest", 17: "Spurs", 9: "Hull City"}
    assert match_team_id("Arsenal", fpl) == 1
    assert match_team_id("Manchester United", fpl) == 11
    assert match_team_id("Nottingham Forest", fpl) == 13
    assert match_team_id("Tottenham Hotspur", fpl) == 17
    assert match_team_id("Hull City", fpl) == 9
    assert match_team_id("Real Madrid", fpl) is None


def test_market_odds_override_model():
    """Where bookmaker data exists it must move the projection toward the market."""
    from app.engine.team_strength import TeamStrength

    class T:
        def __init__(self, i):
            self.id = i
            self.strength = 1200
            self.strength_attack_home = self.strength_attack_away = 1200
            self.strength_defence_home = self.strength_defence_away = 1200

    teams = [T(1), T(2)]
    base = TeamStrength(teams, [], [])
    with_mkt = TeamStrength(teams, [], [], market={(1, 2): (3.0, 0.4)}, market_weight=0.7)
    b_for, _ = base.expected_goals(1, 2, True)
    m_for, m_against = with_mkt.expected_goals(1, 2, True)
    assert m_for > b_for            # market says home team scores a lot
    assert m_against < 1.0
    assert with_mkt.has_market(1, 2, True)
    assert not with_mkt.has_market(2, 1, True)


def test_promoted_team_not_rated_elite_defence():
    """Đội mới lên hạng (không có dữ liệu Ngoại hạng) không được chấm phòng ngự tốt.

    Regression: chia mean_xGA cho mẫu ~0 đẩy chỉ số phòng ngự kịch trần, khiến
    Coventry/Ipswich có xác suất giữ sạch lưới cao hơn cả Arsenal.
    """
    from app.engine.team_strength import TeamStrength

    class T:
        def __init__(self, i):
            self.id = i
            self.strength = None
            self.strength_attack_home = self.strength_attack_away = None
            self.strength_defence_home = self.strength_defence_away = None

    class P:
        def __init__(self, tid, mins, xg, xgc):
            self.team_id = tid
            self.minutes = mins
            self.expected_goals = xg
            self.expected_assists = 0.0
            self.expected_goals_conceded = xgc

    established = [P(1, 3000, 12.0, 45.0) for _ in range(12)]   # đội lâu năm
    promoted = [P(2, 0, 0.0, 0.0) for _ in range(12)]           # đội mới lên hạng
    ts = TeamStrength([T(1), T(2)], established + promoted, [])

    strong = ts._rates[1]
    new = ts._rates[2]
    assert new.defence_home < strong.defence_home     # không được "khoẻ" hơn đội có dữ liệu
    assert 0.6 <= new.defence_home <= 0.95            # ở mức dưới trung bình, không cực đoan
    assert 0.6 <= new.attack_home <= 0.95

    # và giữ sạch lưới phải khó hơn cho đội mới lên hạng khi gặp cùng đối thủ
    cs_new = ts.clean_sheet_prob(2, 1, True)
    cs_old = ts.clean_sheet_prob(1, 2, True)
    assert cs_new < cs_old


# ------------------------------------- Championship (đội mới lên hạng) --------
def test_championship_season_code():
    from app.providers.championship import season_code

    assert season_code(2026) == "2526"   # Ngoại hạng 2026/27 -> Championship 2025/26
    assert season_code(2025) == "2425"


def test_championship_ranks_promoted_teams_without_exceeding_average():
    """Dữ liệu Championship chỉ để xếp hạng 3 đội với nhau, luôn dưới TB Ngoại hạng."""
    from app.engine.team_strength import PROMOTED_ATTACK, TeamStrength

    class T:
        def __init__(self, i):
            self.id = i
            self.strength = None
            self.strength_attack_home = self.strength_attack_away = None
            self.strength_defence_home = self.strength_defence_away = None

    class P:
        def __init__(self, tid, mins, xg, xgc):
            self.team_id = tid
            self.minutes = mins
            self.expected_goals = xg
            self.expected_assists = 0.0
            self.expected_goals_conceded = xgc

    established = [P(1, 3000, 12.0, 45.0) for _ in range(12)]
    strong_promoted = [P(2, 0, 0.0, 0.0) for _ in range(12)]   # vô địch Championship
    weak_promoted = [P(3, 0, 0.0, 0.0) for _ in range(12)]     # thắng play-off

    ts = TeamStrength(
        [T(1), T(2), T(3)],
        established + strong_promoted + weak_promoted,
        [],
        promoted={2: (1.62, 1.33), 3: (1.17, 0.91)},  # chỉ số trong Championship
        promoted_damping=0.35,
    )
    strong, weak = ts._rates[2], ts._rates[3]

    assert strong.attack_home > weak.attack_home       # phân hoá đúng thứ tự
    assert strong.defence_home > weak.defence_home
    assert strong.attack_home <= 1.0                   # không bao giờ vượt TB giải
    assert strong.defence_home <= 1.0
    assert weak.attack_home >= 0.5                     # cũng không bị dìm quá đà


def test_championship_can_be_switched_off():
    """Không có dữ liệu Championship -> quay về mức nền phẳng, không lỗi."""
    from app.engine.team_strength import (
        PROMOTED_ATTACK,
        PROMOTED_DEFENCE,
        TeamStrength,
    )

    class T:
        def __init__(self, i):
            self.id = i
            self.strength = None
            self.strength_attack_home = self.strength_attack_away = None
            self.strength_defence_home = self.strength_defence_away = None

    class P:
        def __init__(self, tid):
            self.team_id = tid
            self.minutes = 0
            self.expected_goals = 0.0
            self.expected_assists = 0.0
            self.expected_goals_conceded = 0.0

    class PF:
        def __init__(self, tid):
            self.team_id = tid
            self.minutes = 3000
            self.expected_goals = 12.0
            self.expected_assists = 0.0
            self.expected_goals_conceded = 45.0

    # cần một đội có lịch sử làm mốc thì mới nhận ra đội nào là mới lên hạng
    established = [PF(9) for _ in range(12)]
    ts = TeamStrength(
        [T(9), T(1)], established + [P(1) for _ in range(12)], [], promoted=None
    )
    assert ts._rates[1].attack_home == PROMOTED_ATTACK
    assert ts._rates[1].defence_home == PROMOTED_DEFENCE


# --------------------------------- chuyển sang dữ liệu thật sau vòng 1 --------
def test_after_gameweek1_no_team_flagged_as_promoted():
    """FPL reset thống kê đầu mùa: sau vòng 1 mọi đội chỉ có ~1000 phút.

    Regression: ngưỡng tuyệt đối khiến TẤT CẢ các đội (kể cả Man City) bị coi là
    mới lên hạng và tụt xuống mức nền 0.80.
    """
    from app.engine.team_strength import PROMOTED_ATTACK, TeamStrength

    class T:
        def __init__(self, i):
            self.id = i
            self.strength = None
            self.strength_attack_home = self.strength_attack_away = None
            self.strength_defence_home = self.strength_defence_away = None

    class P:
        def __init__(self, tid, xg):
            self.team_id = tid
            self.minutes = 90            # đúng 1 trận
            self.expected_goals = xg
            self.expected_assists = 0.0
            self.expected_goals_conceded = 1.2

    teams = [T(1), T(2), T(3)]
    players = [P(t, xg) for t, xg in ((1, 0.9), (2, 0.25), (3, 0.05)) for _ in range(11)]
    ts = TeamStrength(teams, players, [])

    for tid in (1, 2, 3):
        assert ts._rates[tid].attack_home != PROMOTED_ATTACK   # không bị gán nhãn
        # một trận đấu không đủ để kết luận mạnh/yếu -> phải gần mức trung bình
        assert 0.85 <= ts._rates[tid].attack_home <= 1.25


def test_one_gameweek_sample_does_not_create_certainty():
    """Đá chính/ngồi ghế đúng 1 trận không được cho ra 98% hay 0%."""
    started = estimate_minutes(
        element_type=3, status="a", chance_of_playing=None,
        season_starts=1, season_minutes=90, team_matches_played=1,
    )
    benched = estimate_minutes(
        element_type=3, status="a", chance_of_playing=None,
        season_starts=0, season_minutes=20, team_matches_played=1,
    )
    unused = estimate_minutes(
        element_type=3, status="a", chance_of_playing=None,
        season_starts=0, season_minutes=0, team_matches_played=1,
    )
    assert 0.45 < started.p_start < 0.8      # có tín hiệu nhưng chưa chắc chắn
    assert 0.1 < benched.p_start < 0.45
    assert started.p_start > benched.p_start > 0
    # vào sân từ ghế phải khác hẳn người không ra sân
    assert benched.p_sub > unused.p_sub

    # nhưng sau 10 vòng thì bằng chứng đủ để tự tin
    nailed = estimate_minutes(
        element_type=3, status="a", chance_of_playing=None,
        season_starts=10, season_minutes=900, team_matches_played=10,
    )
    assert nailed.p_start > 0.85


def test_zero_minutes_player_uses_role_prior_not_zero():
    """Cầu thủ chưa có phút Ngoại hạng nào (đội mới lên hạng, hoặc tân binh).

    Regression: chia 0 starts cho 38 trận -> mọi cầu thủ Coventry/Hull/Ipswich và
    75 tân binh đều ra p_start ~2%, tức mô hình bảo "đội này không xếp ai đá".
    """
    common = dict(
        status="a", chance_of_playing=None, season_starts=0,
        season_minutes=0, team_matches_played=0,
    )
    first_choice = estimate_minutes(element_type=4, role_rank=0, **common)
    fringe = estimate_minutes(element_type=4, role_rank=5, **common)

    assert 0.5 < first_choice.p_start < 0.8    # có khả năng đá chính, nhưng không chắc
    assert first_choice.xmins > 40
    assert fringe.p_start < 0.2                # người ngoài nhóm chính vẫn thấp
    assert first_choice.confidence == "Low"    # luôn gắn nhãn tin cậy thấp
    assert "mới lên hạng" in first_choice.reason or "vai trò" in first_choice.reason


def test_backup_goalkeeper_not_inflated():
    """Thủ môn số 2 gần như không ra sân — không được dùng thang của cầu thủ ngoài sân."""
    common = dict(
        element_type=1, status="a", chance_of_playing=None, season_starts=0,
        season_minutes=0, team_matches_played=0,
    )
    first = estimate_minutes(role_rank=0, **common)
    backup = estimate_minutes(role_rank=1, **common)
    assert first.p_start > 0.5
    assert backup.p_start < 0.15
    assert backup.p_sub < 0.05                 # thủ môn không được tung vào từ ghế


def test_role_prior_never_overrides_injury_flag():
    """Có cờ chấn thương/treo giò thì vẫn phải về ~0 dù xếp hạng vai trò cao."""
    out = estimate_minutes(
        element_type=4, status="i", chance_of_playing=0, season_starts=0,
        season_minutes=0, team_matches_played=0, role_rank=0,
    )
    assert out.p_start == 0.0
    assert out.xmins == 0.0


def test_players_with_history_unaffected_by_role_prior():
    """Cầu thủ đã có phút thi đấu vẫn dùng dữ liệu thật, không rơi vào nhánh vai trò."""
    nailed = estimate_minutes(
        element_type=2, status="a", chance_of_playing=None, season_starts=36,
        season_minutes=3200, team_matches_played=0, role_rank=8,   # rank thấp
    )
    assert nailed.p_start > 0.85    # dữ liệu thật thắng, không bị kéo về 0.12


# ------------------------------------------- đồng thuận nhà cái & độ mỏng ----
def test_median_consensus_ignores_a_single_mispriced_book():
    """Một nhà cái treo giá lệch không được kéo đồng thuận.

    Trung bình cho mỗi nhà cái quyền dịch đồng thuận 1/n; trung vị thì không,
    trừ khi giá lệch nằm gần giữa.
    """
    from app.providers.probability import _consensus_lines, _median

    prices = [0.52, 0.51, 0.53, 0.52, 0.52, 0.51, 0.53, 0.52, 0.51, 0.52, 0.53, 0.52]
    quotes = [(2.5, p) for p in prices]
    clean = _consensus_lines(quotes)
    dirty = _consensus_lines(quotes + [(2.5, 0.20)])   # một nhà cái stale

    assert clean[0][1] == pytest.approx(0.52, abs=1e-9)
    # trung vị gần như không nhúc nhích; trung bình sẽ lệch ~0.025
    assert dirty[0][1] == pytest.approx(0.52, abs=1e-9)
    assert abs(dirty[0][1] - sum(prices + [0.20]) / 13) > 0.02

    # trung vị số lẻ và số chẵn phần tử
    assert _median([1.0, 3.0, 2.0]) == 2.0
    assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert _median([]) == 0.0


def test_1x2_medians_are_renormalised_to_a_valid_distribution():
    """Ba trung vị độc lập không tự cộng thành 1 — phải chuẩn hoá lại."""
    from app.providers.probability import _median

    # mỗi nhà cái tự cộng thành 1, nhưng nhà cái nằm giữa KHÁC nhau ở từng kết cục
    books = [(0.50, 0.25, 0.25), (0.40, 0.35, 0.25), (0.44, 0.26, 0.30)]
    for b in books:
        assert sum(b) == pytest.approx(1.0, abs=1e-9)
    m = [
        _median([b[0] for b in books]),
        _median([b[1] for b in books]),
        _median([b[2] for b in books]),
    ]
    assert sum(m) != pytest.approx(1.0, abs=1e-9)      # đúng là lệch
    tot = sum(m)
    normalised = [x / tot for x in m]
    assert sum(normalised) == pytest.approx(1.0, abs=1e-12)


def test_market_weight_falls_when_few_books_priced_the_fixture():
    from app.engine.team_strength import TeamStrength

    teams = [type("T", (), {"id": i, "short_name": f"T{i}", "code": i,
                            "strength": 1100, "strength_overall_home": 1100,
                            "strength_overall_away": 1100, "strength_attack_home": 1100,
                            "strength_attack_away": 1100, "strength_defence_home": 1100,
                            "strength_defence_away": 1100})() for i in (1, 2)]
    market = {(1, 2): (3.0, 0.4)}          # thị trường nói đội nhà rất mạnh

    full = TeamStrength(teams, [], [], market=market, market_weight=0.7,
                        market_support={(1, 2): 20}, full_support_books=8)
    thin = TeamStrength(teams, [], [], market=market, market_weight=0.7,
                        market_support={(1, 2): 2}, full_support_books=8)
    unknown = TeamStrength(teams, [], [], market=market, market_weight=0.7,
                           market_support={}, full_support_books=8)

    lam_full = full.expected_goals(1, 2, True)[0]
    lam_thin = thin.expected_goals(1, 2, True)[0]
    lam_unknown = unknown.expected_goals(1, 2, True)[0]

    # 20 nhà cái: đủ hỗ trợ nên không bị hạ — giống hệt khi không khai số nhà cái
    assert lam_full == pytest.approx(lam_unknown, abs=1e-9)
    # 2 nhà cái: nghiêng về mô hình nội bộ nên xa mức thị trường 3.0 hơn
    assert lam_thin < lam_full
    # nhưng vẫn phải nhúc nhích theo thị trường, không bị bỏ hẳn
    no_market = TeamStrength(teams, [], [], market={}, market_weight=0.7)
    assert lam_thin > no_market.expected_goals(1, 2, True)[0]


# ----------------------------------------- mốc hàng thủ & chiết khấu đổi HLV ----
def test_defence_proxy_uses_the_goalkeeper_not_any_outfield_player():
    """Mốc hàng thủ phải là thủ môn, theo xGC/90.

    Bản trước lấy `max(expected_goals_conceded)` toàn đội và chọn nhầm người:
    hàng thủ Man City bị chấm bằng Elliot Anderson — 53.6 xGC tích luỹ ở
    Nottingham Forest — nên City thành "kém trung bình giải" và Crystal Palace được
    cho 2.01 bàn kỳ vọng khi tiếp City.
    """
    from app.engine.team_strength import _defence_proxy

    class P:
        def __init__(self, pid, team, etype, mins, xgc):
            self.id, self.team_id, self.element_type = pid, team, etype
            self.minutes, self.expected_goals_conceded = mins, xgc

    players = [
        P(1, 10, 1, 3060, 38.4),    # thủ môn: 1.13 / 90
        P(2, 10, 3, 3332, 53.6),    # tiền vệ có xGC TỔNG cao hơn — không được chọn
        P(3, 10, 1, 400, 12.0),     # thủ môn dự bị, dưới ngưỡng phút
    ]
    out = _defence_proxy(players)
    assert out[10] == pytest.approx(38.4 / (3060 / 90), rel=1e-6)
    assert out[10] < 1.3, "chọn nhầm cầu thủ ngoài sân"


def test_defence_proxy_skips_flagged_new_signings():
    """Tân binh mang xGC của CLB CŨ — dùng vào là chấm nhầm đội."""
    from app.config import settings
    from app.engine.team_strength import _defence_proxy

    class P:
        def __init__(self, pid, team, etype, mins, xgc):
            self.id, self.team_id, self.element_type = pid, team, etype
            self.minutes, self.expected_goals_conceded = mins, xgc

    signing_id = int((settings.new_signing_players.split(",") or ["0"])[0])
    players = [
        P(signing_id, 20, 1, 3400, 90.0),   # tân binh, xGC/90 rất xấu
        P(999_001, 20, 1, 2000, 20.0),      # thủ môn thật của CLB
    ]
    out = _defence_proxy(players)
    assert out[20] == pytest.approx(20.0 / (2000 / 90), rel=1e-6)


def test_defence_proxy_tolerates_partial_player_objects():
    """Thiếu một thuộc tính không được làm sập cả mô hình sức mạnh đội."""
    from app.engine.team_strength import _defence_proxy

    class Bare:
        def __init__(self, team):
            self.team_id = team

    assert _defence_proxy([Bare(1), Bare(2)]) == {}


def test_new_manager_discounts_last_season_rating():
    """CLB đổi HLV thì xếp hạng mùa trước bị kéo về trung bình giải.

    Trước vòng 1, `own_minutes` là số phút MÙA TRƯỚC nên nguyên trạng nó cho trọng
    số ~0.79 vào một mô tả của đội bóng CŨ — trong khi 12/20 CLB đã đổi HLV.
    """
    from app.config import settings
    from app.engine.team_strength import _manager_factor
    from app.services.season_state import new_manager_clubs

    changed = sorted(new_manager_clubs())
    if not changed:
        pytest.skip("chưa khai CLB nào đổi HLV")

    class T:
        def __init__(self, short):
            self.short_name = short

    assert _manager_factor(T(changed[0])) == pytest.approx(
        settings.prior_weight_new_manager
    )
    assert _manager_factor(T("__KHONG_TON_TAI__")) == 1.0
    # hệ số phải kéo VỀ trung bình, tức nhỏ hơn 1
    assert settings.prior_weight_new_manager < 1.0


# ------------------------------------------- tỷ lệ per-90 trên mẫu cực nhỏ ----
def test_per90_rates_are_shrunk_so_a_one_minute_sample_cannot_explode():
    """`tổng_mùa / (phút/90)` phải co giãn về prior, nếu không nó nổ ở mẫu bé.

    Regression đo được: một cầu thủ trẻ **đá đúng 1 phút với 3 BPS** cho ra
    `bps90 = 270` (người dẫn đầu giải thật sự chỉ 29.6). Qua số mũ 1.99 của
    `standalone_bonus`, anh ta nhận **1.91 điểm bonus mỗi trận** trong khi một
    trung vệ đá chính với 724 BPS cả mùa nhận 0.41 — gấp 4.6 lần.

    `xg90`/`xa90`/`dc90` vốn đã được `_shrink()` bảo vệ; `bps90`, `saves90`,
    `yc90`, `rc90` thì chưa, và đó là toàn bộ khác biệt.
    """
    from app.engine.xpoints import PRIOR_BPS90, expected_points

    common = dict(
        xg_season=0.0, xa_season=0.0, dc_season=0.0, red_season=0,
        cbi_season=0.0, penalties_order=None, lam_team_goals=1.5,
        lam_conceded=1.2, team_avg_gf=1.42,
    )
    # Đúng hai cầu thủ đã đo được sự đảo ngược: Byfield (TOT) và Gabriel (ARS),
    # kèm xMins mà mô hình phút thật sự gán cho họ.
    rookie = expected_points(
        element_type=2, minutes_season=1, saves_season=0, yellow_season=0,
        bps_season=3, xmins=2.5, p_start=0.022, p_appear=0.055,
        p_60_plus=0.021, **common,
    )
    regular = expected_points(
        element_type=2, minutes_season=2750, saves_season=0, yellow_season=6,
        bps_season=724, xmins=68.5, p_start=0.772, p_appear=0.955,
        p_60_plus=0.749, **common,
    )

    # 1 phút không được cho ra một tỷ lệ ngoài khoảng của cả giải
    assert rookie.components["bps90"] == pytest.approx(PRIOR_BPS90[2], abs=1.0)
    assert rookie.components["bps90"] < 30.0, "mẫu 1 phút vẫn đang nổ"
    # Trước khi sửa: tân binh 1.907 vs trụ cột 0.412 — ĐẢO NGƯỢC gấp 4.6 lần.
    assert regular.bonus > rookie.bonus * 10, (
        f"tân binh {rookie.bonus:.3f} vs trụ cột {regular.bonus:.3f}"
    )

    # Hai tỷ lệ còn lại cũng vậy, và ở đây phải cho người dự bị một suất ra sân
    # thật (xMins 45) — nếu không thì `minutes_frac` bé tự che mất chỗ nổ.
    fringe = dict(xmins=45.0, p_start=0.3, p_appear=0.6, p_60_plus=0.3)

    # 1 thẻ vàng trong 1 phút không phải "0.9 thẻ mỗi trận"
    carded = expected_points(
        element_type=2, minutes_season=1, saves_season=0, yellow_season=1,
        bps_season=3, **fringe, **common,
    )
    assert carded.negative > -1.0, f"phạt thẻ nổ: {carded.negative}"

    # thủ môn dự bị: 1 pha cứu thua trong 5 phút không phải 18 lần cứu/90
    keeper = expected_points(
        element_type=1, minutes_season=5, saves_season=1, yellow_season=0,
        bps_season=3, **fringe, **common,
    )
    assert keeper.saves < 1.0, f"cứu thua nổ: {keeper.saves}"


def test_preseason_confidence_cannot_be_labelled_high():
    """Trước vòng 1, không dự báo nào được gắn nhãn "Cao".

    Regression: phần thưởng "mẫu lớn" (+0.1 khi > 900 phút) đọc `minutes_season`,
    mà trước vòng 1 đó là tổng phút của mùa TRƯỚC — tức phần thưởng được trao cho
    một mẫu chưa hề tồn tại. Đo được: 241 cầu thủ nằm đúng ở 0.70 và giao diện gắn
    "Tin cậy: Cao" cho toàn bộ nhóm ứng viên đội trưởng, ngay cạnh banner của
    chính hệ thống ghi "PRE-SEASON · 100% dựa trên prior · Confidence: Low".
    """
    from app.engine.risk import PRESEASON_CONFIDENCE_CAP, confidence_from

    # ngưỡng mà services/captains.py gọi là "Cao"
    HIGH = 0.70

    veteran_preseason = confidence_from("High", 2750, False, team_matches_played=0)
    assert veteran_preseason < HIGH, f"vẫn ra nhãn Cao: {veteran_preseason}"
    assert veteran_preseason <= PRESEASON_CONFIDENCE_CAP

    # nhưng khi mùa giải đã chạy thì đúng là được phép Cao
    veteran_inseason = confidence_from("High", 2750, True, team_matches_played=8)
    assert veteran_inseason >= HIGH
    assert veteran_inseason > veteran_preseason

    # mẫu bé vẫn bị phạt trong cả hai giai đoạn — đó là hình phạt, không phải thưởng
    assert confidence_from("Low", 100, False, team_matches_played=8) < confidence_from(
        "Low", 2750, False, team_matches_played=8
    )

    # bỏ trống tham số = hành vi cũ, chỗ gọi lẻ không gãy
    assert confidence_from("High", 2750, False) >= HIGH

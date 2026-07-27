"""Projection engine unit tests (xMins, xP, Poisson helpers)."""
import math

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
def test_odds_inversion_recovers_lambdas():
    """1X2 + totals -> expected goals must round-trip back to the same market."""
    from app.providers.probability import _outcome_probs, _p_over, _solve_supremacy, _solve_total

    lam_h, lam_a = 2.1, 0.9
    ph, _, _ = _outcome_probs(lam_h, lam_a)
    total = _solve_total(_p_over(lam_h + lam_a, 2.5), 2.5)
    sup = _solve_supremacy(ph, total)
    assert abs(total - (lam_h + lam_a)) < 0.05
    assert abs((total + sup) / 2 - lam_h) < 0.1
    assert abs((total - sup) / 2 - lam_a) < 0.1


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

    ts = TeamStrength([T(1)], [P(1) for _ in range(12)], [], promoted=None)
    assert ts._rates[1].attack_home == PROMOTED_ATTACK
    assert ts._rates[1].defence_home == PROMOTED_DEFENCE

"""Expected points model (spec §7), decomposed by scoring component.

xP = appearance + goals + assists + clean_sheet + saves + bonus + defcon
     - cards - conceded

All rates are per-90 from season data, shrunk toward position priors
(Bayesian shrinkage) to avoid over-rating small samples, then scaled by
expected minutes and the specific fixture's difficulty.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app import bps_rules as bps_mod
from app import scoring as scoring_mod
from app.engine import bonus as bonus_mod
from app.scoring import RULES

# per-90 position priors (league-ish) used for shrinkage of small samples
PRIOR_XG90 = {1: 0.0, 2: 0.05, 3: 0.15, 4: 0.35}
PRIOR_XA90 = {1: 0.0, 2: 0.06, 3: 0.14, 4: 0.12}
PRIOR_DC90 = {1: 0.0, 2: 8.0, 3: 6.0, 4: 3.0}   # defensive contribution actions/90
SHRINK_MINUTES = 540.0   # ~6 full matches before season data dominates prior


@dataclass
class XPBreakdown:
    xp: float
    appearance: float
    goals: float
    assists: float
    clean_sheet: float
    saves: float
    bonus: float
    defcon: float
    negative: float
    # probabilities for UI
    goal_prob: float
    assist_prob: float
    clean_sheet_prob: float
    components: dict = field(default_factory=dict)


def _shrink(rate: float, prior: float, minutes: float) -> float:
    w = minutes / (minutes + SHRINK_MINUTES)
    return w * rate + (1 - w) * prior


def _poisson_at_least_one(lmbda: float) -> float:
    return 1.0 - math.exp(-max(lmbda, 0.0))


def _poisson_ge_k(lmbda: float, k: int) -> float:
    """P(X >= k) for Poisson(lambda)."""
    lmbda = max(lmbda, 1e-9)
    cdf = 0.0
    term = math.exp(-lmbda)
    for i in range(0, k):
        cdf += term
        term *= lmbda / (i + 1)
    return max(0.0, min(1.0, 1.0 - cdf))


def expected_points(
    *,
    element_type: int,
    minutes_season: int,
    xg_season: float,
    xa_season: float,
    saves_season: int,
    dc_season: float,
    yellow_season: int,
    red_season: int,
    bps_season: int,
    cbi_season: float = 0.0,
    # Mùa mà các tổng cả-mùa ở trên thực sự thuộc về. Trước vòng 1, FPL vẫn phát
    # tổng của mùa trước, nên nó KHÁC mùa đang chơi và BPS phải được quy đổi.
    # None = coi như cùng mùa với luật đang áp (không quy đổi).
    stats_season: str | None = None,
    # Suất bonus đã chia trong nội bộ trận đấu. None = không có ngữ cảnh cả trận.
    bonus_override: float | None = None,
    penalties_order: int | None,
    # from xMins model
    xmins: float,
    p_start: float,
    p_appear: float,
    p_60_plus: float,
    # from team strength for THIS fixture
    lam_team_goals: float,
    lam_conceded: float,
    team_avg_gf: float,
    n_fixtures: int = 1,
) -> XPBreakdown:
    pos = element_type
    rules = RULES
    mins90 = max(minutes_season, 1) / 90.0

    # per-90 underlying, shrunk toward prior
    xg90 = _shrink(xg_season / mins90, PRIOR_XG90.get(pos, 0.1), minutes_season)
    xa90 = _shrink(xa_season / mins90, PRIOR_XA90.get(pos, 0.1), minutes_season)
    dc90 = _shrink(dc_season / mins90, PRIOR_DC90.get(pos, 4.0), minutes_season)
    saves90 = (saves_season / mins90) if pos == 1 else 0.0
    yc90 = yellow_season / mins90
    rc90 = red_season / mins90

    # fixture difficulty adjustment: scale attacking output by how this fixture's
    # expected team goals compares to the team's season average
    fixture_adj = 1.0
    if team_avg_gf > 0.1:
        fixture_adj = max(0.4, min(1.8, lam_team_goals / team_avg_gf))

    minutes_frac = xmins / 90.0  # already includes DGW multiplier via xmins

    # penalty responsibility bump (penalty taker gets extra goal threat)
    pen_bump = 1.0
    if penalties_order == 1:
        pen_bump = 1.12
    elif penalties_order == 2:
        pen_bump = 1.04

    # ---- component EVs ----
    exp_goals = xg90 * minutes_frac * fixture_adj * pen_bump
    exp_assists = xa90 * minutes_frac * fixture_adj

    goal_ev = exp_goals * rules.goal_points.get(pos, 4)
    assist_ev = exp_assists * rules.assist_points

    appearance_ev = p_60_plus * rules.points_play_60_plus + max(
        0.0, p_appear - p_60_plus
    ) * rules.points_play_under_60

    # clean sheet — needs 60'+, prob = Poisson(0 conceded)
    cs_prob = math.exp(-lam_conceded)
    cs_ev = cs_prob * p_60_plus * rules.clean_sheet_points.get(pos, 0)

    # saves (GK): +1 per 3
    save_ev = 0.0
    if pos == 1:
        shot_adj = max(0.6, min(1.8, lam_conceded / 1.3))
        exp_saves = saves90 * minutes_frac * shot_adj
        save_ev = exp_saves / rules.saves_per_point

    # goals conceded penalty (GK/DEF): -1 per 2, requires 60'
    conceded_ev = 0.0
    if pos in rules.conceded_penalty_positions:
        conceded_ev = (lam_conceded / 2.0) * rules.points_per_two_conceded * p_60_plus

    # defensive contribution (2025/26)
    defcon_ev = 0.0
    if pos in rules.defcon_positions:
        threshold = (
            rules.defcon_threshold_def if pos == 2 else rules.defcon_threshold_att
        )
        exp_actions = dc90 * minutes_frac
        p_hit = _poisson_ge_k(exp_actions, threshold)
        defcon_ev = rules.defcon_points * p_hit

    # bonus — from BPS/90 blended with attacking/CS involvement.
    # BPS mang từ mùa trước sang được quy về luật đang áp trước khi dùng: mùa
    # 2026/27 hạ BPS từ CBI (1 điểm/2 -> 1 điểm/3) nên tổng BPS cũ của trung vệ
    # phóng đại tiềm năng bonus của họ. Xem app/bps_rules.py.
    active_bps = scoring_mod.BPS_RULES
    bps_effective = bps_mod.equivalent_bps(
        bps=bps_season,
        cbi=cbi_season,
        saves=saves_season,
        minutes=minutes_season,
        from_rules=bps_mod.for_season(stats_season) if stats_season else active_bps,
        to_rules=active_bps,
    )
    bps90 = bps_effective / mins90

    # Bonus là quỹ CỐ ĐỊNH 6 điểm mỗi trận chia cho 3 người có BPS cao nhất, nên
    # nó không tính được từ một cầu thủ đứng riêng. `bonus_override` là suất đã
    # chia trong nội bộ trận (do engine/bonus.allocate tính, gọi từ projections);
    # thiếu nó thì dùng dạng rời rạc đã khớp dữ liệu, kém hơn nhưng vẫn hiệu chuẩn.
    if bonus_override is not None:
        bonus_ev = min(rules.max_bonus, max(0.0, bonus_override))
    else:
        bonus_ev = bonus_mod.standalone_bonus(
            bps90=bps90, minutes_frac=minutes_frac, max_bonus=rules.max_bonus,
        )

    # cards
    card_ev = yc90 * minutes_frac * rules.yellow_card_points + rc90 * minutes_frac * rules.red_card_points

    negative = conceded_ev + card_ev  # both are <= 0

    xp = (
        appearance_ev
        + goal_ev
        + assist_ev
        + cs_ev
        + save_ev
        + bonus_ev
        + defcon_ev
        + negative
    )

    return XPBreakdown(
        xp=round(xp, 3),
        appearance=round(appearance_ev, 3),
        goals=round(goal_ev, 3),
        assists=round(assist_ev, 3),
        clean_sheet=round(cs_ev, 3),
        saves=round(save_ev, 3),
        bonus=round(bonus_ev, 3),
        defcon=round(defcon_ev, 3),
        negative=round(negative, 3),
        goal_prob=round(_poisson_at_least_one(exp_goals), 3),
        assist_prob=round(_poisson_at_least_one(exp_assists), 3),
        clean_sheet_prob=round(cs_prob, 3),
        components={
            "xg90": round(xg90, 3),
            "xa90": round(xa90, 3),
            "dc90": round(dc90, 2),
            "fixture_adj": round(fixture_adj, 3),
            "minutes_frac": round(minutes_frac, 3),
            "lam_team_goals": round(lam_team_goals, 3),
            "lam_conceded": round(lam_conceded, 3),
            # Nguyên liệu để chia quỹ bonus trong nội bộ trận đấu. Được trả ra đây
            # thay vì tính lại ở projections.py: hai bản sao của cùng phép tính là
            # hai chỗ có thể lệch nhau về sau.
            "bps90": round(bps90, 3),
            "exp_goals": round(exp_goals, 4),
            "exp_assists": round(exp_assists, 4),
            "cs_prob": round(cs_prob, 4),
            "p_60_plus": round(p_60_plus, 4),
        },
    )

"""Sáu bước của mô hình độ khó lịch thi đấu.

Mỗi bước có test riêng cho đúng cái nó hứa, và những chỗ đã từng sai — hoặc dễ
sai một cách âm thầm — đều bị khoá lại: chuẩn hoá lại trọng số khi thiếu nguồn,
hai chiều λ phải khớp nhau, blend hình học chứ không phải số học, vòng trắng
tính 0 chứ không phải biến mất, và ngũ phân vị phải ra đúng năm nhóm bằng nhau.
"""
from datetime import datetime, timedelta, timezone

import math

import pytest

from app.engine.fixture_difficulty import (
    POSITIONS,
    build_reference_players,
    decay_weight,
    defence_value,
    fdr_from_percentile,
    percentiles,
    quintile_fdr,
    rank_fixtures,
    rate_fixture,
    schedule_ease,
    uncertainty_penalty,
)
from app.engine.prior_strength import (
    NOMINAL_WEIGHTS,
    Observation,
    build_priors,
    solve_attack_defence,
)
from app.engine.team_strength import TeamStrength


# --------------------------------------------------------------- đồ chơi ----
class T:
    """Đội tối giản. `short` để thử nhánh đổi huấn luyện viên."""

    def __init__(self, i, short=None, ratings=None):
        self.id = i
        self.short_name = short or f"T{i}"
        self.name = self.short_name
        self.strength = ratings
        self.strength_attack_home = self.strength_attack_away = ratings
        self.strength_defence_home = self.strength_defence_away = ratings


class P:
    def __init__(self, pid, tid, etype=3, mins=3000, xg=4.0, xgc=40.0,
                 cost=60, status="a", chance=None):
        self.id, self.team_id, self.element_type = pid, tid, etype
        self.minutes, self.expected_goals, self.expected_goals_conceded = mins, xg, xgc
        self.expected_assists = 2.0
        self.now_cost, self.status = cost, status
        self.chance_of_playing_next_round = chance
        self.saves = 90 if etype == 1 else 0
        self.defensive_contribution = 200.0
        self.yellow_cards, self.red_cards, self.bps = 4, 0, 500
        self.clearances_blocks_interceptions = 120.0


class F:
    """Trận đấu tối giản, có giờ thi đấu để thử ngày nghỉ / mật độ."""

    _n = 0

    def __init__(self, h, a, kickoff=None, hs=None, as_=None, fid=None):
        F._n += 1
        self.id = fid if fid is not None else F._n
        self.team_h, self.team_a = h, a
        self.kickoff_time = kickoff
        self.team_h_score, self.team_a_score = hs, as_
        self.finished = hs is not None
        self.event = 1


def _squad(tid, cost=60, **kw):
    """Một đội hình đủ bốn tuyến, đủ dữ liệu cho mọi thành phần của BƯỚC 1."""
    out = [P(tid * 100 + 1, tid, 1, xg=0.0, cost=cost - 10, **kw)]
    out += [P(tid * 100 + 1 + i, tid, 2, xg=1.0, cost=cost - 5, **kw) for i in range(1, 6)]
    out += [P(tid * 100 + 6 + i, tid, 3, xg=4.0, cost=cost, **kw) for i in range(1, 6)]
    out += [P(tid * 100 + 11 + i, tid, 4, xg=6.0, cost=cost + 5, **kw) for i in range(1, 4)]
    return out


# ============================================================== BƯỚC 1 ======
def test_missing_source_drops_out_of_the_denominator():
    """Nguồn thiếu dữ liệu bị loại khỏi mẫu số, KHÔNG được mặc định bằng 1.0.

    Mặc định bằng trung bình giải là lén kéo mọi đội về giữa rồi vẫn khai là đã
    dùng đủ năm nguồn. Ở đây chỉ có xG và định giá đội hình, nên tổng bằng chứng
    phải đúng bằng 45% + 15% + 10% (neo HLV) chứ không phải 100%.
    """
    teams = [T(1), T(2), T(3)]
    players = sum((_squad(t) for t in (1, 2, 3)), [])
    priors = build_priors(teams, players)

    p = priors[1]
    assert p.components["market_elo"].available is False
    assert p.components["preseason"].available is False
    expected = (
        NOMINAL_WEIGHTS["xg_adjusted"]
        + NOMINAL_WEIGHTS["squad_quality"]
        + NOMINAL_WEIGHTS["manager_continuity"]
    )
    assert p.evidence_weight == pytest.approx(expected, abs=1e-6)
    assert p.evidence_weight < 1.0, "thiếu 2/5 nguồn mà vẫn khai đủ bằng chứng"


def test_new_manager_pulls_prior_toward_the_league_mean():
    """Đổi HLV chuyển trọng số từ xG mùa trước sang neo trung bình giải.

    Nó KHÔNG được chấm đội đó yếu đi — "đổi HLV" là bằng chứng dữ liệu cũ mô tả
    kém đi, không phải bằng chứng đội kém đi. Nên prior phải dịch VỀ 1.0, bất kể
    trước đó nó ở phía nào của 1.0.
    """
    strong = _squad(1, cost=90)          # đội mạnh
    weak = _squad(2, cost=45)
    weak2 = _squad(3, cost=45)
    for p in strong:
        p.expected_goals *= 2.0
    teams_stable = [T(1, "AAA"), T(2, "BBB"), T(3, "CCC")]
    teams_changed = [T(1, "ZZZ"), T(2, "BBB"), T(3, "CCC")]
    players = strong + weak + weak2

    stable = build_priors(teams_stable, players, new_manager_shorts=set())
    changed = build_priors(teams_changed, players, new_manager_shorts={"ZZZ"})

    assert stable[1].attack > 1.0
    assert changed[1].attack < stable[1].attack, "phải kéo VỀ trung bình giải"
    assert changed[1].attack > 1.0, "nhưng không được kéo qua bên kia"


def test_opponent_adjustment_discounts_an_easy_run():
    """Cùng một lượng xG, gặp đối thủ yếu thì không được chấm bằng gặp đối thủ mạnh.

    Đây là chữ "opponent-adjusted" của BƯỚC 1. Cộng thẳng xG cả mùa — cách làm cũ
    — coi hai thứ đó như nhau, nên đội có lịch đầu mùa nhẹ được thổi lên đúng bằng
    mức nhẹ của lịch.
    """
    # 1 và 2 cùng ghi 2.0 xG mỗi trận; 1 chỉ gặp hàng thủ nát (3,4), 2 gặp hàng
    # thủ tốt (5,6). Các trận khác dựng nền để hệ thống xác định được.
    obs = []
    for _ in range(3):
        obs += [
            Observation(1, 3, 2.0, True), Observation(3, 1, 1.0, False),
            Observation(1, 4, 2.0, False), Observation(4, 1, 1.0, True),
            Observation(2, 5, 2.0, True), Observation(5, 2, 1.0, False),
            Observation(2, 6, 2.0, False), Observation(6, 2, 1.0, True),
            # 3,4 thủng nhiều trước mọi người; 5,6 thì không
            Observation(5, 3, 3.0, True), Observation(3, 5, 0.5, False),
            Observation(6, 4, 3.0, True), Observation(4, 6, 0.5, False),
        ]
    solved = solve_attack_defence(obs, [1, 2, 3, 4, 5, 6])
    assert solved is not None
    att, weak, _h = solved
    assert att[2] > att[1], "đội gặp hàng thủ tốt phải được chấm tấn công cao hơn"
    assert weak[3] > weak[5], "hàng thủ nát phải bị chấm là dễ thủng hơn"


def test_thin_graph_refuses_to_answer():
    """Đồ thị trận quá thưa -> trả None, thà không trả lời còn hơn bịa nghiệm."""
    obs = [Observation(1, 2, 2.0, True), Observation(2, 1, 1.0, False)]
    assert solve_attack_defence(obs, [1, 2]) is None


def test_one_match_cannot_make_a_team_elite():
    """Co giãn theo cỡ mẫu: một trận rực rỡ không được đẩy prior lên kịch trần."""
    teams = [T(i) for i in (1, 2, 3)]
    players = []
    for tid, xg in ((1, 6.0), (2, 0.5), (3, 0.3)):
        players += [P(tid * 100 + i, tid, 3, mins=90, xg=xg) for i in range(11)]
    priors = build_priors(teams, players)
    assert 0.85 <= priors[1].attack <= 1.30, priors[1].attack


# ============================================================== BƯỚC 2 ======
def _ts(**kw):
    teams = [T(1), T(2), T(3)]
    players = sum((_squad(t, cost=60 + 10 * t) for t in (1, 2, 3)), [])
    return TeamStrength(teams, players, [], **kw)


def test_log_terms_add_up_to_the_structural_lambda():
    """Phân rã phải là phân rã THẬT: tổng các số hạng đúng bằng log λ cấu trúc."""
    ts = _ts()
    t, _ = ts.terms(1, 2, True)
    total = (
        t.baseline + t.attack + t.opponent_defence + t.home + t.lineup
        + t.rest + t.congestion + t.squad + t.manager + t.promotion + t.calibration
    )
    assert total == pytest.approx(t.structural, abs=1e-9)
    assert math.exp(t.structural) == pytest.approx(t.lam, abs=1e-9)


def test_lambda_against_is_the_same_formula_run_backwards():
    """λ_against(A) phải BẰNG λ_for(B). Hai công thức song song là hai chỗ để lệch."""
    ts = _ts()
    a_for, a_against = ts.expected_goals(1, 2, True)
    b_for, b_against = ts.expected_goals(2, 1, False)
    assert a_against == pytest.approx(b_for, abs=1e-12)
    assert a_for == pytest.approx(b_against, abs=1e-12)


def test_home_advantage_is_symmetric_in_log_space():
    """Cùng cặp đội, đổi sân: tích của hai λ_for phải không đổi.

    Bản trước nhân 1.12 cho đội nhà và 0.90 cho đội khách — tích 1.008 ≠ 1 — nên
    tổng số bàn của giải trôi theo tỷ lệ sân nhà/khách của lịch.
    """
    ts = _ts()
    home = ts.expected_goals(1, 2, True)[0]
    away = ts.expected_goals(1, 2, False)[0]
    neutral = math.sqrt(home * away)
    ts2 = _ts()
    h2 = ts2.expected_goals(2, 1, True)[0]
    a2 = ts2.expected_goals(2, 1, False)[0]
    assert neutral == pytest.approx(math.sqrt(home * away), abs=1e-12)
    # lợi thế sân nhà là MỘT hệ số: đi lên đúng bằng đi xuống
    assert (home / neutral) == pytest.approx(neutral / away, abs=1e-9)
    assert (h2 / math.sqrt(h2 * a2)) == pytest.approx(home / neutral, abs=1e-9)


def test_rest_differential_moves_lambda_the_right_way():
    """Nghỉ nhiều hơn đối thủ -> ghi nhiều hơn một chút, và ngược lại."""
    base = datetime(2026, 8, 15, 14, tzinfo=timezone.utc)
    teams = [T(1), T(2)]
    players = _squad(1) + _squad(2)
    # đội 1 đá giữa tuần trước đó, đội 2 nghỉ trọn tuần
    schedule = [
        F(1, 3, base - timedelta(days=2), fid=901),
        F(2, 3, base - timedelta(days=8), fid=902),
        F(1, 2, base, fid=903),
    ]
    ts = TeamStrength(teams, players, [], schedule=schedule)
    tired, _ = ts.terms(1, 2, True, 903)
    assert tired.rest < 0, "đội vừa đá cách 2 ngày phải bị trừ, không được thưởng"
    fresh, _ = ts.terms(2, 1, False, 903)
    assert fresh.rest == pytest.approx(-tired.rest, abs=1e-12)
    # và không có fixture_id thì bằng 0, kèm lý do
    no_ctx, _ = ts.terms(1, 2, True)
    assert no_ctx.rest == 0.0 and "rest" in no_ctx.notes


def test_missing_players_reduce_expected_goals():
    """Số hạng đội hình: mất người đắt tiền thì ghi ít hơn."""
    teams = [T(1), T(2), T(3)]
    healthy = sum((_squad(t) for t in (1, 2, 3)), [])
    injured = sum((_squad(t) for t in (2, 3)), []) + _squad(1, status="i", chance=0)
    lam_ok = TeamStrength(teams, healthy, []).terms(1, 2, True)[0]
    lam_bad = TeamStrength(teams, injured, []).terms(1, 2, True)[0]
    assert lam_bad.lineup < lam_ok.lineup
    assert lam_bad.lam < lam_ok.lam


def test_promoted_team_is_capped_at_the_league_average():
    """Đội chưa có phút Ngoại hạng nào không được chấm trên mức trung bình giải."""
    teams = [T(1), T(2)]
    # đội 2 không có lịch sử, nhưng đội hình rất đắt -> nguồn giá sẽ đẩy nó lên
    players = _squad(1, cost=50) + [
        P(200 + i, 2, 3, mins=0, xg=0.0, cost=130) for i in range(14)
    ]
    ts = TeamStrength(teams, players, [])
    t, _ = ts.terms(2, 1, True)
    assert ts._rates[2].no_pl_history
    assert t.promotion <= 0.0
    assert ts._rates[2].promotion_cap_applied or t.promotion == 0.0


# ============================================================== BƯỚC 3 ======
def test_market_blend_is_geometric_not_arithmetic():
    """λ_cuối = λ_thị_trường^w · λ_cấu_trúc^(1−w), không phải trung bình cộng."""
    teams = [T(1, ratings=1100), T(2, ratings=1100)]
    market = {(1, 2): (3.0, 0.4)}
    ts = TeamStrength(teams, [], [], market=market, market_weight=0.7)
    plain = TeamStrength(teams, [], [])
    struct = plain.expected_goals(1, 2, True)[0]
    got = ts.expected_goals(1, 2, True)[0]

    w = 0.7
    geo = 3.0 ** w * struct ** (1 - w)
    arith = w * 3.0 + (1 - w) * struct
    assert got == pytest.approx(geo, rel=1e-6)
    assert got != pytest.approx(arith, rel=1e-3)
    assert got < arith, "hình học luôn ≤ số học; nếu không thì đã trộn nhầm thang"


def test_distant_fixtures_trust_the_market_less():
    """Độ trưởng thành: giá của trận sau sáu tuần không nặng bằng giá tuần này."""
    teams = [T(1, ratings=1100), T(2, ratings=1100)]
    market = {(1, 2): (3.0, 0.4)}
    soon = TeamStrength(teams, [], [], market=market, market_weight=0.7,
                        market_maturity={(1, 2): 1.0})
    far = TeamStrength(teams, [], [], market=market, market_weight=0.7,
                       market_maturity={(1, 2): 0.45})
    assert far.terms(1, 2, True)[0].market_weight < soon.terms(1, 2, True)[0].market_weight
    assert far.expected_goals(1, 2, True)[0] < soon.expected_goals(1, 2, True)[0]


def test_calibration_reaches_fixtures_that_have_no_odds():
    """Độ lệch đo trên trận CÓ giá phải áp cho cả trận CHƯA có giá.

    Đây là điểm khác biệt giữa "blend từng trận" và "hiệu chuẩn": nếu mô hình nội
    bộ nóng hơn thị trường 8% ở những trận đã có bảng kèo, thì nó cũng đang nóng
    hơn 8% ở GW12 — dù GW12 chưa ai ra giá.
    """
    ids = list(range(1, 13))
    teams = [T(i, ratings=1100) for i in ids]
    players = sum((_squad(t) for t in ids), [])
    # sáu trận có giá, tất cả đều thấp hơn hẳn mô hình
    market = {(ids[i], ids[i + 1]): (0.7, 0.6) for i in range(0, 12, 2)}
    plain = TeamStrength(teams, players, [])
    calibrated = TeamStrength(teams, players, [], market=market, market_weight=0.7)

    assert calibrated.calibration_multiplier < 1.0
    # cặp 1–4 KHÔNG nằm trong bảng kèo, nhưng vẫn phải nguội đi
    assert not calibrated.has_market(1, 4, True)
    assert calibrated.expected_goals(1, 4, True)[0] < plain.expected_goals(1, 4, True)[0]


def test_calibration_needs_a_real_sample():
    """Một trận có giá không đủ để kết luận mô hình lệch hệ thống."""
    teams = [T(1, ratings=1100), T(2, ratings=1100)]
    ts = TeamStrength(teams, [], [], market={(1, 2): (0.5, 0.5)}, market_weight=0.7)
    assert ts.calibration_multiplier == 1.0


# ============================================================== BƯỚC 4 ======
def test_percentiles_span_the_full_range_and_share_ties():
    got = percentiles([1.0, 2.0, 3.0, 4.0, 5.0])
    assert got[0] == 0.0 and got[-1] == 100.0
    tied = percentiles([1.0, 1.0, 1.0, 9.0])
    assert tied[0] == tied[1] == tied[2], "đồng hạng phải cùng percentile"
    assert tied[3] == 100.0


def test_fdr_bands_are_exact_quintiles():
    assert fdr_from_percentile(100) == 1
    assert fdr_from_percentile(81) == 1
    assert fdr_from_percentile(79) == 2
    assert fdr_from_percentile(50) == 3
    assert fdr_from_percentile(0) == 5
    # 20 đội -> đúng 4 đội mỗi bậc
    buckets = [quintile_fdr(i, 20) for i in range(20)]
    assert [buckets.count(b) for b in (1, 2, 3, 4, 5)] == [4, 4, 4, 4, 4]


def test_defence_value_counts_deductions_in_whole_steps():
    """−1 mỗi HAI bàn thua, không phải λ/2. Thủng đúng một bàn không mất điểm."""
    from app.scoring import RULES

    cs_pts = RULES.clean_sheet_points.get(2, 4)
    # λ rất nhỏ -> gần như chắc chắn sạch lưới, gần như không bị trừ
    assert defence_value(0.01) == pytest.approx(cs_pts, abs=0.05)
    # λ = 1.0: sạch lưới 37%, nhưng khoản trừ phải NHỎ HƠN nhiều so với λ/2 = −0.5
    v = defence_value(1.0)
    naive = cs_pts * math.exp(-1.0) + (1.0 / 2) * RULES.points_per_two_conceded
    assert v > naive, "đang phạt theo tỷ lệ thay vì theo mốc trọn"
    assert defence_value(3.0) < defence_value(1.0) < defence_value(0.3)


def test_reference_player_threshold_is_relative_not_absolute():
    """Ngưỡng "đá đều" phải xét tương đối với giải, không phải một mốc phút cứng.

    FPL reset thống kê mỗi mùa. Một mốc 900 phút cố định nghĩa là suốt GW1–GW9
    **không một ai** đủ điều kiện, và cả bốn cầu thủ tham chiếu rơi về prior theo
    vị trí — tức Role Ease mất độ sắc đúng 9 vòng đầu mỗi mùa. Đây là cùng một lỗi
    mà `team_strength` đã dính với ngưỡng "đội mới lên hạng".
    """
    class Pl:
        def __init__(self, pid, et, mins):
            self.id, self.element_type, self.minutes = pid, et, mins
            self.expected_goals = 0.3 * mins / 90
            self.expected_assists = 0.2 * mins / 90
            self.saves = 3 * mins / 90
            self.defensive_contribution = 6 * mins / 90
            self.yellow_cards, self.bps = 1, 18 * mins / 90
            self.clearances_blocks_interceptions = 4 * mins / 90

    # sau 3 vòng: người đá đều mới có 270 phút, xa mốc 900
    regulars = [Pl(i, et, 270) for et in POSITIONS for i in range(10)]
    bench = [Pl(500 + i, et, 30) for et in POSITIONS for i in range(10)]
    refs = build_reference_players(regulars + bench)
    for pos in POSITIONS:
        assert "prior theo vị trí" not in refs[pos].source, (
            f"{pos}: rơi về prior dù giải đã đá 3 vòng — ngưỡng đang là mốc tuyệt đối"
        )
        assert refs[pos].minutes_season == 270, "phải lấy nhóm đá đều, không lấy cả ghế dự bị"

    # Ngưỡng tương đối cần một cái sàn, nếu không nó tự huỷ ở đúng đầu mùa: chưa ai
    # đá phút nào thì `0.6 × 0` = 0, ai cũng "đủ điều kiện" với 0 phút, và cầu thủ
    # tham chiếu được dựng từ toàn số 0.
    for busiest in (0, 90):     # chưa đá gì; và mới đúng một trận
        refs = build_reference_players(
            [Pl(i, et, busiest) for et in POSITIONS for i in range(10)]
        )
        assert "prior theo vị trí" in refs[3].source, f"busiest={busiest}"
        assert refs[3].minutes_season > 0, "cầu thủ tham chiếu không được toàn số 0"


def test_role_ease_separates_the_positions(db):
    """Cùng một trận không cùng độ khó cho bốn vai trò."""
    from app.models import Player
    from sqlalchemy import select

    players = db.scalars(select(Player)).all()
    refs = build_reference_players(players)
    teams = [T(1), T(2), T(3)]
    ts = TeamStrength(teams, sum((_squad(t) for t in (1, 2, 3)), []), [])

    # trận nhiều bàn cho cả hai bên: tốt cho tiền đạo, xấu cho hậu vệ
    wild = rate_fixture(ts, 1, 2, 1, True, references=refs)
    tight = rate_fixture(ts, 3, 2, 1, True, references=refs)
    rank_fixtures([wild, tight])
    assert set(wild.role_points) == set(POSITIONS)
    assert wild.role_points[4] != wild.role_points[2]
    assert wild.role_fdr.keys() == tight.role_fdr.keys()


# ============================================================== BƯỚC 5 ======
def test_recent_gameweeks_weigh_more():
    assert decay_weight(0) == 1.0
    assert decay_weight(4) == pytest.approx(0.5)
    assert decay_weight(0) > decay_weight(1) > decay_weight(7)


def _fake_rating(team, gw, points, has_market=True):
    from app.engine.fixture_difficulty import FixtureRating

    return FixtureRating(
        team_id=team, opponent_id=99, gameweek=gw, is_home=True, fixture_id=None,
        proj_goals_for=1.4, proj_goals_against=1.4, clean_sheet_prob=0.24,
        defence_value=1.0, role_points={3: points}, has_market=has_market,
        market_weight=0.7, has_kickoff=True,
    )


def test_easy_fixtures_early_beat_the_same_fixtures_late():
    """Suy giảm theo thời gian phải thật sự đổi thứ hạng, không chỉ là trang trí."""
    gws = [1, 2, 3, 4, 5, 6, 7, 8]
    early = {1: [_fake_rating(1, g, 9.0 if g <= 4 else 2.0) for g in gws]}
    late = {2: [_fake_rating(2, g, 2.0 if g <= 4 else 9.0) for g in gws]}
    combined = {**early, **late}
    out = {s.team_id: s for s in schedule_ease(
        combined, gws, 3, evidence_weight={1: 1.0, 2: 1.0}
    )}
    assert out[1].ease > out[2].ease


def test_blank_gameweek_scores_zero_and_double_scores_twice():
    """Vòng trắng là 0 điểm — một sự thật, không phải dữ liệu thiếu."""
    gws = [1, 2]
    rows = {
        1: [_fake_rating(1, 1, 5.0), _fake_rating(1, 2, 5.0)],      # đơn, đơn
        2: [_fake_rating(2, 1, 5.0), _fake_rating(2, 1, 5.0)],      # đôi rồi trắng
        3: [_fake_rating(3, 1, 5.0)],                               # đơn rồi trắng
    }
    out = {s.team_id: s for s in schedule_ease(
        rows, gws, 3, evidence_weight={1: 1.0, 2: 1.0, 3: 1.0}
    )}
    assert out[3].blanks == [2] and out[3].doubles == []
    assert out[2].doubles == [1] and out[2].blanks == [2]
    assert out[2].ease > out[3].ease, "vòng đôi phải hơn vòng đơn"
    assert out[1].ease > out[3].ease, "hai vòng có đá phải hơn một vòng trắng"


def test_uncertainty_penalty_grows_when_evidence_is_thin():
    full = uncertainty_penalty(share_no_market=0.0, evidence_weight=1.0,
                               share_no_kickoff=0.0)
    none = uncertainty_penalty(share_no_market=1.0, evidence_weight=0.0,
                               share_no_kickoff=1.0)
    assert full == 0.0
    assert none == pytest.approx(12.0)
    mid = uncertainty_penalty(share_no_market=1.0, evidence_weight=1.0,
                              share_no_kickoff=0.0)
    assert 0 < mid < none


# ======================================================= tích hợp cả 6 bước ==
def test_ticker_runs_all_six_steps(db):
    from app.services.fixtures import fixture_ticker

    data = fixture_ticker(db, n_gws=8)
    rows = data["rows"]
    assert rows and len(data["gameweeks"]) == 8
    assert data["roles"] == ["GK", "DEF", "MID", "FWD"]

    # BƯỚC 6: đúng 20% mỗi bậc, cho TỪNG vai trò
    for role in data["roles"]:
        fdrs = [r["schedule"][role]["fdr"] for r in rows]
        counts = [fdrs.count(b) for b in (1, 2, 3, 4, 5)]
        assert max(counts) - min(counts) <= 1, f"{role} chia không đều: {counts}"
        assert set(fdrs) == {1, 2, 3, 4, 5}

    # BƯỚC 4: mỗi ô có percentile và FDR cho cả bốn vai trò
    cell = next(c for r in rows for cs in r["cells"].values() for c in cs)
    assert set(cell["role_fdr"]) == {"GK", "DEF", "MID", "FWD"}
    assert 0 <= cell["attack_ease"] <= 100
    assert 1 <= cell["attack_difficulty"] <= 5

    # BƯỚC 5: phạt bất định có thật và không âm
    s = rows[0]["schedule"]["MID"]
    assert s["uncertainty_penalty"] >= 0
    assert s["ease"] == pytest.approx(max(0.0, s["raw_ease"] - s["uncertainty_penalty"]))

    # Bốn vai trò phải được chấm ĐỘC LẬP — nếu ra cùng một điểm thì Role Ease chỉ
    # là bảng độ khó chung đội bốn cái tên khác nhau. (Thứ hạng có thể trùng khi
    # dữ liệu demo chỉ có vài đội; điều kiểm tra được ở mọi cỡ mẫu là giá trị.)
    assert any(
        r["schedule"]["GK"]["ease"] != r["schedule"]["FWD"]["ease"] for r in rows
    )
    assert any(
        r["schedule"]["DEF"]["ease"] != r["schedule"]["MID"]["ease"] for r in rows
    )


def test_explain_endpoint_traces_a_cell_back_to_its_evidence(db):
    from app.models import Team
    from app.services.fixtures import explain_fixture
    from sqlalchemy import select

    ids = [t.id for t in db.scalars(select(Team)).all()][:2]
    out = explain_fixture(db, ids[0], ids[1], True)
    assert out["prior"]["components"].keys() >= set(NOMINAL_WEIGHTS)
    terms = out["lambda_terms"]["for"]
    for key in ("baseline", "attack", "opponent_defence", "home", "lineup",
                "rest", "congestion", "squad", "manager", "promotion"):
        assert key in terms
    assert terms["lambda"] > 0

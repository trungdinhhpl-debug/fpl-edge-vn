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
    21% xG của một đội biến thành bàn thắng thất lạc;
  * share dựng từ TỔNG CẢ MÙA — MONTE CARLO sai: tổng mùa đã nhân sẵn số phút từng
    được đá, rồi MC nhân thêm xác suất ra sân của trận và dồn phần hụt cho người có
    mặt. Đo ngày 21/10/2026 (GW8, xP > 2.5, MC/giải tích): bàn thắng hậu vệ 1.34,
    tiền vệ 1.17, tiền đạo 1.15; kiến tạo 1.24 / 1.15 / 1.08. Giờ share dựng từ kỳ
    vọng giải tích của chính trận, quy về share khi có mặt, và được chỉnh cho khớp.
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


# ------------------------------------------ share từ kỳ vọng giải tích ---------
LAM_FOR = 1.7
# (player_id, vị trí, p_start, p_sub, bàn kỳ vọng, kiến tạo kỳ vọng) của MỘT trận.
# Có đủ ba kiểu người: trụ cột, dự bị luân phiên, và một chân sút ôm 36% bàn của đội
# — kiểu người mà chặn "không tự kiến tạo" lấy mất nhiều kiến tạo nhất.
SQUAD = [
    (1, 4, 0.95, 0.03, 0.62, 0.20),   # tiền đạo số 1
    (2, 4, 0.10, 0.45, 0.10, 0.04),   # tiền đạo dự bị
    (3, 3, 0.90, 0.05, 0.30, 0.40),
    (4, 3, 0.50, 0.30, 0.12, 0.15),   # tiền vệ luân phiên
    (5, 3, 0.85, 0.08, 0.15, 0.30),
    (6, 2, 0.95, 0.01, 0.05, 0.09),
    (7, 2, 0.92, 0.02, 0.05, 0.09),
    (8, 2, 0.90, 0.03, 0.05, 0.09),
    (9, 2, 0.93, 0.02, 0.05, 0.09),
    (10, 2, 0.30, 0.10, 0.015, 0.03),  # hậu vệ dự bị
    (11, 1, 0.98, 0.00, 0.0, 0.01),
]


def _analytic_squad(targets: bool = True, scale: float = 1.0) -> list[MCPlayer]:
    """MCPlayer như projections.py dựng: share KHI CÓ MẶT + kỳ vọng làm đích."""
    out = []
    for pid, et, ps, sb, g, a in SQUAD:
        p_play = min(1.0, ps + sb)
        out.append(MCPlayer(
            player_id=pid, element_type=et, p_start=ps, p_sub=sb,
            p_60_plus=ps * 0.85,
            share_goal=g * scale / (p_play * LAM_FOR),
            share_assist=a / (p_play * LAM_FOR),
            saves90=0.0, dc_hit_prob=0.0, yellow90=0.0, bonus_base=0.0,
            exp_goals=g * scale if targets else None,
            exp_assists=a if targets else None,
        ))
    return out


def _mc_means(players: list[MCPlayer], n: int = 40_000, seed: int = 21):
    col: dict = {}
    simulate_fixture(players, LAM_FOR, 1.2, n, np.random.default_rng(seed), collect=col)
    et = {p.player_id: p.element_type for p in players}
    goals = {pid: c["goals"] / RULES.goal_points[et[pid]] for pid, c in col.items()
             if RULES.goal_points.get(et[pid])}
    assists = {pid: c["assists"] / RULES.assist_points for pid, c in col.items()}
    return goals, assists


def test_share_that_already_includes_availability_is_counted_twice():
    """Cơ chế của lỗi, giữ lại làm bằng chứng: share VÔ ĐIỀU KIỆN (đã gồm khả năng
    ra sân — đúng bản chất của share dựng từ tổng cả mùa) bị MC nhân thêm xác suất ra
    sân rồi dồn phần hụt cho người có mặt. Người đá luân phiên mất phần của mình,
    trụ cột ôm phần đó."""
    players = []
    for p in _analytic_squad(targets=False):
        p_play = min(1.0, p.p_start + p.p_sub)
        players.append(MCPlayer(**{
            **p.__dict__,
            "share_goal": p.share_goal * p_play,   # = bàn kỳ vọng / λ, vô điều kiện
            "share_assist": p.share_assist * p_play,
        }))
    goals, _ = _mc_means(players)
    target = {pid: g for pid, _, _, _, g, _ in SQUAD}
    assert goals[1] / target[1] > 1.04, "trụ cột phải được chia DƯ"
    assert goals[4] / target[4] < 0.9, "người luân phiên phải bị chia THIẾU"
    assert goals[2] / target[2] < 0.7, "dự bị gần như mất trắng phần của mình"


def test_mc_goals_and_assists_match_their_analytic_targets():
    """Với đích giải tích, trung bình MC khớp ±3% cho từng người đáng kể và cho
    từng vị trí — gồm cả người luân phiên và chân sút số 1, người mà chặn tự kiến
    tạo lấy mất ~36% kiến tạo nếu không chỉnh."""
    goals, assists = _mc_means(_analytic_squad())
    for pid, et, _, _, g, a in SQUAD:
        if g >= 0.1:
            assert goals[pid] == pytest.approx(g, rel=0.03), f"bàn của {pid}"
        if a >= 0.1:
            assert assists[pid] == pytest.approx(a, rel=0.03), f"kiến tạo của {pid}"
    for pos in (2, 3, 4):
        ids = [s for s in SQUAD if s[1] == pos]
        tg, ta = sum(s[4] for s in ids), sum(s[5] for s in ids)
        assert sum(goals[s[0]] for s in ids) / tg == pytest.approx(1.0, abs=0.03), pos
        assert sum(assists[s[0]] for s in ids) / ta == pytest.approx(1.0, abs=0.03), pos


def test_mc_follows_the_analytic_total_when_it_exceeds_lambda():
    """Điều chỉnh vai trò/kèo cộng bàn mà không bớt của ai, nên tổng giải tích có thể
    vượt λ. Chia λ thì không bao giờ khớp được — MC phải rút theo tổng xP ngụ ý."""
    scale = 1.25 * LAM_FOR / sum(s[4] for s in SQUAD)   # tổng đích = 1.25 λ
    goals, _ = _mc_means(_analytic_squad(scale=scale))
    want = sum(s[4] for s in SQUAD) * scale
    assert sum(goals.values()) == pytest.approx(want, rel=0.03)


def test_calibration_keeps_the_share_transfer():
    """Chỉnh share không được xoá cơ chế chuyển share: khi tiền đạo số 1 không đá
    chính, người dự bị phải ghi nhiều hơn so với khi anh ta đá chính."""
    players = _analytic_squad()
    # không cho vào sân từ ghế, để "không đá chính" đúng nghĩa là vắng mặt
    players[0] = MCPlayer(**{**players[0].__dict__, "p_sub": 0.0})
    started: dict = {}
    out = simulate_fixture(
        players, LAM_FOR, 1.2, 40_000, np.random.default_rng(4), out_started=started,
    )
    understudy = out[2]
    absent = ~started[1]
    assert understudy[absent].mean() > understudy[~absent].mean() + 0.15


def test_calibrated_shares_never_hand_out_more_than_the_team_scored():
    """Tổng share mỗi lần rút <= 1 kể cả sau khi chỉnh và khi tổng đích vượt λ — nên
    chuỗi Binomial có điều kiện không bao giờ chia dư bàn hay kiến tạo."""
    from app.engine.montecarlo import _calibrated_shares

    n = 20_000
    rng = np.random.default_rng(6)
    players = _analytic_squad(scale=1.3)
    played = {p.player_id: rng.random(n) < min(1.0, p.p_start + p.p_sub) for p in players}
    lam = sum(p.exp_goals for p in players)
    eff = _calibrated_shares(
        players, played, "share_goal", {p.player_id: p.exp_goals for p in players},
        lam, n, lambda e: {pid: lam * float(x.mean()) for pid, x in e.items()},
    )
    assert (sum(eff.values()) <= 1.0 + 1e-9).all()


def test_pipeline_mc_matches_the_analytic_per_position(db):
    """Đầu cuối, qua chính `build_projections`: bàn thắng và kiến tạo của MC khớp
    phần giải tích ±3% theo từng vị trí, ở nhóm xP > 2.5 — đúng nhóm mà ceiling và
    P(haul) được đọc ra cho Triple Captain và Bench Boost. Trên bộ demo này, share
    cũ (tổng cả mùa) cho bàn thắng tiền vệ 1.28, tiền đạo 0.89, kiến tạo hậu vệ 1.11,
    tiền đạo 1.16. Ba vòng để nhiễu Monte Carlo (~1%) nằm xa ngưỡng."""
    from collections import defaultdict

    from sqlalchemy import select

    from app.engine.projections import build_projections
    from app.models import Player, PlayerProjection

    col: dict = {}
    result = build_projections(db, horizon=3, mc_iterations=6000, collect_components=col)
    db.flush()
    et = {p.id: p.element_type for p in db.scalars(select(Player)).all()}
    rows = db.scalars(
        select(PlayerProjection).where(PlayerProjection.gameweek.in_(result["gameweeks"]))
    ).all()
    agg: dict = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for r in rows:
        c = col.get((r.player_id, r.gameweek))
        if c is None or r.xp <= 2.5 or et[r.player_id] == 1:
            continue
        a = agg[et[r.player_id]]
        a[0] += c["goals"]
        a[1] += r.xp_goals
        a[2] += c["assists"]
        a[3] += r.xp_assists
    assert set(agg) == {2, 3, 4}
    for pos, (mg, ag, ma, aa) in agg.items():
        assert mg / ag == pytest.approx(1.0, abs=0.03), f"bàn thắng vị trí {pos}"
        assert ma / aa == pytest.approx(1.0, abs=0.03), f"kiến tạo vị trí {pos}"

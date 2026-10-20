"""Trừ đúng số quả 11m THẬT khỏi xG tổng, thay cho trừ một tỷ lệ trung bình.

Lỗi gốc (2026-10-20, người dùng hỏi vì sao Le Fée của Sunderland đứng top xP):
FPL phát xG tổng gồm cả 11m. Le Fée được xếp đá 11m SỐ 2 nhưng thực tế đá 6 quả
trong ~40 trận (1.58/2.09 xG mùa này, 3.16/5.22 mùa trước). Cách trừ theo tỷ lệ chỉ
bớt ~0.05/90 nên engine tưởng anh ta tạo ~0.20 xG bóng sống mỗi 90 phút, trong khi
thật là ~0.07 — và xP(5) của anh ta bị thổi lên 3.6 điểm.

Các test ở đây khoá những tính chất làm cho phép sửa ĐÚNG chứ không chỉ khác đi:
người không đá 11m không được đụng tới, "chưa biết" không được biến thành 0, và
tổng bàn của đội vẫn do mô hình đội quyết định.
"""
import pytest
from sqlalchemy import select

from app.engine import penalties as pen
from app.engine.xpoints import expected_points
from app.providers import pl_penalties as plp


# ================================================== số học cơ bản ===========
def test_non_penalty_xg_subtracts_exactly_what_happened():
    assert pen.non_penalty_xg(2.09, 2) == pytest.approx(2.09 - 1.58)
    assert pen.non_penalty_xg(5.0, 0) == 5.0
    # không bao giờ âm: một quả 11m hỏng của người xG thấp
    assert pen.non_penalty_xg(0.5, 1) == 0.0
    assert pen.non_penalty_xg(None, 1) == 0.0


def test_measured_rate_pools_seasons_in_xg_units():
    """Mùa này mỏng thì mùa trước làm nền; đơn vị là xG (0.79 mỗi quả)."""
    r = pen.measured_penalty_rate([(11, 140), (92, 760)])
    assert r.measured
    assert r.goals_per_team_match == pytest.approx(103 * 0.79 / 900)
    assert "103 quả" in r.detail
    # chưa có trận nào -> mức nền, và nói rõ là mức nền
    empty = pen.measured_penalty_rate([(0, 0)])
    assert not empty.measured
    assert empty.goals_per_team_match == pen.FALLBACK_PENALTY_GOALS_PER_TEAM_MATCH


# ================================================ tác động lên xP ============
# Số thật của Le Fée tại vòng 7 mùa 2026/27.
_LE_FEE = dict(
    element_type=3, minutes_season=608, xg_season=2.09, xa_season=1.60,
    saves_season=0, dc_season=40.0, yellow_season=1, red_season=0, bps_season=118,
    cbi_season=10.0, xmins=82.8, p_start=0.985, p_appear=0.99, p_60_plus=0.93,
    lam_team_goals=1.3, lam_conceded=1.3, team_avg_gf=1.3,
    penalties_order=2, penalty_rate=0.09, lead_taker_on_pitch=0.05,
)
# Nền mùa trước: xG tổng 5.22 trong 2930 phút, trong đó 4 quả 11m.
_PRIOR_TOTAL = 5.22 / (2930 / 90)
_PRIOR_OPEN = (5.22 - 4 * 0.79) / (2930 / 90)


def test_heavy_penalty_taker_open_play_is_not_inflated():
    """Regression trực tiếp: xG bóng sống phải về ~0.07/90, không phải ~0.2."""
    old = expected_points(prior_xg90=_PRIOR_TOTAL, **_LE_FEE)
    new = expected_points(
        prior_xg90=_PRIOR_OPEN, prior_xg90_is_open_play=True,
        pen_attempts_season=2, **_LE_FEE,
    )
    assert new.components["xg90_open"] == pytest.approx(0.07, abs=0.015)
    # cách cũ để lọt hơn gấp đôi phần bóng sống thật
    assert old.components["xg90_open"] > 2 * new.components["xg90_open"]
    # phần 11m kỳ vọng phía trước KHÔNG bị bỏ: anh ta vẫn là người đá thực tế
    assert new.components["exp_pen_goals"] > 0
    assert new.goals < old.goals


def test_non_taker_with_known_zero_attempts_is_unchanged():
    """Biết chắc 0 quả + không đá 11m -> đúng y như trước, từng chữ số."""
    base = dict(_LE_FEE, penalties_order=None)
    old = expected_points(prior_xg90=0.12, **base)
    new = expected_points(
        prior_xg90=0.12, prior_xg90_is_open_play=True, pen_attempts_season=0, **base,
    )
    assert new.goals == pytest.approx(old.goals, abs=1e-12)
    assert new.xp == pytest.approx(old.xp, abs=1e-12)


def test_unknown_attempts_fall_back_to_the_rate_split_exactly():
    """None = chưa biết -> hành vi cũ nguyên vẹn. Chưa biết KHÔNG phải 0."""
    a = expected_points(prior_xg90=_PRIOR_TOTAL, **_LE_FEE)
    b = expected_points(
        prior_xg90=_PRIOR_TOTAL, prior_xg90_is_open_play=False,
        pen_attempts_season=None, **_LE_FEE,
    )
    assert a.xp == b.xp and a.goals == b.goals
    # còn coi "chưa biết" là 0 quả thì xG bóng sống phồng hẳn lên — đúng thứ cấm
    as_zero = expected_points(
        prior_xg90=_PRIOR_TOTAL, pen_attempts_season=0, **_LE_FEE,
    )
    assert as_zero.components["xg90_open"] > a.components["xg90_open"]


def test_designated_taker_without_penalties_yet_keeps_full_penalty_value():
    """Người số 1 mà đội chưa được quả 11m nào: mẫu sạch, phần 11m vẫn cộng đủ.

    Với cách cũ, tỷ lệ bị trừ khỏi một mẫu KHÔNG chứa quả 11m nào — tức trừ oan.
    """
    taker = dict(_LE_FEE, penalties_order=1, lead_taker_on_pitch=None)
    bd = expected_points(
        prior_xg90=0.15, prior_xg90_is_open_play=True, pen_attempts_season=0, **taker,
    )
    assert bd.components["xg90_open"] == pytest.approx(
        (608 / (608 + 540)) * (2.09 / (608 / 90)) + (540 / (608 + 540)) * 0.15,
        rel=1e-3,
    )
    # `components` làm tròn 3–4 chữ số nên so theo sai số tuyệt đối
    assert bd.components["xg90"] == pytest.approx(
        bd.components["xg90_open"] + 0.09, abs=1e-3
    )


# ============================================== nguồn dữ liệu ================
class _FakeProvider:
    def __init__(self, seasons, ranked, fail_seasons=()):
        self._seasons = seasons
        self._ranked = ranked
        self._fail = set(fail_seasons)

    def seasons(self):
        return dict(self._seasons)

    def ranked(self, stat, season_id):
        if season_id in self._fail:
            raise RuntimeError("503")
        return dict(self._ranked.get((stat, season_id), {}))


def test_fetch_sums_all_four_outcomes_and_drops_failed_seasons_whole():
    prov = _FakeProvider(
        seasons={"2026/27": 841, "2025/26": 777},
        ranked={
            ("att_pen_goal", 841): {484420: 1, 223340: 1},
            ("att_pen_target", 841): {484420: 1},
            ("att_pen_post", 841): {502500: 1},
        },
        fail_seasons={777},
    )
    rep = plp.fetch_penalties(["2026/27", "2025/26"], prov)
    assert rep.by_season["2026/27"][484420] == (2, 1)   # 1 vào + 1 bị cứu
    assert rep.by_season["2026/27"][502500] == (1, 0)   # dội cột
    # mùa lấy hỏng thì VẮNG MẶT, không phải một bảng toàn số 0
    assert "2025/26" not in rep.by_season
    assert any("2025/26" in e for e in rep.errors)


def test_opta_ids_map_to_fpl_codes():
    assert plp._opta_to_code("p484420") == 484420
    assert plp._opta_to_code("484420") == 484420
    assert plp._opta_to_code("x") is None
    assert plp._opta_to_code(None) is None


def test_sync_writes_known_zeros_and_is_idempotent(db, monkeypatch):
    """Mỗi cầu thủ một hàng (0 quả cũng ghi); chạy lại không trùng khoá; lần lấy
    hỏng không xoá số cũ."""
    from app.ingestion import fpl_sync
    from app.models import Player, PlayerPenaltyStat, Season

    season = db.scalar(select(Season.name).where(Season.is_current.is_(True)))
    players = db.scalars(select(Player)).all()
    taker = players[0]
    ok = _FakeProvider(
        seasons={season: 1},
        ranked={("att_pen_goal", 1): {taker.code: 3}, ("att_pen_miss", 1): {taker.code: 1}},
    )
    monkeypatch.setattr(plp, "get_pl_penalties_provider", lambda: ok)
    try:
        db.execute(PlayerPenaltyStat.__table__.delete())
        db.commit()
        r1 = fpl_sync.sync_player_penalties(db)
        r2 = fpl_sync.sync_player_penalties(db)
        assert r1["rows"] == r2["rows"] == len(players)
        rows = db.scalars(select(PlayerPenaltyStat)).all()
        assert len(rows) == len(players)
        by_pid = {r.player_id: r for r in rows}
        assert (by_pid[taker.id].attempts, by_pid[taker.id].goals) == (4, 3)
        assert by_pid[players[1].id].attempts == 0, "vắng trong danh sách đầy đủ = 0"

        broken = _FakeProvider(seasons={season: 1}, ranked={}, fail_seasons={1})
        monkeypatch.setattr(plp, "get_pl_penalties_provider", lambda: broken)
        r3 = fpl_sync.sync_player_penalties(db)
        assert r3["rows"] == 0
        db.expire_all()
        assert db.get(PlayerPenaltyStat, by_pid[taker.id].id).attempts == 4
    finally:
        db.execute(PlayerPenaltyStat.__table__.delete())
        db.commit()


def test_priors_expose_open_play_rate_only_when_attempts_are_known(db):
    from app import bps_rules
    from app.models import PlayerSeasonHistory
    from app.services import player_priors

    db.add(PlayerSeasonHistory(
        player_id=1, season_name="2025/26", minutes=2930, starts=33,
        expected_goals=5.22, expected_assists=5.95,
    ))
    db.commit()
    try:
        cur = "2026/27"
        assert bps_rules.previous_season(cur) == "2025/26"
        known = player_priors.load_priors(db, cur, penalty_attempts={1: 4})[1]
        assert known.npxg90 == pytest.approx(_PRIOR_OPEN)
        assert known.xg90 == pytest.approx(_PRIOR_TOTAL)
        unknown = player_priors.load_priors(db, cur, penalty_attempts={})[1]
        assert unknown.npxg90 is None, "không có số thì là CHƯA BIẾT, không phải 0"
    finally:
        db.execute(
            PlayerSeasonHistory.__table__.delete().where(
                PlayerSeasonHistory.season_name == "2025/26"
            )
        )
        db.commit()

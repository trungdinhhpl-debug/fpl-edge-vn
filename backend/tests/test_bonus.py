"""Bonus — khoá luật bảo toàn và hai chỗ đã thực sự sai một lần.

Bonus là chỗ duy nhất trong engine có một **luật bảo toàn kiểm tra được**: mỗi trận
FPL phát đúng 6 điểm. Bản trước không có ràng buộc đó và trôi xuống 2.47 điểm/trận
mà không test nào bắt được — nên nhóm test đầu tiên ở đây kiểm chính luật đó.

Hai lỗi đã xảy ra trong lúc viết, cả hai đều được khoá lại bên dưới:
  * thứ tự BPS bàn thắng bị ghi NGƯỢC (tiền đạo thấp nhất thay vì cao nhất), làm
    tiền đạo bị chia hụt một nửa bonus;
  * `standalone_bonus` áp số mũ lên cả số phút, làm người đá nửa trận nhận 1/4.
"""
import pytest

from app.engine import bonus as bonus_mod
from app.engine.bonus import (
    BONUS_POOL_PER_MATCH,
    CONCENTRATION,
    GOAL_BPS,
    BonusEntry,
    allocate,
    expected_fixture_bps,
    standalone_bonus,
)


def _fixture(n_per_team: int = 11, bps: float = 20.0) -> list[BonusEntry]:
    return [BonusEntry(player_id=i, expected_bps=bps) for i in range(2 * n_per_team)]


def test_allocation_conserves_the_six_point_pool():
    """Tổng bonus mỗi trận phải bằng đúng 6 — đây là luật, không phải tham số."""
    for entries in (
        _fixture(),
        [BonusEntry(player_id=i, expected_bps=float(i)) for i in range(1, 23)],
        [BonusEntry(player_id=i, expected_bps=5.0 + 3 * i) for i in range(22)],
    ):
        out = allocate(entries)
        assert sum(out.values()) == pytest.approx(BONUS_POOL_PER_MATCH, abs=1e-6)


def test_allocation_conserves_the_pool_even_when_the_cap_binds():
    """Một cầu thủ áp đảo bị chặn ở 3 điểm, phần vượt phải chia lại chứ không mất."""
    entries = [BonusEntry(player_id=0, expected_bps=500.0)] + [
        BonusEntry(player_id=i, expected_bps=1.0) for i in range(1, 22)
    ]
    out = allocate(entries, max_bonus=3.0)
    assert out[0] == pytest.approx(3.0, abs=1e-6)          # chạm trần
    assert sum(out.values()) == pytest.approx(BONUS_POOL_PER_MATCH, abs=1e-6)
    assert all(v <= 3.0 + 1e-9 for v in out.values())


def test_allocation_is_zero_when_nobody_has_expected_bps():
    out = allocate([BonusEntry(player_id=i, expected_bps=0.0) for i in range(22)])
    assert sum(out.values()) == 0.0


def test_allocation_is_concentrated_not_proportional():
    """BPS gấp đôi phải cho bonus nhiều hơn gấp đôi — đó là dấu vết cơ chế top-3."""
    entries = [BonusEntry(player_id=0, expected_bps=40.0)] + [
        BonusEntry(player_id=i, expected_bps=20.0) for i in range(1, 22)
    ]
    out = allocate(entries)
    ratio = out[0] / out[1]
    assert ratio > 2.0, "chia theo tỷ lệ tuyến tính thì bỏ mất cơ chế top-3"
    assert ratio == pytest.approx(2.0 ** CONCENTRATION, rel=0.01)


def test_goal_bps_order_is_forward_highest():
    """Chỗ này đã ghi NGƯỢC một lần và làm tiền đạo hụt 50% bonus.

    BPS cho bàn thắng ngược thang điểm FPL: tiền đạo được NHIỀU nhất (24), thủ môn
    và hậu vệ ít nhất (12). Thang điểm FPL thì ngược lại (thủ môn 10, tiền đạo 4)
    vì nó bù cho việc hậu vệ ghi bàn hiếm.
    """
    assert GOAL_BPS[4] > GOAL_BPS[3] > GOAL_BPS[2]
    assert GOAL_BPS[4] == 24.0
    assert GOAL_BPS[3] == 18.0
    assert GOAL_BPS[2] == GOAL_BPS[1] == 12.0

    # và nó phải chảy vào BPS kỳ vọng: cùng một kỳ vọng bàn thắng, tiền đạo hơn
    common = dict(bps90=0.0, minutes_frac=1.0, exp_goals=0.5)
    fwd = expected_fixture_bps(**common, element_type=4)
    dfd = expected_fixture_bps(**common, element_type=2)
    assert fwd > dfd


def test_expected_fixture_bps_starts_from_the_players_own_rate():
    """Nền phải là bps90 × số phút; không có hành động riêng thì bằng đúng cái đó."""
    got = expected_fixture_bps(bps90=24.0, minutes_frac=0.5, element_type=3)
    assert got == pytest.approx(12.0, abs=1e-9)

    # đá nhiều hơn thì BPS kỳ vọng cao hơn, tuyến tính theo số phút
    half = expected_fixture_bps(bps90=24.0, minutes_frac=0.5, element_type=3)
    full = expected_fixture_bps(bps90=24.0, minutes_frac=1.0, element_type=3)
    assert full == pytest.approx(2 * half, abs=1e-9)


def test_clean_sheet_bps_only_for_keepers_and_defenders():
    common = dict(bps90=0.0, minutes_frac=1.0, cs_prob=1.0, p_60_plus=1.0)
    assert expected_fixture_bps(**common, element_type=1) > 0
    assert expected_fixture_bps(**common, element_type=2) > 0
    assert expected_fixture_bps(**common, element_type=3) == 0.0
    assert expected_fixture_bps(**common, element_type=4) == 0.0


def test_standalone_bonus_scales_linearly_with_minutes():
    """Số mũ áp cho TỶ LỆ bps90, rồi mới nhân số phút.

    Bản đầu áp số mũ cho `bps90 × minutes_frac`, nên người đá nửa trận nhận
    (1/2)^1.99 ≈ 1/4 thay vì 1/2 — số phút bị trừng phạt hai lần.
    """
    full = standalone_bonus(bps90=25.0, minutes_frac=1.0)
    half = standalone_bonus(bps90=25.0, minutes_frac=0.5)
    assert full > 0
    assert half == pytest.approx(full / 2, rel=1e-9)


def test_standalone_bonus_is_bounded_and_non_negative():
    assert standalone_bonus(bps90=0.0, minutes_frac=1.0) == 0.0
    assert standalone_bonus(bps90=-5.0, minutes_frac=1.0) == 0.0
    assert standalone_bonus(bps90=25.0, minutes_frac=0.0) == 0.0
    assert standalone_bonus(bps90=500.0, minutes_frac=1.0) == 3.0     # trần luật


def test_standalone_matches_the_fitted_season_relationship():
    """Dạng rời rạc phải đúng là quan hệ đã khớp: bonus/90 = c · bps90^κ."""
    bps90 = 20.0
    expected = bonus_mod.STANDALONE_SCALE * bps90 ** CONCENTRATION
    assert standalone_bonus(bps90=bps90, minutes_frac=1.0) == pytest.approx(
        expected, rel=1e-9
    )
    # mức của một cầu thủ đầu bảng phải nằm trong khoảng hợp lý (0.2..1.5 điểm)
    assert 0.2 < standalone_bonus(bps90=25.0, minutes_frac=1.0) < 1.5


def test_xpoints_uses_the_allocated_bonus_when_given_one():
    """`bonus_override` phải thắng công thức nội bộ — đó là điểm nối với allocate()."""
    from app import scoring
    from app.engine.xpoints import expected_points

    common = dict(
        element_type=3, minutes_season=2700, xg_season=8.0, xa_season=6.0,
        saves_season=0, dc_season=200, yellow_season=4, red_season=0,
        bps_season=700, cbi_season=80.0, penalties_order=None,
        xmins=85, p_start=0.9, p_appear=0.95, p_60_plus=0.88,
        lam_team_goals=1.6, lam_conceded=1.1, team_avg_gf=1.6,
    )
    free = expected_points(**common)
    forced = expected_points(**common, bonus_override=2.5)
    assert forced.bonus == pytest.approx(2.5, abs=1e-6)
    # các trường trong breakdown được làm tròn 3 chữ số trước khi cộng, nên so ở
    # mức 0.01 chứ không phải chính xác từng bit
    assert forced.xp == pytest.approx(free.xp - free.bonus + 2.5, abs=0.01)

    # trần luật vẫn được tôn trọng
    capped = expected_points(**common, bonus_override=99.0)
    assert capped.bonus == pytest.approx(scoring.RULES.max_bonus, abs=1e-6)


def test_projection_run_conserves_six_bonus_points_per_fixture(db):
    """Kiểm tra ở mức end-to-end, vì lỗi cũ nằm đúng ở khe này.

    Các test đơn vị ở trên chứng minh `allocate()` bảo toàn quỹ. Nhưng lỗi 2.47
    điểm/trận là lỗi ở chỗ NỐI: engine không hề gọi tới một phép chia quỹ nào. Chỉ
    một phép đếm trên bảng đã ghi mới bắt được loại lỗi đó.
    """
    from sqlalchemy import func, select

    from app.engine.projections import build_projections
    from app.models import Fixture, PlayerProjection

    # dùng đúng các vòng mà lần chạy này ghi ra: bộ dữ liệu demo đã đá xong mấy
    # vòng đầu nên kế hoạch không bắt đầu từ GW1
    result = build_projections(db, horizon=2, mc_iterations=200)
    db.flush()

    checked = 0
    for gw in result["gameweeks"]:
        n_fixtures = db.scalar(
            select(func.count()).select_from(Fixture).where(Fixture.event == gw)
        ) or 0
        if not n_fixtures:
            continue
        total = db.scalar(
            select(func.sum(PlayerProjection.xp_bonus)).where(
                PlayerProjection.gameweek == gw
            )
        ) or 0.0
        assert total == pytest.approx(
            n_fixtures * BONUS_POOL_PER_MATCH, rel=0.02
        ), f"GW{gw}: {total:.2f} thay vì {n_fixtures * BONUS_POOL_PER_MATCH}"
        checked += 1
    assert checked > 0, "không vòng nào có lịch để kiểm tra"
    db.rollback()

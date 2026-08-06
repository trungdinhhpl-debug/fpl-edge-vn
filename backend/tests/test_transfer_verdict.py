"""Khuyến nghị chuyển nhượng + chỉ số rủi ro từng cầu thủ.

Test quan trọng nhất ở đây là `test_ft_value_is_not_the_next_best_upgrade`: bản đầu
định giá free transfer bằng lợi ích của nâng cấp tốt tiếp theo, và cách đó sai theo
kế toán — giữ FT không mua thêm nước nào, nó chỉ trì hoãn nước tốt nhất một vòng.
Cách sai đó cho ra 5.30 điểm, đủ để lật kết luận của gần như mọi khuyến nghị.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.optimizer.constraints import MAX_BANKED_FT
from app.services.player_risk import (
    REPLACEMENT_PERCENTILE,
    injury_risk,
    price_risk,
    replacement_level,
    rotation_risk,
    vorp,
)
from app.services.transfer_verdict import (
    HORIZONS,
    MIN_MEANINGFUL_NET,
    ft_value,
    rotation_adjustment,
    transfer_verdict,
    upgrade_opportunities,
)


# ------------------------------------------------------------------ VORP -------
def test_replacement_level_uses_the_configured_percentile():
    xp = {3: [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]}
    repl = replacement_level(xp)
    # loại xP = 0 rồi lấy phân vị -> phải nằm trong khoảng giữa, không phải người tệ nhất
    assert 4.0 <= repl[3] <= 8.0
    assert repl[3] > min(v for v in xp[3] if v > 0)


def test_replacement_level_ignores_zero_xp_players():
    """Nếu tính cả người xP = 0 thì mức thay thế tụt xuống và VORP của ai cũng to."""
    with_zeros = replacement_level({3: [0.0] * 50 + [5.0, 6.0, 7.0]})
    without = replacement_level({3: [5.0, 6.0, 7.0]})
    assert with_zeros[3] == without[3]


def test_vorp_is_negative_when_below_replacement():
    assert vorp(2.0, 4.0)["value"] == pytest.approx(-2.0)
    assert vorp(7.0, 4.0)["value"] == pytest.approx(3.0)
    assert "phân vị" in vorp(7.0, 4.0)["basis"]


# --------------------------------------------------------- rotation risk -------
def test_rotation_risk_separates_steady_from_erratic_minutes():
    """Cùng P(đá chính) nhưng số phút dao động khác nhau phải cho mức khác nhau."""
    steady = rotation_risk(0.7, [72, 70, 68, 74, 71, 69])
    erratic = rotation_risk(0.7, [90, 0, 90, 0, 90, 0])
    assert erratic["score"] > steady["score"]
    assert "độ lệch phút" in erratic["basis"]

    # không có số phút từng vòng thì phải nói ra, không im lặng bỏ qua
    unknown = rotation_risk(0.7, None)
    assert "chưa có số phút" in unknown["basis"]


def test_rotation_risk_scales_with_p_start():
    assert rotation_risk(0.95, None)["level"] == "Thấp"
    assert rotation_risk(0.30, None)["level"] == "Cao"


# ----------------------------------------------------------- injury risk -------
def test_injury_risk_uses_fpls_own_number_when_present():
    """`chance_of_playing` là số của chính FPL — có thì dùng thẳng, không suy diễn."""
    r = injury_risk("d", 25, "Knock - 25% chance", None)
    assert r["score"] == pytest.approx(0.75)
    assert "25%" in r["basis"]

    healthy = injury_risk("a", None, None, None)
    assert healthy["score"] == 0.0 and healthy["level"] == "Thấp"

    suspended = injury_risk("s", None, "Suspended", None)
    assert suspended["score"] == pytest.approx(1.0)


def test_injury_risk_flags_stale_news():
    old = datetime.now(timezone.utc) - timedelta(days=30)
    r = injury_risk("d", 50, "Knock", old)
    assert r["news_age_days"] is not None and r["news_age_days"] > 25
    assert "cũ" in r["basis"]


# ------------------------------------------------------------ price risk -------
def test_price_risk_never_claims_to_be_a_prediction():
    r = price_risk(200_000, 10_000, 12.5)
    assert r["level"] == "Cao" and r["direction"] == "có thể tăng"
    assert r["is_prediction"] is False
    assert "không công khai" in r["caveat"]

    quiet = price_risk(1_000, 900, 3.0)
    assert quiet["level"] == "Thấp" and quiet["direction"] == "ổn định"


# -------------------------------------------------------------- FT value ------
def test_ft_value_is_zero_at_max_banked():
    r = ft_value(MAX_BANKED_FT)
    assert r["value"] == 0.0
    assert str(MAX_BANKED_FT) in r["basis"]


def test_ft_value_is_not_the_next_best_upgrade():
    """Chỗ đã sai một lần, và sai đủ để lật mọi kết luận.

    Kế toán: chuyển ngay thì vòng 1 làm A, vòng 2 (FT mới) làm B. Giữ lại thì vòng 2
    có 2 FT nên làm A và B. Hai đường làm cùng những nước — giữ lại chỉ TRÌ HOÃN A.
    Nên giá của FT không thể bằng lợi ích của B.
    """
    r = ft_value(1, next_best_gain=5.30)
    assert r["value"] == pytest.approx(settings.ft_option_value)
    assert r["value"] < 5.30, "lại lấy nâng cấp tiếp theo làm giá FT"
    # phải nói rõ đây là giả định, không phải số đo được
    assert r["is_assumption"] is True
    assert "KHÔNG cộng vào đây" in r["basis"]

    # và giá trị không đổi theo nâng cấp tiếp theo
    assert ft_value(1, 0.5)["value"] == ft_value(1, 50.0)["value"]


# ---------------------------------------------------- rotation adjustment ------
def test_rotation_adjustment_scales_with_the_incoming_players_xp():
    """Cùng mức rủi ro, mua người điểm cao thì phần kỳ vọng mất đi lớn hơn."""
    small = rotation_adjustment(0.3, 0.0, 3.0)["value"]
    big = rotation_adjustment(0.3, 0.0, 8.0)["value"]
    assert big < small < 0
    assert big == pytest.approx(-0.3 * 8.0)

    # người mua chắc suất hơn thì không trừ
    none = rotation_adjustment(0.05, 0.30, 8.0)
    assert none["value"] == 0.0
    assert "không trừ" in none["basis"]


# ---------------------------------------------------------------- verdict ------
def _mid_squad(db) -> list[int]:
    """15 người ở khoảng giữa bảng xP, để còn chỗ nâng cấp."""
    from app.models import Player
    from app.services.common import planning_start_gw, projections_for_gw

    gw = planning_start_gw(db)
    projs = projections_for_gw(db, gw)
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    need = {1: 2, 2: 5, 3: 5, 4: 3}
    club: dict[int, int] = {}
    squad: list[int] = []
    ranked = sorted(projs.items(), key=lambda kv: -kv[1].xp)
    for pid, _ in ranked[len(ranked) // 3:]:
        p = players.get(pid)
        if not p or need.get(p.element_type, 0) <= 0 or club.get(p.team_id, 0) >= 3:
            continue
        squad.append(pid)
        need[p.element_type] -= 1
        club[p.team_id] = club.get(p.team_id, 0) + 1
        if len(squad) == 15:
            break
    return squad


def test_upgrade_opportunities_respects_budget_and_club_limit(db):
    from app.models import Player

    squad = _mid_squad(db)
    if len(squad) < 15:
        pytest.skip("không dựng được đội 15 người từ bộ dữ liệu")

    opts = upgrade_opportunities(db, squad, bank=0, gws=[1, 2, 3])
    players = {p.id: p for p in db.scalars(select(Player)).all()}
    for gain, out_id, in_id in opts:
        assert gain > 0
        assert in_id not in squad
        # cùng vị trí và mua nổi bằng tiền bán + bank
        assert players[in_id].element_type == players[out_id].element_type
        assert players[in_id].now_cost <= players[out_id].now_cost + 0
    # xếp giảm dần
    assert opts == sorted(opts, reverse=True)


def test_verdict_has_every_section_the_structure_requires(db):
    squad = _mid_squad(db)
    if len(squad) < 15:
        pytest.skip("không dựng được đội 15 người")

    r = transfer_verdict(db, squad, bank=10, free_transfers=1)
    assert r["recommendation"] in ("TRANSFER", "ROLL TRANSFER")
    assert r["conclusion"]
    assert 0.0 <= r["confidence"]["value"] <= 1.0
    assert r["confidence"]["basis"]

    if r["best_move"] is None:
        return      # không có nâng cấp nào: đã có kết luận riêng, hợp lệ

    for side in ("out", "in"):
        assert r["best_move"][side]["name"]
    # đủ ba mốc horizon
    for h in HORIZONS:
        assert f"{h}gw" in r["xp_delta"]
    # điều chỉnh phải có căn cứ, không chỉ có số
    labels = {a["label"] for a in r["adjustments"]}
    assert {"Mất giá trị FT", "Rủi ro rotation"} <= labels
    for a in r["adjustments"]:
        assert a["basis"]
    assert r["net_basis"]


def test_verdict_rolls_when_the_margin_is_inside_model_error(db):
    """Biên nhỏ hơn sai số mô hình thì phải ROLL, và nói đúng lý do đó."""
    squad = _mid_squad(db)
    if len(squad) < 15:
        pytest.skip("không dựng được đội 15 người")

    r = transfer_verdict(db, squad, bank=0, free_transfers=1)
    if r["best_move"] is None:
        pytest.skip("không có nâng cấp nào để xét")
    if abs(r["net_gain"]) >= MIN_MEANINGFUL_NET:
        pytest.skip("biên đủ lớn — nhánh này không chạm được với dữ liệu hiện có")

    assert r["recommendation"] == "ROLL TRANSFER"
    assert "sai số" in r["conclusion"]
    # và độ tin cậy phải bị hạ vì biên nhỏ
    assert "nhỏ hơn sai số" in r["confidence"]["basis"]


def test_news_watch_says_what_the_system_does_not_know(db):
    """Hai trong ba loại tin là KHÔNG có dữ liệu — phải nói ra, không gợi ý đã kiểm."""
    squad = _mid_squad(db)
    if len(squad) < 15:
        pytest.skip("không dựng được đội 15 người")

    r = transfer_verdict(db, squad, bank=10, free_transfers=1)
    kinds = {n["kind"]: n for n in r["news_watch"]}
    assert len(kinds) == 3

    cup = next(n for k, n in kinds.items() if "cúp" in k.lower())
    assert cup["known"] is False
    assert "chỉ công bố lịch Ngoại hạng" in cup["caveat"]

    presser = next(n for k, n in kinds.items() if "họp báo" in k.lower())
    assert presser["known"] is False
    assert "KHÔNG theo dõi họp báo" in presser["caveat"]


# ------------------------------------------------------------- scorecard ------
def test_player_scorecard_has_all_thirteen_fields(db):
    from app.models import Player
    from app.services.players import player_scorecard

    pid = db.scalar(select(Player.id))
    sc = player_scorecard(db, pid)
    assert sc is not None

    for key in (
        "distribution", "minutes", "vorp", "rotation_risk", "injury_risk",
        "price_risk", "source_freshness", "model_confidence",
    ):
        assert key in sc, key

    if sc["distribution"]:
        for k in ("xp_mean", "mc_mean", "median", "p10", "p25", "p75", "p90"):
            assert k in sc["distribution"], k
    for k in ("xmins", "p_start", "p_dnp"):
        assert k in sc["minutes"], k
    # mỗi nhãn rủi ro phải kèm căn cứ để người đọc tự kiểm
    for k in ("rotation_risk", "injury_risk", "price_risk"):
        assert sc[k]["basis"]


def test_freshness_uses_sync_age_not_row_update_time(db):
    """"Cũ" phải theo tuổi lần ĐỒNG BỘ, không theo `player.updated_at`.

    `updated_at` có `onupdate` nên chỉ nhích khi hàng thật sự đổi giá trị. Bản đầu
    dùng nó và production hiện ra badge "đã cũ" ngay cạnh dòng "đồng bộ gần nhất
    cách đây 9 phút" — hai câu tự mâu thuẫn trên cùng một thẻ.
    """
    from app.models import Player, SourceFetchLog
    from app.services.player_risk import source_freshness

    p = db.scalar(select(Player))
    # hàng của cầu thủ mang mốc rất cũ...
    original_updated = p.updated_at
    p.updated_at = datetime.now(timezone.utc) - timedelta(days=30)
    # ...nhưng vừa đồng bộ xong
    log = SourceFetchLog(
        source_name="FPL bootstrap-static", status="ok", rows=1,
        fetched_at=datetime.now(timezone.utc),
    )
    db.add(log)
    db.flush()
    try:
        f = source_freshness(db, p)
        assert f["stale"] is False, "quy sai sang updated_at nên báo cũ oan"
        assert f["fpl_sync_age_minutes"] is not None
        assert f["fpl_sync_age_minutes"] < 60
        # vẫn báo cáo mốc của hàng, chỉ là không dùng để kết luận
        assert f["player_age_minutes"] is not None
        assert f["player_age_minutes"] > 60 * 24

        # đồng bộ cũ thật thì phải báo cũ
        log.fetched_at = datetime.now(timezone.utc) - timedelta(hours=20)
        db.flush()
        assert source_freshness(db, p)["stale"] is True
    finally:
        p.updated_at = original_updated
        db.flush()
        db.rollback()

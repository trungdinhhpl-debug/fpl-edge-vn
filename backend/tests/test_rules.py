"""Luật mùa giải phải đọc từ FPL API, không ghi cứng trong code."""
import json

import pytest

from app import scoring


# game_config rút gọn, đúng hình dạng FPL trả về
SAMPLE_CONFIG = {
    "settings": {
        "static_content_url": "https://fantasy.premierleague.com/gcs/x/plfpl-production/2026_27/"
    },
    "rules": {
        "squad_squadsize": 15,
        "squad_squadplay": 11,
        "squad_team_limit": 3,
        "squad_total_spend": 1000,
        "max_extra_free_transfers": 4,
        "transfers_sell_on_fee": 0.5,
        "transfers_cap": 20,
    },
    "scoring": {
        "short_play": 1,
        "long_play": 2,
        "goals_scored": {"GKP": 10, "DEF": 6, "MID": 5, "FWD": 4},
        "assists": 3,
        "clean_sheets": {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0},
        "goals_conceded": {"GKP": -1, "DEF": -1, "MID": 0, "FWD": 0},
        "yellow_cards": -1,
        "red_cards": -3,
        "own_goals": -2,
        "penalties_missed": -2,
        "penalties_saved": 5,
        "defensive_contribution": {"GKP": 0, "DEF": 2, "MID": 2, "FWD": 2},
    },
}


def test_season_read_from_api_not_hardcoded():
    assert scoring.season_from_config(SAMPLE_CONFIG) == "2026/27"
    # đổi mùa trong config thì tên mùa phải đổi theo, không có chuỗi cố định nào
    other = json.loads(json.dumps(SAMPLE_CONFIG))
    other["settings"]["static_content_url"] = ".../plfpl-production/2027_28/"
    assert scoring.season_from_config(other) == "2027/28"


def test_scoring_rules_parsed_from_config():
    r = scoring.scoring_from_config(SAMPLE_CONFIG)
    # thủ môn ghi bàn 10 điểm (2026/27) — trước đây bị ghi cứng thành 6
    assert r.goal_points[1] == 10
    assert r.goal_points[2] == 6
    assert r.goal_points[3] == 5
    assert r.goal_points[4] == 4
    assert r.clean_sheet_points[1] == 4
    assert r.clean_sheet_points[3] == 1
    assert r.assist_points == 3
    assert r.source != "fallback"


def test_defensive_contribution_from_config():
    r = scoring.scoring_from_config(SAMPLE_CONFIG)
    assert r.defcon_points == 2
    assert set(r.defcon_positions) == {2, 3, 4}      # thủ môn không được tính
    # ngưỡng không có trong API nên giữ trong cấu hình
    assert r.defcon_threshold_def == 10
    assert r.defcon_threshold_att == 12


def test_max_free_transfers_is_extra_plus_one():
    g = scoring.game_from_config(SAMPLE_CONFIG)
    assert g.max_free_transfers == 5          # 4 "extra" + 1 mặc định
    assert g.squad_size == 15
    assert g.team_limit == 3
    assert g.total_spend == 1000


def test_rules_version_changes_only_when_rules_change():
    v1 = scoring.rules_hash(SAMPLE_CONFIG)
    same = json.loads(json.dumps(SAMPLE_CONFIG))
    assert scoring.rules_hash(same) == v1                    # đồng bộ lại: không đổi

    changed = json.loads(json.dumps(SAMPLE_CONFIG))
    changed["scoring"]["assists"] = 4
    assert scoring.rules_hash(changed) != v1                 # đổi luật: đổi phiên bản


def test_apply_config_updates_engine_singletons():
    """Engine giữ tham chiếu tới RULES/GAME nên phải cập nhật TẠI CHỖ."""
    from app.scoring import GAME, RULES

    scoring.apply_config(SAMPLE_CONFIG)
    assert RULES.goal_points[1] == 10        # chính đối tượng engine đang dùng
    assert GAME.max_free_transfers == 5
    assert scoring.SEASON == "2026/27"
    assert scoring.RULES_VERSION == scoring.rules_hash(SAMPLE_CONFIG)


def test_xp_uses_updated_goal_points():
    """Đổi luật phải chảy thẳng vào mô hình xP, không phải sửa code engine."""
    from app.engine.xpoints import expected_points

    common = dict(
        element_type=4, minutes_season=900, xg_season=8.0, xa_season=1.0,
        saves_season=0, dc_season=20, yellow_season=1, red_season=0, bps_season=200,
        penalties_order=None, xmins=85, p_start=0.9, p_appear=0.95, p_60_plus=0.85,
        lam_team_goals=1.5, lam_conceded=1.1, team_avg_gf=1.5,
    )

    scoring.apply_config(SAMPLE_CONFIG)
    before = expected_points(**common)

    # FPL nhân đôi điểm ghi bàn cho tiền đạo -> phần điểm bàn thắng phải nhân đôi
    doubled = json.loads(json.dumps(SAMPLE_CONFIG))
    doubled["scoring"]["goals_scored"]["FWD"] = 8
    scoring.apply_config(doubled)
    after = expected_points(**common)

    assert after.goals == pytest.approx(before.goals * 2, rel=0.01)
    assert after.xp > before.xp

    scoring.apply_config(SAMPLE_CONFIG)   # trả lại luật gốc cho các test khác


# --------------------------------------------------- BPS: luật riêng theo mùa ----
#
# FPL không phát trọng số BPS qua API, nên chúng nằm trong app/bps_rules.py và
# phải được đánh phiên bản theo mùa. Nhóm test này khoá hai điều dễ vỡ lại:
# phiên bản phải đi theo mùa, và tổng BPS mang từ mùa trước sang phải được quy
# đổi trước khi vào mô hình bonus.
def test_bps_rules_follow_the_season():
    from app import bps_rules

    scoring.apply_config(SAMPLE_CONFIG)                  # 2026/27
    assert scoring.BPS_RULES.version == "2026.1"
    assert scoring.BPS_RULES_KNOWN is True
    assert scoring.BPS_RULES.cbi_per_bps == 3            # 1 BPS mỗi 3 CBI
    assert bps_rules.for_season("2025/26").cbi_per_bps == 2
    assert bps_rules.for_season("2025/26").tackled_bps == -1
    assert scoring.BPS_RULES.tackled_bps == 0            # đã bỏ hạng mục

    # mùa chưa khai báo: dùng bộ mới nhất nhưng KHÔNG được báo là đã biết
    future = json.loads(json.dumps(SAMPLE_CONFIG))
    future["settings"]["static_content_url"] = ".../plfpl-production/2030_31/"
    scoring.apply_config(future)
    assert scoring.BPS_RULES_KNOWN is False
    assert scoring.BPS_RULES.version == bps_rules.LATEST.version

    scoring.apply_config(SAMPLE_CONFIG)


def test_previous_season_parsing():
    from app.bps_rules import previous_season

    assert previous_season("2026/27") == "2025/26"
    assert previous_season("2030/31") == "2029/30"
    assert previous_season("unknown") is None
    assert previous_season(None) is None


def test_carryover_bps_rescaled_for_cbi_change():
    """Trung vệ: BPS mùa trước phải bị hạ vì CBI đổi từ 1/2 sang 1/3 BPS."""
    from app.bps_rules import BPS_2025_26, BPS_2026_27, equivalent_bps

    same = dict(bps=600.0, cbi=360.0, saves=0.0, minutes=3200.0)
    # cùng bộ luật thì không quy đổi
    assert equivalent_bps(**same, from_rules=BPS_2026_27, to_rules=BPS_2026_27) == 600.0

    out = equivalent_bps(**same, from_rules=BPS_2025_26, to_rules=BPS_2026_27)
    assert out == pytest.approx(600.0 - 360.0 / 6, abs=1e-6)   # ΔBPS = −CBI/6
    assert out < 600.0

    # cầu thủ không có hành động phòng ngự thì không bị ảnh hưởng
    none_cbi = equivalent_bps(
        bps=600.0, cbi=0.0, saves=0.0, minutes=3200.0,
        from_rules=BPS_2025_26, to_rules=BPS_2026_27,
    )
    assert none_cbi == 600.0

    # không bao giờ trả về số âm
    assert equivalent_bps(
        bps=1.0, cbi=600.0, saves=0.0, minutes=3200.0,
        from_rules=BPS_2025_26, to_rules=BPS_2026_27,
    ) == 0.0


def test_xp_bonus_lower_when_bps_earned_under_old_rules():
    """Đường ĐỌC tự bảo vệ: chỉ cần nói tổng thuộc mùa nào là xP tự quy đổi."""
    from app.engine.xpoints import expected_points

    scoring.apply_config(SAMPLE_CONFIG)                  # đang chơi 2026/27
    common = dict(
        element_type=2, minutes_season=3200, xg_season=1.5, xa_season=1.0,
        saves_season=0, dc_season=350, yellow_season=6, red_season=0,
        bps_season=600, cbi_season=360.0, penalties_order=None,
        xmins=85, p_start=0.9, p_appear=0.95, p_60_plus=0.88,
        lam_team_goals=1.4, lam_conceded=1.1, team_avg_gf=1.4,
    )

    as_current = expected_points(**common, stats_season=None)
    carried = expected_points(**common, stats_season="2025/26")

    assert carried.bonus < as_current.bonus
    assert carried.xp < as_current.xp
    # không đụng tới các thành phần khác
    assert carried.goals == pytest.approx(as_current.goals)
    assert carried.defcon == pytest.approx(as_current.defcon)


def test_rules_versions_covers_every_season_rules_column():
    """`rules_versions()` phải khớp đúng các cột của bảng season_rules."""
    scoring.apply_config(SAMPLE_CONFIG)
    v = scoring.rules_versions()
    for key in (
        "scoring_rules_version",
        "bps_rules_version",
        "assist_rules_version",
        "chip_rules_version",
        "source_url",
    ):
        assert v.get(key), f"thiếu {key}"
    assert v["scoring_rules_version"] == scoring.rules_hash(SAMPLE_CONFIG)
    assert v["bps_rules_version"] == "2026.1"


def test_meta_version_publishes_bps_provenance():
    """Thanh phiên bản trên web phải nói được BPS từ đâu và có từ bao giờ.

    Trọng số BPS không đến từ FPL API, nên nếu giao diện hiện nó cạnh những con số
    lấy từ API mà không phân biệt nguồn thì người đọc sẽ tưởng cả hai đều là số
    chính thức.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/api/meta/version").json()

    # nhãn dễ đọc VÀ vân tay chính xác, cả hai đều phải có
    assert body["rules_label"].startswith("v")
    assert body["rules_revision"] >= 1
    assert body["rules_version"]
    assert body["rules_label"] != body["rules_version"]

    assert body["bps_rules_version"]
    assert body["bps_rules_source_url"].startswith("http")
    assert "bps_rules_known" in body
    # ngày công bố là ISO hoặc None — giao diện hiện "chưa rõ" khi None
    eff = body["bps_rules_effective_from"]
    assert eff is None or len(eff) == 10

    assert body["projection_version"]

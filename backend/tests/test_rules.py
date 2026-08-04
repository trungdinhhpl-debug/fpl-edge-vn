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

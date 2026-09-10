"""Nguồn vai trò chiến thuật: đọc nhãn của Premier League + nạp từ DB.

Phần dễ sai nhất **không phải HTTP mà là cái nhãn**. Premier League phát ra
`"Left/Right Winger"` cho người đá cả hai biên, và `"Midfielder"` cho người họ không
có thông tin chi tiết. Ép cả hai thành một vai trò cụ thể là đúng thứ đặc tả cấm:
bịa dữ liệu không có. Những test dưới đây khoá lại chuyện đó.

Mọi nhãn dùng ở đây là **giá trị thật** quan sát được từ API ngày 2026-09-09.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.engine import roles as roles_mod
from app.providers import pl_positions as plp


# ============================================================ đọc nhãn =======
@pytest.mark.parametrize(
    "label,expected",
    [
        # nhãn có cánh rõ ràng -> vai trò có cánh
        ("Left Full Back", "LB"),
        ("Right Full Back", "RB"),
        ("Left Winger", "LW"),
        ("Right Winger", "RW"),
        ("Centre/Right Wing Back", "RWB"),
        # nhãn không phụ thuộc cánh -> cánh ghép cũng không sao
        ("Centre Central Defender", "CB"),
        ("Central Defender", "CB"),
        ("Left/Centre Central Defender", "CB"),
        ("Centre/Right Central Defender", "CB"),
        ("Centre Central Midfielder", "CM"),
        ("Central Midfielder", "CM"),
        ("Left Central Midfielder", "CM"),
        ("Left/Centre Central Midfielder", "CM"),
        ("Centre Defensive Midfielder", "DM"),
        ("Defensive Midfielder", "DM"),
        ("Centre Attacking Midfielder", "AM"),
        ("Attacking Midfielder", "AM"),
        ("Left/Centre/Right Attacking Midfielder", "AM"),
        ("Right Attacking Midfielder", "AM"),
        ("Centre Striker", "ST"),
        ("Striker", "ST"),
        ("Left/Centre/Right Striker", "ST"),
    ],
)
def test_real_labels_map_to_roles(label, expected):
    out = plp.parse_position_info(label)
    assert out.role == expected, out.reason
    assert out.raw_label == label


@pytest.mark.parametrize(
    "label",
    [
        "Left/Right Winger",                     # 19 người có nhãn này
        "Left/Centre/Right Winger",
        "Winger",                                # không nói biên nào
    ],
)
def test_both_flanks_or_no_flank_gives_no_role(label):
    """Vai trò phụ thuộc cánh mà cánh mơ hồ -> None. Không chọn bừa một bên."""
    out = plp.parse_position_info(label)
    assert out.role is None
    assert out.raw_label == label
    assert out.reason                       # phải nói được VÌ SAO


def test_one_flank_plus_centre_keeps_the_flank():
    """"Left/Centre Winger" KHÔNG mơ hồ về biên — chỉ mơ hồ giữa biên và trung lộ.

    Mơ hồ thật sự là khi nhãn có CẢ trái lẫn phải. Ở đây biên duy nhất được nhắc là
    trái, nên giữ LW là đọc đúng thông tin nguồn có, không phải chọn bừa. Phần "đôi
    khi đá trung lộ" đã được `finishing_share` của LW (0.60) hạ bớt sẵn.
    """
    assert plp.parse_position_info("Left/Centre Winger").role == "LW"
    assert plp.parse_position_info("Centre/Right Wing Back").role == "RWB"


@pytest.mark.parametrize(
    "label", ["Defender", "Midfielder", "Forward", "Goalkeeper"]
)
def test_coarse_labels_are_refused(label):
    """Bốn nhãn thô đúng bằng element_type của FPL — dùng chúng là suy từ nhãn vị trí."""
    out = plp.parse_position_info(label)
    assert out.role is None
    assert "thô" in out.reason


def test_unknown_and_empty_labels():
    assert plp.parse_position_info(None).role is None
    assert plp.parse_position_info("").role is None
    out = plp.parse_position_info("Sweeper Keeper Libero")
    assert out.role is None and "Chưa biết cách đọc" in out.reason


def test_second_striker_is_read_conservatively():
    """Hộ công xếp vào AM (dứt điểm 0.70) chứ không ST (1.00) — đoán phía thấp."""
    assert plp.parse_position_info("Left/Centre/Right Second Striker").role == "AM"
    assert roles_mod.FINISHING_SHARE["AM"] < roles_mod.FINISHING_SHARE["ST"]


def test_every_produced_role_is_in_the_taxonomy():
    """Parser không được sinh ra mã nằm ngoài danh sách vai trò hợp lệ."""
    labels = [
        "Left Full Back", "Right Full Back", "Left Winger", "Right Winger",
        "Centre/Right Wing Back", "Centre Central Defender", "Central Midfielder",
        "Defensive Midfielder", "Attacking Midfielder", "Striker", "Centre Goalkeeper",
    ]
    for lab in labels:
        role = plp.parse_position_info(lab).role
        if role is not None:
            assert role in roles_mod.ROLES, f"{lab} -> {role}"


def test_flank_roles_map_to_the_opposite_defensive_flank():
    """Ghép đầu-cuối: nhãn nguồn -> vai trò -> trục đối thủ, phải bắt chéo."""
    assert roles_mod.role_axis(plp.parse_position_info("Right Winger").role) == (
        roles_mod.AXIS_LEFT_DEF
    )
    assert roles_mod.role_axis(plp.parse_position_info("Left Full Back").role) == (
        roles_mod.AXIS_RIGHT_DEF
    )


# ==================================================== gom về bản ghi ========
class _FakeProvider(plp.PLPositionsProvider):
    """Provider giả — không chạm mạng, để test phần lọc CLB và gom bản ghi."""

    def __init__(self, players, clubs, season=(841, "2026/27")):
        self._players, self._clubs, self._season = players, clubs, season

    def current_season(self):
        return self._season

    def clubs(self, season_id):
        return self._clubs

    def players(self, season_id, max_pages=30):
        return self._players


def _api_player(name, club, position_info):
    return {
        "name": {"display": name},
        "currentTeam": {"name": club},
        "info": {"positionInfo": position_info},
    }


def test_fetch_filters_out_players_who_left_the_league():
    """Danh sách nguồn có cả người sang giải khác và đội U21 — phải lọc theo 20 CLB."""
    prov = _FakeProvider(
        players=[
            _api_player("Bukayo Saka", "Arsenal", "Right Winger"),
            _api_player("Max Aarons", "Rangers", "Defender"),
            _api_player("Adam Harrison", "Wolverhampton Wanderers U21", "Goalkeeper"),
            _api_player("Nobody", None, "Striker"),
        ],
        clubs={"Arsenal", "Chelsea"},
    )
    report = plp.fetch_roles(prov)
    assert [r.display_name for r in report.records] == ["Bukayo Saka"]
    assert report.usable[0].role == "RW"
    assert report.season_id == 841


def test_fetch_keeps_unusable_labels_for_explanation():
    """Nhãn không dùng được vẫn được giữ lại — giao diện cần nói vì sao trống."""
    prov = _FakeProvider(
        players=[_api_player("A Player", "Arsenal", "Left/Right Winger")],
        clubs={"Arsenal"},
    )
    report = plp.fetch_roles(prov)
    assert len(report.records) == 1
    assert report.records[0].role is None
    assert report.records[0].raw_label == "Left/Right Winger"
    assert report.usable == []


def test_network_failure_is_an_empty_report_not_an_exception():
    class _Broken(plp.PLPositionsProvider):
        def current_season(self):
            raise RuntimeError("503")

    report = plp.fetch_roles(_Broken())
    assert report.records == [] and report.errors


def test_enable_flag_gates_every_network_call():
    """Cờ tắt -> KHÔNG có provider, tức không có đường nào gọi ra mạng.

    Test này cố ý **không** khẳng định giá trị mặc định: mặc định là một quyết định
    vận hành (hiện là bật), còn thứ phải luôn đúng là cái cổng — tắt cờ thì không
    một request nào rời khỏi máy.
    """
    from app.config import settings

    original = settings.pl_positions_enabled
    try:
        settings.pl_positions_enabled = False
        assert plp.get_pl_positions_provider() is None
        settings.pl_positions_enabled = True
        assert plp.get_pl_positions_provider() is not None
    finally:
        settings.pl_positions_enabled = original


def test_disabled_sync_writes_nothing_and_makes_no_request(db):
    """Cờ tắt: sync trả về ngay, không ghi hàng nào, không chạm mạng."""
    from app.config import settings
    from app.ingestion.fpl_sync import sync_player_roles
    from app.models import PlayerRole

    original = settings.pl_positions_enabled
    try:
        settings.pl_positions_enabled = False
        db.execute(PlayerRole.__table__.delete())
        db.commit()
        res = sync_player_roles(db)
        assert res == {"enabled": False, "roles": 0}
        assert db.scalars(select(PlayerRole)).all() == []
    finally:
        settings.pl_positions_enabled = original


# ================================================ nạp vai trò từ DB =========
def _put_role(db, player_id, role, raw="x", source="nguồn thử"):
    from app.models import PlayerRole

    db.add(PlayerRole(
        player_id=player_id, role=role, raw_label=raw, source_name=source,
        fetched_at=datetime.now(timezone.utc),
    ))
    db.commit()


def _clear_roles(db):
    from app.models import PlayerRole

    db.execute(PlayerRole.__table__.delete())
    db.commit()


def test_roles_load_from_db(db, monkeypatch):
    from app.config import settings
    from app.models import Player

    monkeypatch.setattr(settings, "player_roles_json", "")
    players = db.scalars(select(Player)).all()
    mid = next(p for p in players if p.element_type == 3)
    try:
        _clear_roles(db)
        _put_role(db, mid.id, "RW", raw="Right Winger")
        table = roles_mod.load_roles(players, db=db)
        assert table.configured is True
        assert table.get(mid.id).role == "RW"
        assert table.get(mid.id).source == "nguồn thử"
        assert table.status()["needs"] is None
    finally:
        _clear_roles(db)


def test_db_row_with_null_role_is_not_a_role(db, monkeypatch):
    """Hàng role=NULL nghĩa là "nguồn có nói nhưng nói không rõ" — không phải vai trò."""
    from app.config import settings
    from app.models import Player

    monkeypatch.setattr(settings, "player_roles_json", "")
    players = db.scalars(select(Player)).all()
    mid = next(p for p in players if p.element_type == 3)
    try:
        _clear_roles(db)
        _put_role(db, mid.id, None, raw="Left/Right Winger")
        table = roles_mod.load_roles(players, db=db)
        assert table.get(mid.id).role is None
        assert table.configured is False
    finally:
        _clear_roles(db)


def test_db_role_contradicting_fpl_position_is_rejected(db, monkeypatch):
    """Nguồn nói thủ môn là trung phong -> bác, không tin bên nào."""
    from app.config import settings
    from app.models import Player

    monkeypatch.setattr(settings, "player_roles_json", "")
    players = db.scalars(select(Player)).all()
    gk = next(p for p in players if p.element_type == 1)
    try:
        _clear_roles(db)
        _put_role(db, gk.id, "ST", raw="Centre Striker")
        table = roles_mod.load_roles(players, db=db)
        assert table.get(gk.id).role is None
        assert table.rejected == 1
    finally:
        _clear_roles(db)


def test_hand_written_json_overrides_the_automated_source(db, tmp_path, monkeypatch):
    """Người khai tay thắng nguồn tự động — chiều ngược lại thì không."""
    import json

    from app.config import settings
    from app.models import Player

    players = db.scalars(select(Player)).all()
    mid = next(p for p in players if p.element_type == 3)
    path = tmp_path / "roles.json"
    path.write_text(
        json.dumps({
            "source": "khai tay",
            "roles": {str(mid.id): "LW"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "player_roles_json", str(path))
    try:
        _clear_roles(db)
        _put_role(db, mid.id, "RW", raw="Right Winger", source="tự động")
        table = roles_mod.load_roles(players, db=db)
        assert table.get(mid.id).role == "LW"          # khai tay thắng
        assert table.get(mid.id).source == "khai tay"
        assert "đè lên nguồn tự động" in table.detail
    finally:
        _clear_roles(db)


def test_engine_writes_the_role_onto_each_fixture_row(db, monkeypatch):
    """Vai trò phải đi tới tận bản ghi từng trận — và KHÔNG tự dịch xP."""
    from app.config import settings
    from app.engine.projections import build_projections, get_planning_start_gw
    from app.models import Player, PlayerFixtureGoals

    monkeypatch.setattr(settings, "player_roles_json", "")
    gw = get_planning_start_gw(db)
    players = db.scalars(select(Player)).all()
    fwd = next(p for p in players if p.element_type == 4)
    try:
        _clear_roles(db)
        _put_role(db, fwd.id, "ST", raw="Centre Striker")
        db.expire_all()
        build_projections(db, horizon=1, mc_iterations=300)
        row = db.scalar(
            select(PlayerFixtureGoals).where(
                PlayerFixtureGoals.player_id == fwd.id,
                PlayerFixtureGoals.gameweek == gw,
            )
        )
        if row is None:
            pytest.skip("cầu thủ này không có trận ở vòng đang xét")
        assert row.tactical_role == "ST"
        assert row.weakness_axis == roles_mod.AXIS_CENTRAL
        # Chưa đo được điểm yếu đối thủ -> hệ số đúng 1.0 và xP KHÔNG đổi.
        assert row.position_factor == pytest.approx(1.0)
        assert row.xp_delta == pytest.approx(0.0)
    finally:
        _clear_roles(db)
        db.expire_all()
        build_projections(db, horizon=2, mc_iterations=300)


# ============================ Postgres siết varchar, SQLite thì không =========
# Lỗi thật 2026-09-10: `season_label` của API dài 39 ký tự, cột khai varchar(32).
# SQLite (dev + test) nuốt gọn, Postgres (Neon) từ chối -> job đồng bộ hỏng 4 lần
# liên tiếp, mỗi lần một email. Cả test suite lúc đó vẫn xanh, nên bài học là:
# test chạy trên SQLite KHÔNG bao giờ tự bắt được lớp lỗi này. Phải khẳng định độ
# dài một cách tường minh.
def test_season_label_is_shortened_to_project_convention():
    assert plp.season_label("English Premier League Season 2026/2027") == "2026/27"
    assert plp.season_label("2025/26") == "2025/26"
    assert plp.season_label("English Premier League Season 2003/2004") == "2003/04"
    assert plp.season_label(None) is None


def test_season_label_always_fits_the_column():
    """Nhãn lạ không rút được vẫn phải vừa cột — thà xấu còn hơn giết job sync."""
    from app.models import PlayerRole

    limit = PlayerRole.season_label.type.length
    weird = "Some Extremely Long Competition Name Without Any Year In It At All"
    out = plp.season_label(weird)
    assert len(out) <= limit
    assert len(plp.season_label("English Premier League Season 2026/2027")) <= limit


def test_sync_never_writes_a_string_longer_than_its_column(db, monkeypatch):
    """Nguồn ngoài trả chuỗi dài vô lý -> vẫn không hàng nào tràn cột.

    Đây là test đứng thay cho Postgres: nó khẳng định điều mà SQLite không bao giờ
    ép, nên nó bắt được đúng lớp lỗi đã làm hỏng cron.
    """
    from app.config import settings
    from app.ingestion.fpl_sync import sync_player_roles
    from app.models import Player, PlayerRole, Team

    players = db.scalars(select(Player)).all()
    team = db.get(Team, players[0].team_id)
    victim = next(p for p in players if p.team_id == team.id and p.element_type == 3)

    long_club = team.name          # phải khớp CLB thật thì mới ghép được cầu thủ
    prov = _FakeProvider(
        players=[{
            "name": {"display": f"{victim.first_name} {victim.second_name}"},
            "currentTeam": {"name": long_club},
            "info": {"positionInfo": "Right Winger"},
        }],
        clubs={long_club},
        season=(841, "X" * 500),   # nhãn mùa dài vô lý
    )
    monkeypatch.setattr(plp, "get_pl_positions_provider", lambda: prov)
    monkeypatch.setattr(settings, "pl_positions_enabled", True)

    try:
        db.execute(PlayerRole.__table__.delete())
        db.commit()
        sync_player_roles(db)
        rows = db.scalars(select(PlayerRole)).all()
        assert rows, "phải ghi được ít nhất một hàng"
        for r in rows:
            for col in PlayerRole.__table__.columns:
                limit = getattr(col.type, "length", None)
                val = getattr(r, col.name)
                if limit and isinstance(val, str):
                    assert len(val) <= limit, (
                        f"{col.name}: {len(val)} ký tự > varchar({limit})"
                    )
    finally:
        db.execute(PlayerRole.__table__.delete())
        db.commit()

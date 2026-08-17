"""Núm chỉnh đám đông: nó phải làm đúng cái nó khai, và không hơn.

Điểm dễ sai nhất không phải công thức mà là PHẠM VI: mặc định phải không đổi gì
so với trước, và tấm băng đội trưởng phải nằm ngoài tầm với của EO (xem lý do
trong docstring của services/field.py).
"""
import pytest
from sqlalchemy import select

from app.models import Player
from app.services import field, team as team_svc
from app.services.common import planning_start_gw


def test_weight_zero_changes_absolutely_nothing(db):
    """Mặc định là 0, và 0 phải là đường cũ y nguyên — kể cả không đi lấy EO."""
    base = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep")
    same = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep", eo_weight=0.0)
    assert base["squad_ids"] == same["squad_ids"]
    assert same["field"]["kind"] == "off"


def test_tilt_direction(db):
    """Dương thì đẩy ra khỏi đám đông, âm thì kéo về phía đám đông."""
    # cùng xP, khác EO
    assert field.tilt(5.0, 5.0, eo=80.0, weight=0.5) < field.tilt(5.0, 5.0, eo=10.0, weight=0.5)
    assert field.tilt(5.0, 5.0, eo=80.0, weight=-0.5) > field.tilt(5.0, 5.0, eo=10.0, weight=-0.5)
    assert field.tilt(5.0, 5.0, eo=80.0, weight=0.0) == 5.0
    # phần trừ tỷ lệ với xP: người nhiều điểm bị trừ nhiều hơn ở cùng mức EO
    big = 8.0 - field.tilt(8.0, 8.0, eo=100.0, weight=1.0)
    small = 2.0 - field.tilt(2.0, 2.0, eo=100.0, weight=1.0)
    assert big > small


def test_modelled_eo_matches_the_captaincy_page(db):
    """Cùng một cầu thủ không được mang hai con số EO ở hai trang."""
    from app.services.captains import _build_candidates

    gw = planning_start_gw(db)
    eo = field._modelled_eo(db, gw)
    cands = _build_candidates(db, gw)
    assert cands, "không có ứng viên đội trưởng để đối chiếu"
    for c in cands[:20]:
        assert eo[c["id"]] == pytest.approx(c["projected_eo"], abs=0.05), c["name"]


def test_chasing_picks_a_less_owned_squad_than_protecting(db):
    """Hai đầu của núm phải cho ra hai đội hình khác nhau về mức phổ biến."""
    chase = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep", eo_weight=1.0)
    protect = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep", eo_weight=-1.0)

    def mean_eo(payload):
        rows = payload["starting"]
        return sum(r["field_eo"] for r in rows) / len(rows)

    assert mean_eo(chase) < mean_eo(protect)
    # và bám đám đông phải trả giá bằng điểm tuyệt đối, không có bữa trưa miễn phí
    assert protect["xi_xp"] <= team_svc.optimize_free_hit(
        db, budget=1000, mode="max_ep")["xi_xp"] + 1e-6


def test_eo_never_touches_the_captain_bonus(db):
    """Băng đội trưởng là phần TĂNG THÊM, mà đám đông thì không đổi vì lựa chọn đó.

    Nếu EO lọt được vào `cap_value`, người phổ biến sẽ bị phạt hai lần và đội
    trưởng bị đổi vì một lý do sai. Kiểm bằng chính dữ liệu vào của optimizer.
    """
    gw = planning_start_gw(db)
    eo = {p.id: 90.0 for p in db.scalars(select(Player)).all()}
    plain = {p.id: p for p in team_svc.build_opt_players(db, gw, "max_ep")}
    tilted = {p.id: p for p in team_svc.build_opt_players(
        db, gw, "max_ep", eo=eo, eo_weight=1.0)}

    changed_value = [i for i in plain if plain[i].value != tilted[i].value]
    changed_cap = [i for i in plain if plain[i].cap_value != tilted[i].cap_value]
    assert changed_value, "EO phải đổi được giá trị đá chính"
    assert not changed_cap, "EO không được chạm vào phần thưởng đội trưởng"


def test_free_hit_text_stops_claiming_it_ignores_ownership_when_it_does_not(db):
    off = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep")
    on = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep", eo_weight=0.5)
    assert "Không tính đám đông" in off["explanation"]["mode_desc"]
    assert "RA KHỎI" in on["explanation"]["mode_desc"]

    back = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep", eo_weight=-0.5)
    assert "VỀ PHÍA" in back["explanation"]["mode_desc"]


def test_wildcard_and_long_term_report_the_source_they_used(db):
    wc = team_svc.optimize_wildcard(db, horizon=4, eo_weight=0.5)
    assert wc["field"]["kind"] == "modelled"
    assert wc["field"]["eo_weight"] == 0.5

    squad = team_svc.optimize_free_hit(db, budget=1000)["squad_ids"]
    lt = team_svc.optimize_long_term(db, squad, horizon=3, eo_weight=0.5)
    assert lt["field"]["kind"] == "modelled"


def test_measured_league_eo_wins_over_the_modelled_one(db, monkeypatch):
    """Có số đếm được thì phải dùng số đếm được, và phải nói là đã dùng nó."""
    from app.services import league as lg

    pid = db.scalars(select(Player.id)).first()
    monkeypatch.setattr(lg, "_latest_public_gameweek", lambda _db: 1)
    monkeypatch.setattr(lg, "_fetch_rival_picks", lambda *a, **k: {
        "league_name": "Giải thử", "league_id": 7, "n_on_first_page": 2,
        "rivals": [
            {"picks": [{"element": pid, "multiplier": 2}]},
            {"picks": [{"element": pid, "multiplier": 1}]},
        ],
        "failed": [],
    })
    eo, source = field.field_eo(db, planning_start_gw(db), league_id=7)
    assert source["kind"] == "measured"
    assert source["n_rivals"] == 2
    assert eo[pid] == 150.0


def test_falls_back_to_the_modelled_source_and_says_so(db, monkeypatch):
    from app.services import league as lg

    monkeypatch.setattr(lg, "_latest_public_gameweek", lambda _db: None)
    _, source = field.field_eo(db, planning_start_gw(db), league_id=7)
    assert source["kind"] == "modelled"
    assert "rơi về nguồn này" in source["label"]

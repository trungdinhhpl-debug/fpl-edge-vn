"""Optimizer legality + behaviour tests (spec §24 acceptance criteria)."""
from sqlalchemy import select

from app.models import Player
from app.optimizer.constraints import (
    MAX_PER_CLUB,
    SQUAD_BY_TYPE,
    validate_squad,
    validate_xi,
)
from app.services import team as team_svc


def _players(db):
    return {p.id: p for p in db.scalars(select(Player)).all()}


def test_free_hit_is_a_legal_squad(db):
    players = _players(db)
    res = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep")
    ids = res["squad_ids"]
    assert len(ids) == 15
    v = validate_squad(
        ids, lambda i: players[i].element_type,
        lambda i: players[i].now_cost, lambda i: players[i].team_id,
    )
    assert v.valid, v.errors
    # budget respected
    assert sum(players[i].now_cost for i in ids) <= 1000


def test_free_hit_starting_xi_valid(db):
    players = _players(db)
    res = team_svc.optimize_free_hit(db, budget=1000, mode="balanced")
    xi_types = [players[s["id"]].element_type for s in res["starting"]]
    assert validate_xi(xi_types)
    assert res["captain"] in [s["id"] for s in res["starting"]]


def test_max_per_club_respected(db):
    players = _players(db)
    res = team_svc.optimize_free_hit(db, budget=1000, mode="aggressive")
    clubs = {}
    for i in res["squad_ids"]:
        clubs[players[i].team_id] = clubs.get(players[i].team_id, 0) + 1
    assert max(clubs.values()) <= MAX_PER_CLUB


def test_next_gw_no_free_transfer_no_hit(db):
    """With a fresh optimal squad and 1 FT, a 0-transfer solution has no hit."""
    fh = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep")
    squad = fh["squad_ids"]
    res = team_svc.optimize_next_gw(db, squad, bank=0, free_transfers=1, max_transfers=2)
    assert res["hits"] == 0 or res["n_transfers"] <= res["hits"] + 1


def test_wildcard_is_a_legal_squad_in_every_mode(db):
    players = _players(db)
    for mode in ("max_ep", "balanced", "aggressive"):
        res = team_svc.optimize_wildcard(db, budget=1000, horizon=4, mode=mode)
        ids = res["squad_ids"]
        assert len(ids) == 15, mode
        v = validate_squad(
            ids, lambda i: players[i].element_type,
            lambda i: players[i].now_cost, lambda i: players[i].team_id,
        )
        assert v.valid, (mode, v.errors)
        assert sum(players[i].now_cost for i in ids) <= 1000
        assert validate_xi([players[s["id"]].element_type for s in res["starting"]])


def test_wildcard_mode_actually_changes_the_objective(db):
    """`mode` từng được nhận vào rồi bị bỏ qua — ba nút bấm cho một kết quả.

    Không khẳng định ba đội hình phải KHÁC nhau (dữ liệu nhỏ thì trùng nhau là
    hợp lệ), mà khẳng định thứ được tối đa hoá đã khác: balanced trừ rủi ro phút
    nên tổng xP horizon của nó không thể vượt max_ep, vốn tối đa đúng đại lượng đó.
    """
    max_ep = team_svc.optimize_wildcard(db, budget=1000, horizon=4, mode="max_ep")
    balanced = team_svc.optimize_wildcard(db, budget=1000, horizon=4, mode="balanced")
    assert balanced["xi_horizon_xp"] <= max_ep["xi_horizon_xp"] + 1e-6


def test_wildcard_honours_locked_and_excluded(db):
    base = team_svc.optimize_wildcard(db, budget=1000, horizon=4, mode="max_ep")
    dropped = base["starting"][0]["id"]
    res = team_svc.optimize_wildcard(
        db, budget=1000, horizon=4, mode="max_ep", excluded={dropped},
    )
    assert dropped not in res["squad_ids"]
    assert len(res["squad_ids"]) == 15

    keep = res["bench"][0]["id"]
    locked = team_svc.optimize_wildcard(
        db, budget=1000, horizon=4, mode="max_ep", locked={keep},
    )
    assert keep in locked["squad_ids"]
    assert locked["locked"] == [keep]


def test_wildcard_reports_a_lock_it_could_not_use(db):
    """Khoá một id không tồn tại: optimizer bỏ qua, nhưng payload phải nói ra."""
    res = team_svc.optimize_wildcard(db, budget=1000, horizon=4, locked={999_999})
    assert res["locked_ignored"] == [999_999]
    assert res["locked"] == []
    assert len(res["squad_ids"]) == 15


def test_wildcard_separates_horizon_points_from_next_gameweek(db):
    res = team_svc.optimize_wildcard(db, budget=1000, horizon=5, mode="balanced")
    cap = next(s for s in res["starting"] if s["is_captain"])
    expected = sum(s["xp"] for s in res["starting"]) + cap["xp"]
    assert res["xi_xp"] == round(expected, 2)
    # cả 5 vòng phải nhiều điểm hơn một vòng
    assert res["xi_horizon_xp"] > res["xi_xp"]


def test_reported_points_are_points_not_the_solver_objective(db):
    """`xi_xp` phải luôn là điểm cộng từ xP thật, kể cả khi hàm mục tiêu bị nghiêng.

    Trước đây nó là `result.xi_value` — trùng nhau khi mục tiêu còn là xP thuần,
    nhưng bật núm EO lên thì giao diện sẽ hiện một con số bị bơm dưới nhãn "xP".
    """
    for weight in (0.0, 1.0, -1.0):
        res = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep", eo_weight=weight)
        cap = next(s for s in res["starting"] if s["is_captain"])
        expected = sum(s["xp"] for s in res["starting"]) + cap["xp"]
        assert res["xi_xp"] == round(expected, 2), weight

    # Nghiêng theo bất kỳ hướng nào cũng phải TRẢ GIÁ bằng điểm tuyệt đối, vì
    # bản không nghiêng đã tối đa đúng đại lượng đó.
    plain = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep")["xi_xp"]
    for weight in (1.0, -1.0):
        tilted = team_svc.optimize_free_hit(
            db, budget=1000, mode="max_ep", eo_weight=weight)["xi_xp"]
        assert tilted <= plain + 1e-6, weight


def test_long_term_returns_three_plans(db):
    fh = team_svc.optimize_free_hit(db, budget=1000, mode="max_ep")
    squad = fh["squad_ids"]
    res = team_svc.optimize_long_term(db, squad, bank=0, free_transfers=1, horizon=4)
    assert set(res["plans"].keys()) == {"safe", "balanced", "aggressive"}
    for plan in res["plans"].values():
        assert plan["status"] in ("Optimal", "Not Solved", "Feasible")
        assert "net_xp" in plan

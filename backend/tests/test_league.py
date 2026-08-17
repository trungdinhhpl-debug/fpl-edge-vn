"""Mini-league EO: đếm đúng, và nói đúng cái mình đang đếm.

Không lệnh gọi mạng nào ở đây — `_fetch_rival_picks` bị thay bằng đội hình dựng
sẵn, nên bài test kiểm tra PHÉP ĐẾM chứ không kiểm tra FPL API.
"""
from sqlalchemy import select

from app.models import Player
from app.services import league as lg


def _ids(db, n=6):
    return [p.id for p in db.scalars(select(Player).order_by(Player.id)).all()[:n]]


def _rivals(picks_per_rival):
    """picks_per_rival: list[dict[player_id, multiplier]] -> payload như FPL trả."""
    return {
        "league_name": "Giải thử",
        "league_id": 123,
        "n_on_first_page": len(picks_per_rival),
        "rivals": [
            {
                "entry": 1000 + i, "entry_name": f"Đội {i}", "player_name": f"Người {i}",
                "rank": i + 1, "total": 100 - i, "active_chip": None,
                "picks": [{"element": pid, "multiplier": m} for pid, m in picks.items()],
            }
            for i, picks in enumerate(picks_per_rival)
        ],
        "failed": [],
    }


def test_no_finished_gameweek_says_so_instead_of_guessing(db, monkeypatch):
    monkeypatch.setattr(lg, "_latest_public_gameweek", lambda _db: None)
    res = lg.league_analysis(db, 123)
    assert res["available"] is False
    assert res["code"] == "no_finished_gameweek"


def test_empty_standings_is_not_reported_as_a_wrong_league_code(db, monkeypatch):
    """Giải có thật nhưng chưa ai có điểm — đừng đổ cho người dùng gõ sai mã."""
    monkeypatch.setattr(lg, "_latest_public_gameweek", lambda _db: 1)
    monkeypatch.setattr(lg, "_fetch_rival_picks", lambda *a, **k: {
        "league_name": "Overall", "league_id": 314,
        "n_on_first_page": 0, "rivals": [], "failed": [],
    })
    res = lg.league_analysis(db, 314)
    assert res["code"] == "league_not_ranked_yet"
    assert "head-to-head" not in res["message"]

    # Có người trong bảng mà không đọc được ai mới là dấu hiệu sai loại giải.
    monkeypatch.setattr(lg, "_fetch_rival_picks", lambda *a, **k: {
        "league_name": "Giải H2H", "league_id": 999,
        "n_on_first_page": 12, "rivals": [], "failed": [{"entry": 1}],
    })
    res = lg.league_analysis(db, 999)
    assert res["code"] == "no_rival_squads"
    assert "head-to-head" in res["message"]


def test_eo_is_the_mean_multiplier(db, monkeypatch):
    """3 người đá chính + 1 người bắt băng => EO = (1+1+1+2)/4 = 125%."""
    pids = _ids(db)
    star = pids[0]
    monkeypatch.setattr(lg, "_fetch_rival_picks", lambda *a, **k: _rivals([
        {star: 1}, {star: 1}, {star: 1}, {star: 2},
    ]))
    res = lg.league_analysis(db, 123, squad_ids=[star])
    row = next(r for r in res["template"] if r["id"] == star)
    assert row["league_eo"] == 125.0
    assert row["league_owned_pct"] == 100.0
    assert row["league_captain_pct"] == 25.0


def test_a_benched_player_is_owned_but_carries_no_effective_ownership(db, monkeypatch):
    pids = _ids(db)
    benched = pids[1]
    monkeypatch.setattr(lg, "_fetch_rival_picks", lambda *a, **k: _rivals([
        {benched: 0}, {benched: 0},
    ]))
    res = lg.league_analysis(db, 123)
    row = next(r for r in res["players"] if r["id"] == benched)
    assert row["league_owned_pct"] == 100.0
    assert row["league_eo"] == 0.0
    # Không lọt vào lát cắt nào cả ba, nên bảng đầy đủ là chỗ duy nhất thấy được.
    assert benched not in [r["id"] for r in res["template"]]
    assert benched not in [r["id"] for r in res["missing_template"]]


def test_template_i_do_not_own_becomes_exposure_and_my_odd_pick_becomes_upside(db, monkeypatch):
    pids = _ids(db)
    theirs, mine = pids[0], pids[5]
    monkeypatch.setattr(lg, "_fetch_rival_picks", lambda *a, **k: _rivals([
        {theirs: 1}, {theirs: 1}, {theirs: 1}, {theirs: 1},
    ]))
    res = lg.league_analysis(db, 123, squad_ids=[mine])

    missing_ids = [r["id"] for r in res["missing_template"]]
    diff_ids = [r["id"] for r in res["my_differentials"]]
    assert theirs in missing_ids, "người cả giải có mà mình không có phải là hở sườn"
    assert mine in diff_ids, "người mình có mà cả giải không có phải là differential"

    # Hở sườn mang dấu âm, differential mang dấu dương — phép trừ chỉ có nghĩa
    # nếu hai bên nằm về hai phía của số 0.
    assert res["exposure_xp"] <= 0
    assert res["upside_xp"] >= 0
    assert res["net_rank_edge"] == round(res["upside_xp"] + res["exposure_xp"], 2)


def test_squad_ids_without_a_team_id_is_flagged_as_an_approximation(db, monkeypatch):
    pids = _ids(db)
    monkeypatch.setattr(lg, "_fetch_rival_picks", lambda *a, **k: _rivals([{pids[0]: 1}]))
    res = lg.league_analysis(db, 123, squad_ids=[pids[0]])
    assert res["my_squad_source"] == "squad_ids"
    assert any("đá chính" in n for n in res["notes"])


def test_measured_and_projected_gameweeks_are_reported_separately(db, monkeypatch):
    """EO đo ở vòng đã xong, xP dự báo vòng tới — trang phải nói ra cả hai mốc."""
    pids = _ids(db)
    monkeypatch.setattr(lg, "_fetch_rival_picks", lambda *a, **k: _rivals([{pids[0]: 1}]))
    res = lg.league_analysis(db, 123)
    assert res["measured_gameweek"] < res["projection_gameweek"]
    assert res["n_rivals"] == 1

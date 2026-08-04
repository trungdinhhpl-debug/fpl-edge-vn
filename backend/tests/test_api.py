"""API smoke tests via TestClient (uses the seeded demo DB)."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_model_health_ready():
    r = client.get("/api/model/health")
    assert r.status_code == 200
    assert r.json()["ready"] is True


def test_players_list_has_projections():
    r = client.get("/api/players?position=MID&limit=10")
    assert r.status_code == 200
    players = r.json()["players"]
    assert len(players) > 0
    assert "xp_next5" in players[0]
    assert "xmins" in players[0]


def test_captains_endpoint():
    r = client.get("/api/captains?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert set(body["lists"]) == {"ev", "safe", "ceiling", "chase"}
    for key, lst in body["lists"].items():
        assert lst["players"], f"list {key} is empty"
        for c in lst["players"]:
            # every metric the captaincy page promises must be present
            for field in ("xp", "xmins", "p_start", "p_blank", "p_10_plus",
                          "p_15_plus", "penalty_duty", "projected_eo",
                          "substitution_risk", "confidence"):
                assert field in c, f"{key} list missing {field}"


def test_captain_compare_endpoint():
    ev = client.get("/api/captains?limit=5").json()["lists"]["ev"]["players"]
    a, b = ev[0]["id"], ev[1]["id"]
    r = client.get(f"/api/captains/compare?a={a}&b={b}")
    assert r.status_code == 200
    body = r.json()
    assert body["a"]["id"] == a and body["b"]["id"] == b
    # every dimension lands in exactly one bucket
    total = len(body["a_better"]) + len(body["b_better"]) + len(body["even"])
    from app.services.captains import _DIMENSIONS
    assert total == len(_DIMENSIONS)
    assert body["verdict"]["pick"] in ("a", "b")
    assert body["verdict"]["reason"]


def test_captain_compare_unknown_player_is_explained():
    r = client.get("/api/captains/compare?a=999999&b=999998")
    assert r.status_code == 200
    assert "error" in r.json()


def test_free_hit_via_api_is_legal():
    r = client.post("/api/optimizer/free-hit", json={"budget": 1000, "mode": "max_ep"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["squad_ids"]) == 15
    assert len(body["starting"]) == 11
    assert body["run_id"] is not None


def test_optimization_run_retrievable():
    r = client.post("/api/optimizer/free-hit", json={"budget": 1000, "mode": "balanced"})
    run_id = r.json()["run_id"]
    r2 = client.get(f"/api/optimization/{run_id}")
    assert r2.status_code == 200
    assert r2.json()["kind"] == "free_hit"

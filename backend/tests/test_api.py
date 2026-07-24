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
    assert "candidates" in r.json()


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

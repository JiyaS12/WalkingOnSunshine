import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent
from main import app
from simulator import load_cohort, get_simulation

client = TestClient(app)


def _use_tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CACHE_PATH", tmp_path / "summaries.json")
    monkeypatch.setattr(agent, "_cache", None)
    monkeypatch.setattr(
        agent, "_stats", {"estimated_tokens_saved": 0, "cache_hits": 0}
    )


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_get_simulation_day1():
    resp = client.get("/api/get-simulation", params={"day": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["day"] == 1
    for key in ("fps", "frame_count", "metrics", "frames"):
        assert key in body
    assert "fall_risk_score" in body["metrics"]


def test_get_simulation_comparison():
    resp = client.get("/api/get-simulation")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("patient_id", "day_1", "day_14", "deltas"):
        assert key in body


def test_get_simulation_bad_day():
    resp = client.get("/api/get-simulation", params={"day": 99})
    assert resp.status_code == 404


def test_process_frame_matches_simulator():
    frames = load_cohort()["sessions"]["day_14"]["frames"]
    resp = client.post("/api/process-frame",
                       json={"frames": frames, "fps": 30})
    assert resp.status_code == 200
    expected = get_simulation(14)["metrics"]
    for key, value in resp.json().items():
        assert value == expected[key]


def test_process_frame_too_few():
    resp = client.post("/api/process-frame", json={"frames": []})
    assert resp.status_code == 422


def test_generate_summary(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    resp = client.post("/api/generate-summary", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert "summary" in body
    assert body["source"] in ("template", "openai")
    assert body["cached"] is False


def test_generate_summary_cached_on_second_call(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    first = client.post("/api/generate-summary", json={}).json()
    assert first["cached"] is False
    second = client.post("/api/generate-summary", json={}).json()
    assert second["cached"] is True
    assert second["summary"] == first["summary"]
    assert second["estimated_tokens_saved"] > 0


def test_summary_cache_stats(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    resp = client.get("/api/summary-cache-stats")
    assert resp.status_code == 200
    assert resp.json() == {
        "entries": 0,
        "cache_hits": 0,
        "estimated_tokens_saved": 0,
    }
    client.post("/api/generate-summary", json={})
    client.post("/api/generate-summary", json={})
    stats = client.get("/api/summary-cache-stats").json()
    assert stats["entries"] == 1
    assert stats["cache_hits"] == 1
    assert stats["estimated_tokens_saved"] > 0

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent
import clinician_auth
import gait_gen
import store
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINICIAN_USERNAME", "test-clinician")
    monkeypatch.setenv("CLINICIAN_PASSWORD", "test-password")
    monkeypatch.setenv("CLINICIAN_SESSION_SECRET", "s" * 32)
    clinician_auth.reset_login_rate_limits()
    client.cookies.clear()
    login = client.post(
        "/api/clinician/session",
        json={"username": "test-clinician", "password": "test-password"},
    )
    assert login.status_code == 200
    store.reset_for_tests(tmp_path / "patients.json")
    yield
    client.cookies.clear()
    store.reset_for_tests(store.CACHE_PATH)


def _use_tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CACHE_PATH", tmp_path / "summaries.json")
    monkeypatch.setattr(agent, "_cache", None)
    monkeypatch.setattr(
        agent, "_stats", {"estimated_tokens_saved": 0, "cache_hits": 0}
    )


def _metrics(**kw):
    base = {
        "stride_length_m": 1.0,
        "asymmetry_pct": 5.0,
        "velocity_degradation_pct": 2.0,
        "fall_risk_score": 0.4,
        "cadence_steps_per_min": 100.0,
        "frame_count": 100,
        "leg_length_m": 0.9,
        "stride_ratio": 1.1,
        "knee_flexion_rom_deg": 40.0,
        "peak_ankle_speed_mps": 3.0,
        "gait_detected": True,
        "dropped_frame_pct": 0.0,
    }
    base.update(kw)
    return base


def _survey(pid="RGN-0999", **kw):
    base = {
        "patient_id": pid,
        "patient_name": "Test Patient",
        "pain_scale": 5,
        "fall_history": {"falls_last_6_months": 1, "injured": False},
        "dizziness": False,
        "primary_complaints": ["knee pain"],
    }
    base.update(kw)
    return base


def _patient_with_session(pid="RGN-0999"):
    client.post("/api/submit-survey", json=_survey(pid))
    client.post(
        f"/api/patients/{pid}/sessions",
        json={"label": "S1", "source": "live", "metrics": _metrics()},
    )


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_process_frame():
    frames = gait_gen.recovered_session()["frames"]
    resp = client.post("/api/process-frame",
                       json={"frames": frames, "fps": 30})
    assert resp.status_code == 200
    body = resp.json()
    assert "fall_risk_score" in body
    assert body["gait_detected"] is True


def test_process_frame_with_leg_length():
    frames = gait_gen.recovered_session()["frames"][:100]
    resp = client.post(
        "/api/process-frame",
        json={"frames": frames, "fps": 30, "leg_length_m": 0.9},
    )
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "leg_length_m",
        "stride_ratio",
        "knee_flexion_rom_deg",
        "peak_ankle_speed_mps",
        "gait_detected",
    ):
        assert key in body
    assert body["leg_length_m"] == 0.9


def test_process_frame_too_few():
    resp = client.post("/api/process-frame", json={"frames": []})
    assert resp.status_code == 422


def test_process_frame_survives_dropped_landmarks():
    """A dropped landmark must not mask a high-risk reading."""
    frames = json.loads(json.dumps(gait_gen.impaired_session()["frames"][:120]))
    clean = client.post(
        "/api/process-frame", json={"frames": frames, "fps": 30}
    ).json()
    assert clean["gait_detected"] is True

    for i in (3, 4, 41):
        frames[i]["left_ankle"][1] = float("nan")
    resp = client.post("/api/process-frame",
                       content=json.dumps({"frames": frames, "fps": 30}),
                       headers={"content-type": "application/json"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["gait_detected"] is True
    assert body["dropped_frame_pct"] > 0
    assert abs(body["fall_risk_score"] - clean["fall_risk_score"]) < 0.1


def test_generate_summary_for_patient(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    _patient_with_session()
    resp = client.post("/api/generate-summary", json={"patient_id": "RGN-0999"})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["summary"], str)
    assert body["source"] in ("template", "openai")
    assert body["cached"] is False


def test_generate_summary_single_session_no_trend(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    _patient_with_session()
    resp = client.post("/api/generate-summary", json={"patient_id": "RGN-0999"})
    assert resp.status_code == 200
    summary = resp.json()["summary"].lower()
    assert "improved" not in summary
    assert "worsened" not in summary


def test_generate_summary_identical_sessions_unchanged(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    _patient_with_session()
    client.post(
        "/api/patients/RGN-0999/sessions",
        json={"label": "S2", "source": "live", "metrics": _metrics()},
    )
    resp = client.post("/api/generate-summary", json={"patient_id": "RGN-0999"})
    assert resp.status_code == 200
    assert "unchanged" in resp.json()["summary"].lower()


def test_ensure_demo_creates_patient(monkeypatch, tmp_path):
    resp = client.post("/api/patients/NEW-001/ensure-demo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    assert body["patient"]["patient_id"] == "NEW-001"
    assert len(body["patient"]["surveys"]) == 1
    assert body["patient"]["surveys"][0]["pain_scale"] == 3


def test_ensure_demo_idempotent(monkeypatch, tmp_path):
    first = client.post("/api/patients/NEW-002/ensure-demo")
    assert first.json()["created"] is True
    second = client.post("/api/patients/NEW-002/ensure-demo")
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert len(second.json()["patient"]["surveys"]) == 1


def test_ensure_demo_seeded_patient_unchanged(monkeypatch, tmp_path):
    before = store.get_patient("RGN-0417")
    resp = client.post("/api/patients/RGN-0417/ensure-demo")
    assert resp.status_code == 200
    assert resp.json()["created"] is False
    after = store.get_patient("RGN-0417")
    assert len(after["surveys"]) == len(before["surveys"])


def test_ensure_demo_invalid_id_422(monkeypatch, tmp_path):
    resp = client.post("/api/patients/bad id!/ensure-demo")
    assert resp.status_code == 422


def test_ensure_patient_sequential_created_flag(monkeypatch, tmp_path):
    record, created = store.ensure_patient(
        "NEW-003", {"patient_name": "New", "pain_scale": 3}
    )
    assert created is True
    record2, created2 = store.ensure_patient(
        "NEW-003", {"patient_name": "New", "pain_scale": 3}
    )
    assert created2 is False
    assert len(record["surveys"]) == 1
    assert len(record2["surveys"]) == 1


def test_ensure_demo_blocked_when_token_set(monkeypatch):
    monkeypatch.setenv("SURVEY_INGEST_TOKEN", "secret")
    monkeypatch.delenv("ALLOW_DEMO_PATIENTS", raising=False)
    resp = client.post("/api/patients/NEW-004/ensure-demo")
    assert resp.status_code == 403
    assert "ALLOW_DEMO_PATIENTS" in resp.json()["detail"]
    existing = client.post("/api/patients/RGN-0417/ensure-demo")
    assert existing.status_code == 200
    assert existing.json()["created"] is False


def test_ensure_demo_allowed_with_opt_in(monkeypatch):
    monkeypatch.setenv("SURVEY_INGEST_TOKEN", "secret")
    monkeypatch.setenv("ALLOW_DEMO_PATIENTS", "1")
    resp = client.post("/api/patients/NEW-005/ensure-demo")
    assert resp.status_code == 200
    assert resp.json()["created"] is True


def test_generate_summary_unknown_patient_404(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    resp = client.post("/api/generate-summary", json={"patient_id": "NOPE"})
    assert resp.status_code == 404


def test_generate_summary_no_sessions_422(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    client.post("/api/submit-survey", json=_survey())
    resp = client.post("/api/generate-summary", json={"patient_id": "RGN-0999"})
    assert resp.status_code == 422


def _mark_cache_openai():
    # pretend the first summary came from the OpenAI path so token savings
    # accrue on subsequent hits
    for entry in agent._cache.values():
        entry["source"] = "openai"


def test_generate_summary_cached_on_second_call(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    _patient_with_session()
    payload = {"patient_id": "RGN-0999"}
    first = client.post("/api/generate-summary", json=payload).json()
    assert first["cached"] is False
    _mark_cache_openai()
    second = client.post("/api/generate-summary", json=payload).json()
    assert second["cached"] is True
    assert second["summary"] == first["summary"]
    assert second["estimated_tokens_saved"] > 0


def test_template_cache_hit_saves_nothing(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    _patient_with_session()
    payload = {"patient_id": "RGN-0999"}
    client.post("/api/generate-summary", json=payload)
    hit = client.post("/api/generate-summary", json=payload).json()
    assert hit["cached"] is True
    assert hit["source"] == "template"
    assert hit["estimated_tokens_saved"] == 0


def test_summary_cache_stats(monkeypatch, tmp_path):
    _use_tmp_cache(monkeypatch, tmp_path)
    _patient_with_session()
    resp = client.get("/api/summary-cache-stats")
    assert resp.status_code == 200
    assert resp.json() == {
        "entries": 0,
        "cache_hits": 0,
        "estimated_tokens_saved": 0,
    }
    payload = {"patient_id": "RGN-0999"}
    client.post("/api/generate-summary", json=payload)
    _mark_cache_openai()
    client.post("/api/generate-summary", json=payload)
    stats = client.get("/api/summary-cache-stats").json()
    assert stats["entries"] == 1
    assert stats["cache_hits"] == 1
    assert stats["estimated_tokens_saved"] > 0

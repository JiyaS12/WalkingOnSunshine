import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import store
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path):
    store.reset_for_tests(tmp_path / "patients.json")
    yield
    store.reset_for_tests(store.CACHE_PATH)


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


def test_submit_survey_creates_patient():
    resp = client.post("/api/submit-survey", json=_survey())
    assert resp.status_code == 200
    assert resp.json()["status"] == "stored"
    patient = resp.json()["patient"]
    assert patient["patient_id"] == "RGN-0999"
    assert len(patient["surveys"]) == 1

    resp2 = client.post("/api/submit-survey", json=_survey(pain_scale=7))
    assert len(resp2.json()["patient"]["surveys"]) == 2


def test_survey_validation_422():
    resp = client.post("/api/submit-survey", json=_survey(pain_scale=11))
    assert resp.status_code == 422


def test_survey_token_enforced(monkeypatch):
    monkeypatch.setenv("SURVEY_INGEST_TOKEN", "sekret")
    assert client.post("/api/submit-survey", json=_survey()).status_code == 401
    resp = client.post(
        "/api/submit-survey",
        json=_survey(),
        headers={"X-Survey-Token": "sekret"},
    )
    assert resp.status_code == 200


def test_list_patients_search_by_complaint():
    client.post("/api/submit-survey", json=_survey(primary_complaints=["vertigo"]))
    rows = client.get("/api/patients", params={"q": "vertigo"}).json()
    assert any(r["patient_id"] == "RGN-0999" for r in rows)
    rows = client.get("/api/patients", params={"q": "nosuchthing"}).json()
    assert all(r["patient_id"] != "RGN-0999" for r in rows)


def test_get_patient_404():
    assert client.get("/api/patients/NOPE").status_code == 404


def test_add_session_404():
    body = {"label": "S1", "source": "live", "metrics": _metrics()}
    assert client.post("/api/patients/NOPE/sessions", json=body).status_code == 404


def test_add_session_frames_cap():
    client.post("/api/submit-survey", json=_survey())
    body = {
        "label": "S1",
        "source": "live",
        "metrics": _metrics(),
        "frames": [{"left_hip": [0, 0, 0]}] * 301,
    }
    resp = client.post("/api/patients/RGN-0999/sessions", json=body)
    assert resp.status_code == 422


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
    }
    base.update(kw)
    return base


def test_synthesis_template_deterministic():
    client.post("/api/submit-survey", json=_survey())
    client.post(
        "/api/patients/RGN-0999/sessions",
        json={"label": "S1", "source": "live", "metrics": _metrics()},
    )
    r1 = client.post("/api/patients/RGN-0999/synthesis")
    r2 = client.post("/api/patients/RGN-0999/synthesis")
    assert r1.status_code == 200
    body1, body2 = r1.json(), r2.json()
    assert body1["summary"] == body2["summary"]
    assert body1["source"] == "template"
    assert body2["cached"] is True
    assert "Subjective:" in body1["summary"]


def test_synthesis_422_no_survey():
    client.post("/api/submit-survey", json=_survey())
    store.add_session(
        "RGN-0999",
        {"label": "x", "source": "live", "metrics": _metrics()},
    )
    # has survey + session -> ok; create patient without session
    client.post("/api/submit-survey", json=_survey("RGN-1111"))
    assert client.post("/api/patients/RGN-1111/synthesis").status_code == 422


def test_rgn0417_detail_includes_frames():
    resp = client.get("/api/patients/RGN-0417")
    assert resp.status_code == 200
    sessions = resp.json()["gait_sessions"]
    assert len(sessions) == 2
    for s in sessions:
        assert s["frames"] and len(s["frames"]) > 0
        assert s["metrics"]["gait_detected"] is True

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clinician_auth
import store
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINICIAN_USERNAME", "test-clinician")
    monkeypatch.setenv("CLINICIAN_PASSWORD", "test-password")
    monkeypatch.setenv("CLINICIAN_SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("CLINICIAN_COOKIE_SECURE", "false")
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


def test_surveys_sorted_by_recorded_at():
    client.post(
        "/api/submit-survey",
        json=_survey(recorded_at="2026-09-10T00:00:00+00:00", pain_scale=3),
    )
    client.post(
        "/api/submit-survey",
        json=_survey(recorded_at="2026-09-05T00:00:00+00:00", pain_scale=8),
    )
    rows = client.get("/api/patients", params={"q": "RGN-0999"}).json()
    row = next(r for r in rows if r["patient_id"] == "RGN-0999")
    # latest by timestamp is the Sep-10 survey -> pain_scale 3
    assert row["pain_scale"] == 3
    assert row["latest_survey_at"].startswith("2026-09-10")


def test_add_session_malformed_frame_422():
    client.post("/api/submit-survey", json=_survey())
    body = {
        "label": "S1",
        "source": "live",
        "metrics": _metrics(),
        "frames": [{"left_hip": [0, 0, 0]}] * 10,
    }
    resp = client.post("/api/patients/RGN-0999/sessions", json=body)
    assert resp.status_code == 422
    assert "hip" in resp.json()["detail"]


def test_complaint_length_422():
    resp = client.post(
        "/api/submit-survey", json=_survey(primary_complaints=["x" * 200])
    )
    assert resp.status_code == 422


def test_persist_failure_rolls_back(monkeypatch):
    client.post("/api/submit-survey", json=_survey(patient_name="Original"))
    before = store.get_patient("RGN-0999")
    name_before, n_surveys = before["name"], len(before["surveys"])

    monkeypatch.setattr(
        store, "_persist", lambda: (_ for _ in ()).throw(OSError("disk full"))
    )
    resp = client.post(
        "/api/submit-survey",
        json=_survey(patient_name="Renamed", pain_scale=9),
    )
    assert resp.status_code == 500
    after = store.get_patient("RGN-0999")
    assert after["name"] == name_before
    assert len(after["surveys"]) == n_surveys


def test_rgn0417_detail_includes_metrics():
    resp = client.get("/api/patients/RGN-0417")
    assert resp.status_code == 200
    sessions = resp.json()["gait_sessions"]
    assert len(sessions) == 2
    for s in sessions:
        assert s["metrics"]["gait_detected"] is True
        assert s["metrics"]["fall_risk_score"] > 0

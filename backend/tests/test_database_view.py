from copy import deepcopy
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import clinician_auth
import store
from main import app


@pytest.fixture
def database(monkeypatch):
    records = {
        "demo-1": {
            "patient_id": "demo-1", "name": "Synthetic patient", "secret": "DO-NOT-EXPOSE",
            "calls": [{"patient_id": "demo-1", "call_id": "call-1", "attempt_id": "attempt-1",
                       "call_status": "completed", "survey_status": "stored", "survey_skipped": ["hoos_rising"],
                       "_destination_phone": "+15555550123",
                       "_fingerprint": "DO-NOT-EXPOSE", "_phone_snapshot": {"token": "DO-NOT-EXPOSE"},
                       "walking": {"status": "saved", "events": [{"event_id": "event-1", "event": "page_ready", "token": "DO-NOT-EXPOSE"}]}}],
            "surveys": [{"patient_id": "demo-1", "call_id": "call-1", "pain_scale": 0,
                         "_fingerprint": "DO-NOT-EXPOSE", "condition_survey": {
                             "instrument": "hoos_jr", "skipped": ["hoos_rising"],
                             "answers": [{"question_id": "hoos_stairs", "normalized_value": "none", "confirmed": True, "token": "DO-NOT-EXPOSE"}]}}],
            "gait_sessions": [{"session_id": "walk-1", "call_id": "call-1", "attempt_id": "attempt-1",
                               "metrics": {"fall_risk_score": 0, "stride_length_m": None, "secret": "DO-NOT-EXPOSE"},
                               "frames": [{"example": [1, 2, 3]}], "frames_ref": "DO-NOT-EXPOSE"}],
        },
        "demo-2": {"patient_id": "demo-2", "name": "Another synthetic patient", "surveys": [], "gait_sessions": []},
    }
    monkeypatch.setattr(store, "_patients", records)
    monkeypatch.setattr(store, "_test_json_store", True)
    monkeypatch.setenv("CLINICIAN_USERNAME", "db-doctor")
    monkeypatch.setenv("CLINICIAN_PASSWORD", "test-password")
    monkeypatch.setenv("CLINICIAN_SESSION_SECRET", "d" * 32)
    monkeypatch.setenv("CLINICIAN_COOKIE_SECURE", "false")
    clinician_auth.reset_login_rate_limits()
    with TestClient(app) as client:
        yield client, records


def login(client):
    assert client.post("/api/clinician/session", json={"username": "db-doctor", "password": "test-password"}).status_code == 200


def test_database_requires_clinician_not_patient_or_service_credentials(database):
    client, _ = database
    for headers in ({}, {"Authorization": "Bearer patient-token"}, {"X-Survey-Token": "service-token"}):
        response = client.get("/api/clinician/database", headers=headers)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert "Synthetic patient" not in response.text


def test_database_projection_is_read_only_bounded_and_excludes_internal_secrets(database):
    client, records = database
    before = deepcopy(records)
    login(client)
    response = client.get("/api/clinician/database?limit=1")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["read_only"] is True and body["source"] == "json"
    assert body["total"] == 2 and len(body["patients"]) == 1
    assert body["totals"] == {"patients": 2, "calls": 1, "surveys": 1, "walking": 1}
    patient = body["patients"][0]
    assert patient["calls"][0]["destination_phone"] == "+15555550123"
    assert patient["calls"][0]["survey_skipped"] == ["hoos_rising"]
    assert patient["surveys"][0]["condition_survey"]["skipped"] == ["hoos_rising"]
    assert patient["gait_sessions"][0]["stored_frame_count"] == 1
    assert patient["gait_sessions"][0]["metrics"]["fall_risk_score"] == 0
    assert patient["gait_sessions"][0]["metrics"]["stride_length_m"] is None
    assert "DO-NOT-EXPOSE" not in response.text
    assert '"frames"' not in response.text
    assert records == before
    assert client.post("/api/clinician/database", json={}).status_code == 405
    public = client.get("/api/patients/demo-1").json()
    assert "destination_phone" not in public["calls"][0]
    assert "_destination_phone" not in public["calls"][0]


def test_database_search_pagination_and_empty_state(database):
    client, _ = database
    login(client)
    for query in ("5555550123", "CALL-1", "demo-1"):
        result = client.get("/api/clinician/database", params={"q": query}).json()
        assert [p["patient_id"] for p in result["patients"]] == ["demo-1"]
    assert client.get("/api/clinician/database?limit=1&offset=1").json()["patients"][0]["patient_id"] == "demo-2"
    assert client.get("/api/clinician/database?q=missing").json()["total"] == 0
    for query in ("limit=101", "offset=-1", "limit=0", "q=" + "a" * 201):
        assert client.get("/api/clinician/database?" + query).status_code == 422


def test_database_failure_is_not_reported_as_empty_success(database, monkeypatch):
    client, _ = database
    login(client)
    def fail():
        raise store.StoreUnavailable("private provider details")
    monkeypatch.setattr(store, "database_records", fail)
    response = client.get("/api/clinician/database")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "private provider details" not in response.text


def test_database_reads_fresh_supabase_without_replacing_call_cache(database, monkeypatch):
    client, cached = database
    login(client)
    fresh = deepcopy(cached)
    fresh["demo-1"]["name"] = "Fresh database name"
    class Reader:
        def load(self):
            return fresh
    monkeypatch.setattr(store, "_test_json_store", False)
    monkeypatch.setenv("PATIENT_STORE", "supabase")
    monkeypatch.setattr(store.SupabasePatientStore, "from_env", classmethod(lambda cls: Reader()))
    body = client.get("/api/clinician/database").json()
    assert body["source"] == "supabase"
    assert body["patients"][0]["name"] == "Fresh database name"
    assert store._patients is cached and cached["demo-1"]["name"] == "Synthetic patient"


def test_database_supabase_outage_does_not_return_cached_json(database, monkeypatch):
    client, _ = database
    login(client)
    class Unavailable:
        def load(self):
            raise OSError("private provider details")
    monkeypatch.setattr(store, "_test_json_store", False)
    monkeypatch.setenv("PATIENT_STORE", "supabase")
    monkeypatch.setattr(store.SupabasePatientStore, "from_env", classmethod(lambda cls: Unavailable()))
    response = client.get("/api/clinician/database")
    assert response.status_code == 503
    assert "Synthetic patient" not in response.text

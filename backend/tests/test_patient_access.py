import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import patient_access
import store
from main import app


client = TestClient(app)
_FIXED_NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
_UNIT_SECRET = b"u" * 32
_NEW_PID = "RGN-7001"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path):
    path = tmp_path / "patients.json"
    store.reset_for_tests(path)
    yield path
    store.reset_for_tests(store.CACHE_PATH)


def _survey(pid: str, name: str = "Test Patient") -> dict:
    return {
        "patient_id": pid,
        "patient_name": name,
        "pain_scale": 5,
        "fall_history": {"falls_last_6_months": 1, "injured": False},
        "dizziness": False,
        "primary_complaints": ["knee pain"],
    }


def _metrics() -> dict:
    return {
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


def _session(label: str = "S1", recorded_at: str | None = None) -> dict:
    body = {"label": label, "source": "live", "metrics": _metrics()}
    if recorded_at is not None:
        body["recorded_at"] = recorded_at
    return body


def _token_for(pid: str) -> str:
    token, _ = patient_access.generate_token(pid)
    return token


def _auth(pid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token_for(pid)}"}


def _create(pid: str, name: str = "Test Patient") -> dict:
    response = client.post("/api/submit-survey", json=_survey(pid, name))
    assert response.status_code == 200
    return response.json()


def test_token_generation_and_verification_are_deterministic():
    first, expires_at = patient_access.generate_token(
        "RGN-0417", now=_FIXED_NOW, ttl_seconds=900, secret=_UNIT_SECRET
    )
    second, _ = patient_access.generate_token(
        "RGN-0417", now=_FIXED_NOW, ttl_seconds=900, secret=_UNIT_SECRET
    )

    assert first == second
    assert expires_at == _FIXED_NOW + timedelta(seconds=900)
    patient_access.verify_token(
        first,
        "RGN-0417",
        now=_FIXED_NOW + timedelta(seconds=899),
        secret=_UNIT_SECRET,
    )


def test_token_expires_at_exact_expiration():
    token, expires_at = patient_access.generate_token(
        "RGN-0417", now=_FIXED_NOW, ttl_seconds=60, secret=_UNIT_SECRET
    )

    with pytest.raises(patient_access.PatientAccessError):
        patient_access.verify_token(
            token, "RGN-0417", now=expires_at, secret=_UNIT_SECRET
        )


def test_token_with_fractional_issuance_gets_full_lifetime():
    issued_at = _FIXED_NOW.replace(microsecond=900_000)
    token, expires_at = patient_access.generate_token(
        "RGN-0417", now=issued_at, ttl_seconds=60, secret=_UNIT_SECRET
    )

    assert expires_at > issued_at + timedelta(seconds=60)
    assert expires_at <= issued_at + timedelta(seconds=61)
    patient_access.verify_token(
        token,
        "RGN-0417",
        now=issued_at + timedelta(seconds=60),
        secret=_UNIT_SECRET,
    )


@pytest.mark.parametrize("mutation", ["payload", "signature"])
def test_token_tampering_is_rejected(mutation):
    token, _ = patient_access.generate_token(
        "RGN-0417", now=_FIXED_NOW, ttl_seconds=900, secret=_UNIT_SECRET
    )
    payload, signature = token.split(".")
    if mutation == "payload":
        payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    else:
        signature = ("A" if signature[0] != "A" else "B") + signature[1:]

    with pytest.raises(patient_access.PatientAccessError):
        patient_access.verify_token(
            f"{payload}.{signature}",
            "RGN-0417",
            now=_FIXED_NOW,
            secret=_UNIT_SECRET,
        )


def test_token_is_bound_to_one_patient():
    token, _ = patient_access.generate_token(
        "RGN-0417", now=_FIXED_NOW, ttl_seconds=900, secret=_UNIT_SECRET
    )

    with pytest.raises(patient_access.PatientAccessError):
        patient_access.verify_token(
            token, "RGN-9999", now=_FIXED_NOW, secret=_UNIT_SECRET
        )


@pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c", "@@@.@@@"])
def test_malformed_tokens_are_rejected(token):
    with pytest.raises(patient_access.PatientAccessError):
        patient_access.verify_token(
            token, "RGN-0417", now=_FIXED_NOW, secret=_UNIT_SECRET
        )


def test_survey_response_contains_complete_patient_url(isolated_store):
    response = client.post("/api/submit-survey", json=_survey("RGN-0417"))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    parsed = urlsplit(body["patient_url"])
    token = parse_qs(parsed.query)["token"][0]

    assert parsed.scheme == "https"
    assert parsed.netloc == "patient.example.test"
    assert parsed.path == "/patient/RGN-0417"
    assert body["patient_access_expires_at"].endswith("+00:00")
    patient_access.verify_token(token, "RGN-0417")

    persisted = isolated_store.read_text()
    assert token not in persisted
    assert "test-only-patient-link-secret" not in persisted


def test_survey_fails_closed_before_storage_without_signing_secret(
    monkeypatch, isolated_store
):
    monkeypatch.delenv("PATIENT_LINK_SIGNING_SECRET")

    response = client.post("/api/submit-survey", json=_survey("RGN-NEW"))

    assert response.status_code == 503
    assert response.json() == {"detail": "patient access is not configured"}
    assert not isolated_store.exists() or "RGN-NEW" not in json.loads(
        isolated_store.read_text()
    )


def test_survey_ingestion_fails_closed_without_auth_configuration(monkeypatch):
    monkeypatch.delenv("SURVEY_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED_SURVEY_INGEST", raising=False)

    response = client.post("/api/submit-survey", json=_survey("RGN-NEW"))

    assert response.status_code == 503
    assert response.json() == {"detail": "survey ingestion is not configured"}


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("PATIENT_LINK_SIGNING_SECRET", "too-short"),
        ("PATIENT_LINK_TTL_SECONDS", "59"),
        ("PATIENT_LINK_TTL_SECONDS", "604801"),
        ("PATIENT_LINK_TTL_SECONDS", "not-an-integer"),
        ("PATIENT_APP_BASE_URL", "http://patient.example.test"),
        ("PATIENT_APP_BASE_URL", "https://user:password@patient.example.test"),
    ],
)
def test_unsafe_link_configuration_is_rejected(monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)

    with pytest.raises(patient_access.PatientAccessConfigurationError):
        patient_access.create_patient_link("RGN-0417")


def test_patient_scoped_read_returns_minimum_view_only():
    _create(_NEW_PID, "Linked Patient")

    response = client.get(
        f"/api/patient-access/{_NEW_PID}", headers=_auth(_NEW_PID)
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "patient_id": _NEW_PID,
        "name": "Linked Patient",
        "gait_sessions": [],
    }
    assert "surveys" not in response.json()
    assert "cohort" not in response.json()


def test_patient_token_cannot_read_or_write_another_patient():
    _create(_NEW_PID)
    _create("RGN-7002")
    headers = _auth(_NEW_PID)

    read = client.get("/api/patient-access/RGN-7002", headers=headers)
    write = client.post(
        "/api/patient-access/RGN-7002/sessions",
        headers=headers,
        json=_session(),
    )

    assert read.status_code == write.status_code == 401
    assert read.json() == write.json() == {
        "detail": "invalid or expired patient access"
    }
    assert store.get_patient("RGN-7002")["gait_sessions"] == []


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic abc", "Bearer", "Bearer malformed", "Bearer a.b.c"],
)
def test_missing_and_malformed_api_credentials_are_non_leaky(authorization):
    _create(_NEW_PID)
    headers = {"Authorization": authorization} if authorization else {}

    response = client.get(f"/api/patient-access/{_NEW_PID}", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid or expired patient access"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_expired_api_credential_is_non_leaky():
    _create(_NEW_PID)
    token, _ = patient_access.generate_token(
        _NEW_PID,
        now=datetime(2020, 1, 1, tzinfo=timezone.utc),
        ttl_seconds=60,
    )

    response = client.get(
        f"/api/patient-access/{_NEW_PID}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid or expired patient access"}


def test_patient_scoped_session_write_happy_path():
    _create(_NEW_PID)

    response = client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        headers=_auth(_NEW_PID),
        json=_session(),
    )

    assert response.status_code == 200
    assert len(response.json()["gait_sessions"]) == 1
    assert len(store.get_patient(_NEW_PID)["gait_sessions"]) == 1
    assert "surveys" not in response.json()


def test_patient_scoped_session_write_preserves_frame_validation_and_cap():
    _create(_NEW_PID)
    headers = _auth(_NEW_PID)
    malformed = _session()
    malformed["frames"] = [{"left_hip": [0, 0, 0]}] * 10
    oversized = _session()
    oversized["frames"] = [{"left_hip": [0, 0, 0]}] * 301

    malformed_response = client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        headers=headers,
        json=malformed,
    )
    oversized_response = client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        headers=headers,
        json=oversized,
    )

    assert malformed_response.status_code == 422
    assert oversized_response.status_code == 422
    assert store.get_patient(_NEW_PID)["gait_sessions"] == []


def test_patient_scoped_session_write_preserves_ordering():
    _create(_NEW_PID)
    headers = _auth(_NEW_PID)

    client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        headers=headers,
        json=_session("Later", "2030-01-02T00:00:00Z"),
    )
    response = client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        headers=headers,
        json=_session("Earlier", "2030-01-01T00:00:00Z"),
    )

    assert response.status_code == 200
    assert [item["label"] for item in response.json()["gait_sessions"]] == [
        "Earlier",
        "Later",
    ]


def test_patient_scoped_session_write_preserves_session_limit():
    _create(_NEW_PID)
    for index in range(50):
        store.add_session(_NEW_PID, _session(f"S{index}"))

    response = client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        headers=_auth(_NEW_PID),
        json=_session("Overflow"),
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "patient already has 50 sessions"}
    assert len(store.get_patient(_NEW_PID)["gait_sessions"]) == 50


def test_patient_scoped_session_write_rolls_back_on_persist_failure(monkeypatch):
    _create(_NEW_PID)
    before = store.get_patient(_NEW_PID)
    monkeypatch.setattr(
        store, "_persist", lambda: (_ for _ in ()).throw(OSError("disk full"))
    )

    response = client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        headers=_auth(_NEW_PID),
        json=_session(),
    )

    assert response.status_code == 500
    assert store.get_patient(_NEW_PID) == before

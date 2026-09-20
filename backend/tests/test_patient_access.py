import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clinician_auth
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


def test_patient_scoped_session_write_is_idempotent_across_retries():
    _create(_NEW_PID)
    body = _session()
    body["idempotency_key"] = "live_0123456789abcdef"
    headers = _auth(_NEW_PID)

    first = client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        headers=headers,
        json=body,
    )
    second = client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        headers=headers,
        json=body,
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    sessions = store.get_patient(_NEW_PID)["gait_sessions"]
    assert len(sessions) == 1
    assert sessions[0]["idempotency_key"] == body["idempotency_key"]


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


def _verify(pid: str, token: str | None = None):
    return client.get(
        "/api/auth/verify",
        params={"patient_id": pid, "token": token or _token_for(pid)},
    )


def test_magic_link_verify_issues_patient_session_cookie(monkeypatch):
    monkeypatch.setenv("CLINICIAN_COOKIE_SECURE", "false")
    _create(_NEW_PID)

    response = _verify(_NEW_PID)

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["patient_id"] == _NEW_PID
    cookie = response.headers["set-cookie"]
    assert "sana_patient_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert response.headers["cache-control"] == "no-store"
    # the session outlives the short-lived link token
    assert body["expires_at"] > int(
        patient_access.generate_token(_NEW_PID)[1].timestamp()
    )


def test_magic_link_session_cookie_grants_scoped_access(monkeypatch):
    monkeypatch.setenv("CLINICIAN_COOKIE_SECURE", "false")
    _create(_NEW_PID)
    _create("RGN-7002", "Other")
    assert _verify(_NEW_PID).status_code == 200  # cookie stored on client

    read = client.get(f"/api/patient-access/{_NEW_PID}")
    assert read.status_code == 200
    assert set(read.json()) == {"patient_id", "name", "gait_sessions"}

    other = client.get("/api/patient-access/RGN-7002")
    assert other.status_code == 401

    write = client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        json=_session("Cookie save"),
        headers={"Origin": "http://localhost:3000"},
    )
    assert write.status_code == 200
    assert [s["label"] for s in write.json()["gait_sessions"]] == ["Cookie save"]

    forged = client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        json=_session("CSRF"),
        headers={"Origin": "https://evil.example"},
    )
    assert forged.status_code == 403

    assert client.delete(
        "/api/auth/verify", headers={"Origin": "http://localhost:3000"}
    ).status_code == 204
    assert client.get(f"/api/patient-access/{_NEW_PID}").status_code == 401


@pytest.mark.parametrize(
    "token", ["not-a-token", "", None]
)
def test_magic_link_verify_rejects_bad_tokens(token):
    _create(_NEW_PID)
    bad = _token_for("RGN-9999") if token is None else token
    response = client.get(
        "/api/auth/verify", params={"patient_id": _NEW_PID, "token": bad}
    )
    assert response.status_code in {401, 422}
    assert "set-cookie" not in response.headers


def test_magic_link_verify_requires_existing_patient():
    response = _verify(_NEW_PID)
    assert response.status_code == 404
    assert "set-cookie" not in response.headers


def test_magic_link_verify_fails_closed_without_signing_secret(monkeypatch):
    _create(_NEW_PID)
    token = _token_for(_NEW_PID)
    monkeypatch.delenv("PATIENT_LINK_SIGNING_SECRET")
    response = _verify(_NEW_PID, token)
    assert response.status_code == 503


def test_magic_link_session_can_generate_patient_summary(monkeypatch):
    monkeypatch.setenv("CLINICIAN_COOKIE_SECURE", "false")
    _create(_NEW_PID)
    assert _verify(_NEW_PID).status_code == 200
    origin = {"Origin": "http://localhost:3000"}

    empty = client.post(f"/api/patient-access/{_NEW_PID}/summary", headers=origin)
    assert empty.status_code == 422

    assert client.post(
        f"/api/patient-access/{_NEW_PID}/sessions",
        json=_session("S1"),
        headers=origin,
    ).status_code == 200
    summary = client.post(
        f"/api/patient-access/{_NEW_PID}/summary", headers=origin
    )
    assert summary.status_code == 200
    assert summary.json()["summary"]

    assert client.post(
        "/api/patient-access/RGN-7002/summary", headers=origin
    ).status_code == 401


def _voice_link(pid: str, headers: dict | None = None):
    return client.post(
        "/api/voice/patient-link",
        json={"patient_id": pid, "call_id": "CA123"},
        headers=headers or {},
    )


def test_voice_patient_link_returns_verifiable_magic_link(monkeypatch):
    monkeypatch.setenv("CLINICIAN_COOKIE_SECURE", "false")
    _create(_NEW_PID)

    response = _voice_link(_NEW_PID)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["patient_id"] == _NEW_PID
    parsed = urlsplit(body["patient_url"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "patient.example.test"
    assert parsed.path == f"/patient/{_NEW_PID}"
    token = parse_qs(parsed.query)["token"][0]
    assert body["patient_access_expires_at"].endswith("+00:00")

    verified = _verify(_NEW_PID, token)
    assert verified.status_code == 200
    assert "sana_patient_session=" in verified.headers["set-cookie"]


def test_voice_patient_link_requires_existing_patient():
    response = _voice_link("RGN-NOPE")

    assert response.status_code == 404
    assert response.json() == {"detail": "patient record not found"}


def test_voice_patient_link_requires_service_token(monkeypatch):
    _create(_NEW_PID)
    monkeypatch.setenv("SURVEY_INGEST_TOKEN", "voice-service-token")
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED_SURVEY_INGEST")

    assert _voice_link(_NEW_PID).status_code == 401
    assert _voice_link(_NEW_PID, {"X-Survey-Token": "wrong"}).status_code == 401
    assert (
        _voice_link(_NEW_PID, {"X-Survey-Token": "voice-service-token"}).status_code
        == 200
    )


def test_voice_patient_link_fails_closed_without_signing_secret(monkeypatch):
    _create(_NEW_PID)
    monkeypatch.delenv("PATIENT_LINK_SIGNING_SECRET")

    response = _voice_link(_NEW_PID)

    assert response.status_code == 503
    assert response.json() == {"detail": "patient access is not configured"}


@pytest.mark.parametrize("pid", ["", "bad id", "x" * 33, "../RGN-0417"])
def test_voice_patient_link_validates_patient_id(pid):
    assert _voice_link(pid).status_code == 422


def _sign_in_clinician(monkeypatch) -> None:
    monkeypatch.setenv("CLINICIAN_USERNAME", "test-clinician")
    monkeypatch.setenv("CLINICIAN_PASSWORD", "test-password")
    monkeypatch.setenv("CLINICIAN_SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("CLINICIAN_COOKIE_SECURE", "false")
    clinician_auth.reset_login_rate_limits()
    login = client.post(
        "/api/clinician/session",
        json={"username": "test-clinician", "password": "test-password"},
    )
    assert login.status_code == 200


def test_clinician_can_copy_the_same_magic_link(monkeypatch):
    _create(_NEW_PID)
    try:
        _sign_in_clinician(monkeypatch)
        response = client.post(f"/api/patients/{_NEW_PID}/link")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        parts = urlsplit(body["patient_url"])
        assert parts.path == f"/patient/{_NEW_PID}"
        patient_access.verify_token(parse_qs(parts.query)["token"][0], _NEW_PID)

        assert client.post("/api/patients/RGN-NOPE/link").status_code == 404
        client.cookies.clear()
        assert client.post(f"/api/patients/{_NEW_PID}/link").status_code == 401
    finally:
        client.cookies.clear()

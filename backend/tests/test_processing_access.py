import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clinician_auth  # noqa: E402
import gait_gen  # noqa: E402
import main  # noqa: E402
import patient_access  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CLINICIAN_USERNAME", "test-clinician")
    monkeypatch.setenv("CLINICIAN_PASSWORD", "test-password")
    monkeypatch.setenv("CLINICIAN_SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("CLINICIAN_COOKIE_SECURE", "false")
    clinician_auth.reset_login_rate_limits()
    with TestClient(main.app) as client:
        yield client


def token(pid="RGN-0417", expired=False):
    now = datetime.now(timezone.utc)
    if expired:
        now -= timedelta(minutes=5)
    return patient_access.generate_token(pid, now=now, ttl_seconds=60)[0]


def login(client):
    response = client.post("/api/clinician/session", json={
        "username": "test-clinician", "password": "test-password",
    })
    assert response.status_code == 200


@pytest.mark.parametrize("operation", ["process-frame", "process-video"])
@pytest.mark.parametrize("scope", ["legacy", "patient"])
@pytest.mark.parametrize("credential", ["missing", "malformed", "wrong-patient", "expired"])
def test_denied_before_reading_body(client, monkeypatch, operation, scope, credential):
    body = Mock(side_effect=AssertionError("unauthorized body read"))
    form = Mock(side_effect=AssertionError("unauthorized multipart parsing"))
    monkeypatch.setattr(Request, "body", body)
    monkeypatch.setattr(Request, "form", form)
    credentials = {
        "missing": {},
        "malformed": {"Authorization": "Bearer invalid"},
        "wrong-patient": {"Authorization": f"Bearer {token('RGN-9999')}"},
        "expired": {"Authorization": f"Bearer {token(expired=True)}"},
    }
    prefix = "/api" if scope == "legacy" else "/api/patient-access/RGN-0417"
    response = client.post(f"{prefix}/{operation}", headers=credentials[credential])
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    body.assert_not_called()
    form.assert_not_called()


@pytest.mark.parametrize("operation", ["process-frame", "process-video"])
def test_patient_token_cannot_use_clinician_processing(client, operation):
    response = client.post(f"/api/{operation}", headers={
        "Authorization": f"Bearer {token()}",
    })
    assert response.status_code == 401


@pytest.mark.parametrize("scope", ["legacy", "patient"])
def test_authorized_frame_processing(client, scope):
    prefix = "/api"
    headers = {}
    if scope == "legacy":
        login(client)
    else:
        prefix += "/patient-access/RGN-0417"
        headers["Authorization"] = f"Bearer {token()}"
    response = client.post(
        f"{prefix}/process-frame", headers=headers,
        json=gait_gen.recovered_session(),
    )
    assert response.status_code == 200
    assert response.json()["gait_detected"] is True
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("operation", ["process-frame", "process-video"])
@pytest.mark.parametrize("scope", ["legacy", "patient"])
def test_processing_validation_errors_are_not_cached(client, operation, scope):
    prefix = "/api"
    headers = {}
    if scope == "legacy":
        login(client)
    else:
        prefix += "/patient-access/RGN-0417"
        headers["Authorization"] = f"Bearer {token()}"
    response = client.post(f"{prefix}/{operation}", headers=headers, json={})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert response.headers.get("cache-control") == "no-store"


@pytest.mark.parametrize("scope", ["legacy", "patient"])
def test_authorized_video_processing(client, monkeypatch, scope):
    frames = gait_gen.recovered_session()["frames"]
    extract = Mock(return_value=(frames, 30, len(frames)))
    monkeypatch.setattr(main.video, "extract_frames", extract)
    prefix = "/api"
    headers = {}
    if scope == "legacy":
        login(client)
    else:
        prefix += "/patient-access/RGN-0417"
        headers["Authorization"] = f"Bearer {token()}"
    response = client.post(
        f"{prefix}/process-video", headers=headers,
        files={"file": ("synthetic.mp4", b"synthetic-fixture", "video/mp4")},
    )
    assert response.status_code == 200
    assert response.json()["metrics"]["gait_detected"] is True
    assert response.headers["cache-control"] == "no-store"
    extract.assert_called_once()


@pytest.mark.parametrize("operation", ["process-frame", "process-video"])
def test_patient_cookie_rejects_wrong_scope_and_cross_origin(client, operation):
    client.cookies.set(main.PATIENT_SESSION_COOKIE, token())
    assert client.post(
        f"/api/patient-access/RGN-9999/{operation}",
    ).status_code == 401
    assert client.post(
        f"/api/patient-access/RGN-0417/{operation}",
        headers={"Origin": "https://untrusted.example"},
    ).status_code == 403
    assert client.post(
        f"/api/patient-access/RGN-0417/{operation}",
        headers={"Origin": "http://localhost:3000"},
    ).status_code == 422


@pytest.mark.parametrize("operation", ["process-frame", "process-video"])
def test_clinician_cannot_use_patient_processing_or_cross_origin(client, operation):
    login(client)
    assert client.post(
        f"/api/patient-access/RGN-0417/{operation}",
    ).status_code == 401
    assert client.post(
        f"/api/{operation}", headers={"Origin": "https://untrusted.example"},
    ).status_code == 403


def test_processing_fails_closed_without_configuration(client, monkeypatch):
    monkeypatch.delenv("CLINICIAN_SESSION_SECRET")
    assert client.post("/api/process-frame").status_code == 503
    signed = token()
    monkeypatch.delenv("PATIENT_LINK_SIGNING_SECRET")
    assert client.post(
        "/api/patient-access/RGN-0417/process-frame",
        headers={"Authorization": f"Bearer {signed}"},
    ).status_code == 503


def test_processing_preflight_still_works(client):
    response = client.options(
        "/api/patient-access/RGN-0417/process-video",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_video_failure_logs_no_filename_or_exception_details(client, monkeypatch, caplog):
    monkeypatch.setattr(
        main.video, "extract_frames",
        Mock(side_effect=RuntimeError("private patient context")),
    )
    response = client.post(
        "/api/patient-access/RGN-0417/process-video",
        headers={"Authorization": f"Bearer {token()}"},
        files={"file": ("patient-name.mp4", b"fixture", "video/mp4")},
    )
    assert response.status_code == 422
    assert "RuntimeError" in caplog.text
    assert "private patient context" not in caplog.text
    assert "patient-name" not in caplog.text

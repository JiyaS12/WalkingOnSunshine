import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clinician_auth
from main import _cors_origins, app


client = TestClient(app)


@pytest.fixture(autouse=True)
def auth_environment(monkeypatch):
    monkeypatch.setenv("CLINICIAN_USERNAME", "dr-demo")
    monkeypatch.setenv("CLINICIAN_PASSWORD", "correct horse battery staple")
    monkeypatch.setenv("CLINICIAN_SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("CLINICIAN_LOGIN_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("CLINICIAN_LOGIN_WINDOW_SECONDS", "60")
    monkeypatch.setenv("CLINICIAN_SESSION_TTL_SECONDS", "3600")
    monkeypatch.setenv("CLINICIAN_COOKIE_SECURE", "false")
    clinician_auth.reset_login_rate_limits()
    client.cookies.clear()
    yield
    client.cookies.clear()


def _sign_in(password="correct horse battery staple"):
    return client.post(
        "/api/clinician/session",
        json={"username": "dr-demo", "password": password},
    )


def test_protected_data_denies_unauthenticated_and_patient_credentials():
    paths = [
        ("get", "/api/patients"),
        ("get", "/api/patients/RGN-0417"),
        ("get", "/api/summary-cache-stats"),
        ("post", "/api/generate-summary"),
        ("post", "/api/patients/RGN-0417/synthesis"),
        ("post", "/api/patients/RGN-0417/sessions"),
        ("post", "/api/patients/RGN-0417/ensure-demo"),
    ]
    patient_headers = {
        "Authorization": "Bearer signed-patient-link-token",
        "X-Patient-Token": "signed-patient-link-token",
    }
    for method, path in paths:
        response = client.request(method, path, headers=patient_headers)
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "session_required"


def test_authentication_happens_before_patient_lookup():
    existing = client.get("/api/patients/RGN-0417")
    missing = client.get("/api/patients/DOES-NOT-EXIST")
    assert existing.status_code == missing.status_code == 401
    assert existing.json() == missing.json()


def test_sign_in_allows_clinician_data_and_sets_http_only_cookie():
    response = _sign_in()
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert client.get("/api/clinician/session").status_code == 200
    assert client.get("/api/patients").status_code == 200


def test_invalid_login_is_generic_and_does_not_set_cookie():
    response = _sign_in("wrong")
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid clinician credentials"
    assert "set-cookie" not in response.headers


def test_non_ascii_credentials_are_compared_without_server_error(monkeypatch):
    invalid = client.post(
        "/api/clinician/session",
        json={"username": "dr-démo", "password": "incorrect-☃"},
    )
    assert invalid.status_code == 401

    monkeypatch.setenv("CLINICIAN_USERNAME", "dr-démo")
    monkeypatch.setenv("CLINICIAN_PASSWORD", "correct-☃")
    valid = client.post(
        "/api/clinician/session",
        json={"username": "dr-démo", "password": "correct-☃"},
    )
    assert valid.status_code == 200


def test_untrusted_browser_origin_cannot_login_or_mutate():
    credentials = {
        "username": "dr-demo",
        "password": "correct horse battery staple",
    }
    denied_login = client.post(
        "/api/clinician/session",
        json=credentials,
        headers={"Origin": "https://untrusted.example"},
    )
    assert denied_login.status_code == 403
    assert "set-cookie" not in denied_login.headers

    assert _sign_in().status_code == 200
    denied_mutation = client.post(
        "/api/patients/RGN-0417/synthesis",
        headers={"Origin": "https://untrusted.example"},
    )
    assert denied_mutation.status_code == 403

    denied_logout = client.delete(
        "/api/clinician/session",
        headers={"Origin": "https://untrusted.example"},
    )
    assert denied_logout.status_code == 403
    assert client.get("/api/clinician/session").status_code == 200


def test_expired_session_is_rejected_with_explicit_reason():
    config = clinician_auth.get_auth_config()
    token, _ = clinician_auth.create_session(
        config, now=int(time.time()) - config.session_ttl_seconds - 1
    )
    client.cookies.set(clinician_auth.SESSION_COOKIE, token)
    response = client.get("/api/clinician/session")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_expired"


def test_unicode_session_cookie_is_rejected_as_invalid():
    with pytest.raises(clinician_auth.SessionError) as exc_info:
        clinician_auth.verify_session("☃.signature", clinician_auth.get_auth_config())
    assert exc_info.value.code == "session_invalid"


def test_cookie_secure_defaults_on(monkeypatch):
    monkeypatch.delenv("CLINICIAN_COOKIE_SECURE", raising=False)
    assert clinician_auth.get_auth_config().cookie_secure is True


def test_logout_clears_session():
    assert _sign_in().status_code == 200
    response = client.delete("/api/clinician/session")
    assert response.status_code == 204
    assert client.get("/api/clinician/session").status_code == 401


def test_missing_or_partial_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("CLINICIAN_SESSION_SECRET")
    response = _sign_in()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "auth_unavailable"
    assert client.get("/api/patients").status_code == 503


def test_login_is_size_limited_and_rate_limited(monkeypatch):
    oversized = client.post(
        "/api/clinician/session",
        content=b"x" * 4097,
        headers={"content-type": "application/json"},
    )
    assert oversized.status_code == 413

    monkeypatch.setenv("CLINICIAN_LOGIN_MAX_ATTEMPTS", "2")
    clinician_auth.reset_login_rate_limits()
    assert _sign_in("wrong-1").status_code == 401
    assert _sign_in("wrong-2").status_code == 401
    limited = _sign_in()
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


def test_cors_allows_configured_local_frontend_only():
    allowed = client.options(
        "/api/patients",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert (
        "authorization"
        in allowed.headers["access-control-allow-headers"].lower()
    )

    denied = client.options(
        "/api/patients",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in denied.headers


def test_wildcard_credentialed_cors_is_rejected():
    with pytest.raises(RuntimeError, match="cannot contain"):
        _cors_origins("https://app.example,*")

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.gait_handoff import (
    LINK_PATH,
    TOKEN_HEADER,
    BackendLinkClient,
    GaitHandoffService,
    GaitLinkUnavailable,
    configured_backend_origin,
    validate_patient_link,
)

TOKEN = "voice-service-token-1234"
ENV = {"GAIT_BACKEND_URL": "https://api.example.org/", "GAIT_BACKEND_TOKEN": TOKEN}
LINK = "https://walk.example.org/patient/RGN-0417?token=abc.def"
BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"


def _client(handler, env=ENV) -> BackendLinkClient:
    return BackendLinkClient(env=env, transport=httpx.MockTransport(handler))


def _fetch(client: BackendLinkClient, patient_code: str = "RGN-0417", call_id: str | None = "CA1") -> str:
    return asyncio.run(client.fetch(patient_code, call_id))


def test_client_asks_the_backend_with_the_service_token_and_returns_its_link():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"patient_id": "RGN-0417", "patient_url": LINK})

    assert _fetch(_client(handler)) == LINK
    (request,) = seen
    assert str(request.url) == f"https://api.example.org{LINK_PATH}"
    assert request.headers[TOKEN_HEADER] == TOKEN
    assert json.loads(request.content) == {"patient_id": "RGN-0417", "call_id": "CA1"}


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (404, {"detail": "patient record not found"}, "no record"),
        (401, {"detail": "invalid survey token"}, "GAIT_BACKEND_TOKEN"),
        (503, {"detail": "patient access is not configured"}, "HTTP 503"),
        (500, "boom", "HTTP 500"),
    ],
)
def test_backend_refusals_leave_the_handoff_without_a_link(status, body, reason):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)

    with pytest.raises(GaitLinkUnavailable, match=reason):
        _fetch(_client(handler))


def test_unreachable_backend_is_reported_not_raised_raw():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(GaitLinkUnavailable, match="unreachable"):
        _fetch(_client(handler))


@pytest.mark.parametrize(
    "payload",
    [
        {"patient_url": "https://walk.example.org/patient/RGN-0500?token=abc"},
        {"patient_url": "http://walk.example.org/patient/RGN-0417?token=abc"},
        {"patient_url": "https://walk.example.org/patient/RGN-0417"},
        {"patient_url": "https://user:pw@walk.example.org/patient/RGN-0417?token=abc"},
        {"patient_url": "javascript:alert(1)"},
        {"patient_url": 42},
        {},
        [],
    ],
)
def test_only_a_link_to_this_patients_page_is_accepted(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(GaitLinkUnavailable, match="unexpected"):
        _fetch(_client(handler))


def test_non_json_success_body_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>")

    with pytest.raises(GaitLinkUnavailable, match="non-JSON"):
        _fetch(_client(handler))


def test_local_http_links_are_allowed_for_development():
    assert validate_patient_link("http://localhost:3000/patient/RGN-0417?token=x", "RGN-0417")


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"GAIT_BACKEND_URL": "https://api.example.org"},
        {"GAIT_BACKEND_TOKEN": TOKEN},
        {**ENV, "GAIT_BACKEND_TOKEN": "short"},
        {**ENV, "GAIT_BACKEND_URL": "http://api.example.org"},
        {**ENV, "GAIT_BACKEND_URL": "https://api.example.org/api"},
        {**ENV, "GAIT_BACKEND_URL": "https://user:pw@api.example.org"},
    ],
)
def test_unsafe_configuration_never_contacts_the_backend(env):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("backend must not be called")

    with pytest.raises(GaitLinkUnavailable):
        _fetch(_client(handler, env))


@pytest.mark.parametrize(
    ("base_url", "origin"),
    [
        ("https://api.example.org", "https://api.example.org"),
        ("  https://api.example.org/  ", "https://api.example.org"),
        ("https://Api.Example.org:8443", "https://api.example.org:8443"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_valid_backend_origins_are_normalised(base_url, origin):
    assert configured_backend_origin({"GAIT_BACKEND_URL": base_url}) == origin


def test_unconfigured_handoff_is_marked_unavailable_without_a_link(monkeypatch):
    monkeypatch.delenv("GAIT_BACKEND_URL", raising=False)
    monkeypatch.delenv("GAIT_BACKEND_TOKEN", raising=False)
    handoff = asyncio.run(GaitHandoffService().prepare("RGN-0417", "orthopedic", "CA1"))
    assert handoff.link is None
    assert handoff.status == "unavailable"
    assert handoff.sms_sent is False
    assert "GAIT_BACKEND_URL" in handoff.notes[-1]


def test_prepared_handoff_keeps_the_link_out_of_its_notes():
    async def fetch(patient_code: str, call_id: str | None) -> str:
        return LINK

    handoff = asyncio.run(GaitHandoffService(link_fetcher=fetch).prepare("RGN-0417", "orthopedic", "CA1"))
    assert handoff.link == LINK
    assert handoff.status == "prepared"
    assert all("token" not in note for note in handoff.notes)


@pytest.mark.skipif(not (BACKEND_DIR / "main.py").exists(), reason="backend checkout not present")
def test_round_trip_with_the_real_backend_app(monkeypatch, tmp_path):
    """The voice client's request is accepted by the backend and the link it returns verifies there."""

    pytest.importorskip("numpy", reason="backend requirements not installed")
    monkeypatch.setenv("PATIENT_LINK_SIGNING_SECRET", "test-only-patient-link-secret-32-bytes-minimum")
    monkeypatch.setenv("PATIENT_APP_BASE_URL", "https://walk.example.org")
    monkeypatch.setenv("SURVEY_INGEST_TOKEN", TOKEN)
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED_SURVEY_INGEST", raising=False)
    monkeypatch.syspath_prepend(str(BACKEND_DIR))
    for name in ("main", "store", "patient_access", "agent", "processor", "video", "clinician_auth"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("main", BACKEND_DIR / "main.py")
    assert spec is not None and spec.loader is not None
    backend_main = importlib.util.module_from_spec(spec)
    sys.modules["main"] = backend_main
    spec.loader.exec_module(backend_main)
    store = sys.modules["store"]
    patient_access = sys.modules["patient_access"]
    store.reset_for_tests(tmp_path / "patients.json")
    try:
        store.upsert_survey(
            {
                "patient_id": "RGN-0417",
                "patient_name": "Demo",
                "pain_scale": 3,
                "fall_history": {"falls_last_6_months": 0, "injured": False},
                "dizziness": False,
                "primary_complaints": ["demo"],
            }
        )
        client = BackendLinkClient(env=ENV, transport=httpx.ASGITransport(app=backend_main.app))
        link = _fetch(client)
        parsed = urlsplit(link)
        assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "walk.example.org", "/patient/RGN-0417")
        token = parse_qs(parsed.query)["token"][0]
        patient_access.verify_token(token, "RGN-0417")
        with pytest.raises(patient_access.PatientAccessError):
            patient_access.verify_token(token, "RGN-0500")

        with pytest.raises(GaitLinkUnavailable, match="no record"):
            _fetch(client, patient_code="RGN-9999")
        with pytest.raises(GaitLinkUnavailable, match="GAIT_BACKEND_TOKEN"):
            _fetch(BackendLinkClient(env={**ENV, "GAIT_BACKEND_TOKEN": "wrong-token-1234567"}, transport=httpx.ASGITransport(app=backend_main.app)))
    finally:
        store.reset_for_tests(store.CACHE_PATH)
        for name in ("main", "store", "patient_access", "agent", "processor", "video", "clinician_auth"):
            sys.modules.pop(name, None)

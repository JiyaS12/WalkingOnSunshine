from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.gait_handoff import GaitHandoffService, GaitLinkUnavailable, signed_patient_link

SECRET = "x" * 32
ENV = {
    "GAIT_CHECKER_BASE_URL": "https://walk.example.org/",
    "PATIENT_LINK_SIGNING_SECRET": SECRET,
}
BACKEND_ACCESS = Path(__file__).resolve().parents[2] / "backend" / "patient_access.py"


def test_link_is_patient_scoped_and_signed():
    link = signed_patient_link("RGN-0417", "orthopedic", ENV)
    parsed = urlsplit(link)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "walk.example.org", "/patient/RGN-0417")
    token = parse_qs(parsed.query)["token"][0]
    assert token.count(".") == 1


@pytest.mark.skipif(not BACKEND_ACCESS.exists(), reason="backend checkout not present")
def test_link_token_verifies_with_the_backend(monkeypatch):
    spec = importlib.util.spec_from_file_location("backend_patient_access", BACKEND_ACCESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    token = parse_qs(urlsplit(signed_patient_link("RGN-0417", "orthopedic", ENV)).query)["token"][0]
    module.verify_token(token, "RGN-0417", secret=SECRET.encode())
    with pytest.raises(module.PatientAccessError):
        module.verify_token(token, "RGN-0500", secret=SECRET.encode())


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"GAIT_CHECKER_BASE_URL": "https://walk.example.org"},
        {"GAIT_CHECKER_BASE_URL": "http://walk.example.org", "PATIENT_LINK_SIGNING_SECRET": SECRET},
        {"GAIT_CHECKER_BASE_URL": "https://walk.example.org", "PATIENT_LINK_SIGNING_SECRET": "short"},
        {**ENV, "PATIENT_LINK_TTL_SECONDS": "5"},
    ],
)
def test_unsafe_configuration_never_yields_a_link(env):
    with pytest.raises(GaitLinkUnavailable):
        signed_patient_link("RGN-0417", "orthopedic", env)


def test_unconfigured_handoff_is_marked_unavailable_without_a_link(monkeypatch):
    monkeypatch.delenv("GAIT_CHECKER_BASE_URL", raising=False)
    monkeypatch.delenv("PATIENT_LINK_SIGNING_SECRET", raising=False)
    handoff = GaitHandoffService().prepare("RGN-0417", "orthopedic")
    assert handoff.link is None
    assert handoff.status == "unavailable"
    assert handoff.sms_sent is False

"""Prepare the walking-check handoff the call texts to the patient.

The WalkingOnSunshine backend owns the magic link: it signs the token with
``PATIENT_LINK_SIGNING_SECRET`` and builds ``/patient/<code>?token=…`` from
``PATIENT_APP_BASE_URL``. This module asks it for that URL over
``POST /api/voice/patient-link``, authenticated with the backend's
``SURVEY_INGEST_TOKEN``, so the voice process never holds the signing secret and
cannot drift from the backend's token format.  The returned URL is a credential:
it goes into the SMS and nowhere else (no logs, no transcript, no notes).

Sending the SMS is owned by ``PhoneCallSession`` in ``telephony/call_session.py``
so this module stays testable without a network.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

import httpx

logger = logging.getLogger(__name__)

BACKEND_URL_ENV = "GAIT_BACKEND_URL"
BACKEND_TOKEN_ENV = "GAIT_BACKEND_TOKEN"
LINK_PATH = "/api/voice/patient-link"
TOKEN_HEADER = "X-Survey-Token"
DEFAULT_TIMEOUT_SECONDS = 5.0
MIN_TOKEN_CHARS = 16
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

LinkFetcher = Callable[[str, str | None], Awaitable[str]]


class GaitLinkUnavailable(RuntimeError):
    """The signed patient link cannot be obtained with the current configuration."""


def _bare_origin(raw: str, env_name: str) -> str:
    raw = raw.strip().rstrip("/")
    if not raw:
        raise GaitLinkUnavailable(f"{env_name} is not set")
    problem = f"{env_name} must be a bare HTTPS origin such as https://example.org (HTTP only locally)"
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise GaitLinkUnavailable(problem) from exc
    hostname = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in raw)
        or (parsed.scheme != "https" and hostname not in LOCAL_HOSTS)
    ):
        raise GaitLinkUnavailable(problem)
    origin = f"{parsed.scheme}://{hostname}"
    if ":" in hostname:
        origin = f"{parsed.scheme}://[{hostname}]"
    return origin if port is None else f"{origin}:{port}"


def configured_backend_origin(env: Mapping[str, str]) -> str:
    return _bare_origin(env.get(BACKEND_URL_ENV) or "", BACKEND_URL_ENV)


def configured_backend_token(env: Mapping[str, str]) -> str:
    token = (env.get(BACKEND_TOKEN_ENV) or "").strip()
    if len(token) < MIN_TOKEN_CHARS:
        raise GaitLinkUnavailable(f"{BACKEND_TOKEN_ENV} must contain at least {MIN_TOKEN_CHARS} characters")
    return token


def validate_patient_link(link: object, patient_code: str) -> str:
    """Accept only an HTTPS (or local HTTP) link to this patient's page."""

    problem = "backend returned an unexpected patient link"
    if not isinstance(link, str) or any(char.isspace() for char in link):
        raise GaitLinkUnavailable(problem)
    try:
        parsed = urlsplit(link)
    except ValueError as exc:
        raise GaitLinkUnavailable(problem) from exc
    hostname = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.scheme != "https" and hostname not in LOCAL_HOSTS)
        or parsed.path != f"/patient/{quote(patient_code, safe='')}"
        or "token=" not in parsed.query
    ):
        raise GaitLinkUnavailable(problem)
    return link


class BackendLinkClient:
    """Fetch the patient's signed link from the WalkingOnSunshine backend."""

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.env = env
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def fetch(self, patient_code: str, call_id: str | None = None) -> str:
        env = self.env if self.env is not None else os.environ
        origin = configured_backend_origin(env)
        token = configured_backend_token(env)
        body: dict[str, str] = {"patient_id": patient_code}
        if call_id:
            body["call_id"] = call_id
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = await client.post(
                    f"{origin}{LINK_PATH}", json=body, headers={TOKEN_HEADER: token}
                )
        except httpx.HTTPError as exc:
            raise GaitLinkUnavailable(f"backend unreachable: {type(exc).__name__}") from exc
        if response.status_code == 404:
            raise GaitLinkUnavailable("backend has no record for this patient")
        if response.status_code == 401:
            raise GaitLinkUnavailable(f"backend rejected {BACKEND_TOKEN_ENV}")
        if response.status_code != 200:
            raise GaitLinkUnavailable(f"backend answered HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GaitLinkUnavailable("backend returned a non-JSON body") from exc
        if not isinstance(payload, dict):
            raise GaitLinkUnavailable("backend returned an unexpected body")
        return validate_patient_link(payload.get("patient_url"), patient_code)


@dataclass
class GaitHandoff:
    patient_code: str
    condition_category: str
    status: str = "prepared"
    link: str | None = None
    sms_sent: bool = False
    notes: list[str] = field(default_factory=list)


class GaitHandoffService:
    """Prepare the handoff payload with a backend-issued link when configured.

    Without ``GAIT_BACKEND_URL`` and ``GAIT_BACKEND_TOKEN``, or when the backend
    declines, the handoff is marked ``unavailable`` with no link, so nothing
    unsigned or placeholder is ever texted to a real patient.
    """

    def __init__(self, link_fetcher: LinkFetcher | None = None):
        self.link_fetcher: LinkFetcher = link_fetcher or BackendLinkClient().fetch

    async def prepare(
        self, patient_code: str, condition_category: str, call_id: str | None = None
    ) -> GaitHandoff:
        handoff = GaitHandoff(patient_code=patient_code, condition_category=condition_category)
        try:
            handoff.link = await self.link_fetcher(patient_code, call_id)
        except GaitLinkUnavailable as exc:
            handoff.status = "unavailable"
            handoff.notes.append(f"Gait link not sent: {exc}")
            logger.warning("Gait-checker link unavailable for %s: %s", patient_code, exc)
            return handoff
        handoff.notes.append("Handoff prepared with a backend-issued patient link.")
        return handoff

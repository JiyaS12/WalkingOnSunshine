"""Prepare the walking-check handoff the call texts to the patient.

The link points at the WalkingOnSunshine patient page and carries the same
signed, expiring token that ``backend/patient_access.py`` issues: a canonical
JSON payload ``{"exp", "sub", "v"}`` and an HMAC-SHA256 signature, both
base64url encoded, joined by a dot.  The two processes share
``PATIENT_LINK_SIGNING_SECRET`` so a token minted here verifies there.  The
token is a credential: it goes into the SMS and nowhere else (no logs, no
transcript).

Sending the SMS is owned by ``PhoneCallSession`` in ``telephony/call_session.py``
so this module stays testable without a network.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit

logger = logging.getLogger(__name__)

BASE_URL_ENV = "GAIT_CHECKER_BASE_URL"
SIGNING_SECRET_ENV = "PATIENT_LINK_SIGNING_SECRET"
TTL_ENV = "PATIENT_LINK_TTL_SECONDS"
TOKEN_VERSION = 1
DEFAULT_TTL_SECONDS = 15 * 60
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 7 * 24 * 60 * 60
MIN_SECRET_BYTES = 32
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class GaitLinkUnavailable(RuntimeError):
    """The signed patient link cannot be produced with the current configuration."""


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _configured_base_url(env: Mapping[str, str]) -> str:
    raw = (env.get(BASE_URL_ENV) or "").strip().rstrip("/")
    if not raw:
        raise GaitLinkUnavailable(f"{BASE_URL_ENV} is not set")
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and parsed.hostname not in LOCAL_HOSTS)
    ):
        raise GaitLinkUnavailable(f"{BASE_URL_ENV} must be an HTTPS origin (HTTP only locally)")
    return raw


def _configured_secret(env: Mapping[str, str]) -> bytes:
    raw = env.get(SIGNING_SECRET_ENV) or ""
    if len(raw.encode("utf-8")) < MIN_SECRET_BYTES:
        raise GaitLinkUnavailable(f"{SIGNING_SECRET_ENV} must contain at least {MIN_SECRET_BYTES} bytes")
    return raw.encode("utf-8")


def _configured_ttl(env: Mapping[str, str]) -> int:
    raw = env.get(TTL_ENV, str(DEFAULT_TTL_SECONDS))
    try:
        ttl = int(raw)
    except ValueError as exc:
        raise GaitLinkUnavailable(f"{TTL_ENV} must be an integer") from exc
    if not MIN_TTL_SECONDS <= ttl <= MAX_TTL_SECONDS:
        raise GaitLinkUnavailable(f"{TTL_ENV} must be between {MIN_TTL_SECONDS} and {MAX_TTL_SECONDS}")
    return ttl


def sign_patient_token(patient_code: str, secret: bytes, ttl_seconds: int, now: datetime | None = None) -> str:
    issued_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires_at = math.ceil(issued_at.timestamp() + ttl_seconds)
    payload = json.dumps(
        {"exp": expires_at, "sub": patient_code, "v": TOKEN_VERSION},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = _encode(payload)
    signature = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_encode(signature)}"


def signed_patient_link(
    patient_code: str, condition_category: str, env: Mapping[str, str] | None = None
) -> str:
    """Build ``<base>/patient/<code>?token=<signed>``; raises when unconfigured."""

    del condition_category  # not part of the URL contract
    source = env if env is not None else os.environ
    base = _configured_base_url(source)
    token = sign_patient_token(patient_code, _configured_secret(source), _configured_ttl(source))
    return f"{base}/patient/{quote(patient_code, safe='')}?{urlencode({'token': token})}"


@dataclass
class GaitHandoff:
    patient_code: str
    condition_category: str
    status: str = "prepared"
    link: str | None = None
    sms_sent: bool = False
    notes: list[str] = field(default_factory=list)


class GaitHandoffService:
    """Prepare the handoff payload with a signed link when configured.

    Without ``GAIT_CHECKER_BASE_URL`` and a shared ``PATIENT_LINK_SIGNING_SECRET``
    the handoff is marked ``unavailable`` with no link, so nothing unsigned or
    placeholder is ever texted to a real patient.
    """

    def __init__(self, link_generator=None):
        self.link_generator = link_generator or signed_patient_link

    def prepare(self, patient_code: str, condition_category: str) -> GaitHandoff:
        handoff = GaitHandoff(patient_code=patient_code, condition_category=condition_category)
        try:
            handoff.link = self.link_generator(patient_code, condition_category)
        except GaitLinkUnavailable as exc:
            handoff.status = "unavailable"
            handoff.notes.append(f"Gait link not sent: {exc}")
            logger.warning("Gait-checker link unavailable for %s: %s", patient_code, exc)
            return handoff
        handoff.notes.append("Handoff prepared for gait-checker integration.")
        return handoff

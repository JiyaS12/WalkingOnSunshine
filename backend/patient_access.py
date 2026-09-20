"""Signed, expiring credentials for the patient-facing API.

The token format is intentionally small and dependency-free: a canonical JSON
payload and an HMAC-SHA256 signature, both base64url encoded.  Tokens are
credentials, so callers must keep them out of logs and persistent records.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit, urlunsplit


_TOKEN_VERSION = 1
_DEFAULT_TTL_SECONDS = 15 * 60
_MIN_TTL_SECONDS = 60
_MAX_TTL_SECONDS = 7 * 24 * 60 * 60
_MIN_SECRET_BYTES = 32


class PatientAccessError(ValueError):
    """A patient token is missing, malformed, invalid, expired, or mismatched."""


class PatientAccessConfigurationError(RuntimeError):
    """Patient-link signing configuration is absent or unsafe."""


@dataclass(frozen=True)
class PatientLink:
    url: str
    expires_at: datetime


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not value:
        raise PatientAccessError("invalid patient access token")
    try:
        raw = value.encode("ascii")
        padded = raw + (b"=" * (-len(raw) % 4))
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise PatientAccessError("invalid patient access token") from exc


def _configured_secret() -> bytes:
    raw = os.getenv("PATIENT_LINK_SIGNING_SECRET")
    if raw is None or len(raw.encode("utf-8")) < _MIN_SECRET_BYTES:
        raise PatientAccessConfigurationError(
            "PATIENT_LINK_SIGNING_SECRET must contain at least 32 bytes"
        )
    return raw.encode("utf-8")


def _configured_ttl_seconds() -> int:
    raw = os.getenv("PATIENT_LINK_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS))
    try:
        ttl = int(raw)
    except ValueError as exc:
        raise PatientAccessConfigurationError(
            "PATIENT_LINK_TTL_SECONDS must be an integer"
        ) from exc
    if not _MIN_TTL_SECONDS <= ttl <= _MAX_TTL_SECONDS:
        raise PatientAccessConfigurationError(
            "PATIENT_LINK_TTL_SECONDS must be between 60 and 604800"
        )
    return ttl


def _configured_base_url() -> str:
    raw = os.getenv("PATIENT_APP_BASE_URL", "http://localhost:3000").rstrip("/")
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise PatientAccessConfigurationError(
            "PATIENT_APP_BASE_URL is not a valid URL"
        ) from exc
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in raw)
        or (parsed.scheme != "https" and hostname not in local_hosts)
    ):
        raise PatientAccessConfigurationError(
            "PATIENT_APP_BASE_URL must be an HTTPS origin (HTTP is allowed locally)"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def generate_token(
    patient_id: str,
    *,
    now: datetime | None = None,
    ttl_seconds: int | None = None,
    secret: bytes | None = None,
) -> tuple[str, datetime]:
    """Return a signed patient token and its expiration timestamp.

    Explicit clock, lifetime, and secret inputs keep unit tests deterministic;
    application callers use the validated environment configuration.
    """

    issued_at = now or datetime.now(timezone.utc)
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)
    issued_at = issued_at.astimezone(timezone.utc)
    lifetime = ttl_seconds if ttl_seconds is not None else _configured_ttl_seconds()
    signing_secret = secret if secret is not None else _configured_secret()
    if lifetime <= 0 or not signing_secret:
        raise PatientAccessConfigurationError("patient token configuration is invalid")

    # Tokens encode whole Unix seconds. Round the target expiration upward so
    # callers always receive at least the configured lifetime, even when the
    # issuance clock includes microseconds.
    expires_at = datetime.fromtimestamp(
        math.ceil(issued_at.timestamp() + lifetime), tz=timezone.utc
    )
    payload = json.dumps(
        {"exp": int(expires_at.timestamp()), "sub": patient_id, "v": _TOKEN_VERSION},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded_payload = _encode(payload)
    signature = hmac.new(
        signing_secret, encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded_payload}.{_encode(signature)}", expires_at


def verify_token(
    token: str,
    patient_id: str,
    *,
    now: datetime | None = None,
    secret: bytes | None = None,
) -> None:
    """Validate signature, payload shape, scope, and expiration.

    Every credential failure raises the same public exception type/message so
    API responses do not reveal which part of a credential was accepted.
    """

    try:
        encoded_payload, encoded_signature = token.split(".")
        supplied_signature = _decode(encoded_signature)
        signing_secret = secret if secret is not None else _configured_secret()
        expected_signature = hmac.new(
            signing_secret, encoded_payload.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise PatientAccessError("invalid patient access token")

        payload = json.loads(_decode(encoded_payload))
        version = payload.get("v")
        subject = payload.get("sub")
        expires = payload.get("exp")
        if (
            not isinstance(payload, dict)
            or version != _TOKEN_VERSION
            or not isinstance(subject, str)
            or not isinstance(expires, int)
            or isinstance(expires, bool)
        ):
            raise PatientAccessError("invalid patient access token")

        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        if not hmac.compare_digest(subject, patient_id):
            raise PatientAccessError("invalid patient access token")
        if int(checked_at.astimezone(timezone.utc).timestamp()) >= expires:
            raise PatientAccessError("invalid patient access token")
    except PatientAccessConfigurationError:
        raise
    except PatientAccessError:
        raise
    except (
        AttributeError,
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise PatientAccessError("invalid patient access token") from exc


def create_patient_link(patient_id: str) -> PatientLink:
    token, expires_at = generate_token(patient_id)
    base_url = _configured_base_url()
    path = f"{base_url}/patient/{quote(patient_id, safe='')}"
    url = f"{path}?{urlencode({'token': token})}"
    return PatientLink(url=url, expires_at=expires_at)

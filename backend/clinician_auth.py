"""Signed clinician sessions and login throttling.

The browser receives only an HttpOnly session cookie.  Clinician credentials
and the signing key are read from the backend environment on every request so
misconfiguration fails closed and tests can isolate configuration safely.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


SESSION_COOKIE = "gaitguard_clinician_session"
DEFAULT_SESSION_TTL_SECONDS = 8 * 60 * 60
DEFAULT_LOGIN_ATTEMPTS = 5
DEFAULT_LOGIN_WINDOW_SECONDS = 60


class AuthConfigurationError(RuntimeError):
    """Raised when clinician protection cannot be configured safely."""


class SessionError(ValueError):
    """A signed session was missing, invalid, or expired."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AuthConfig:
    username: str
    password: str
    session_secret: str
    session_ttl_seconds: int
    cookie_secure: bool
    login_attempts: int
    login_window_seconds: int


@dataclass(frozen=True)
class ClinicianSession:
    username: str
    expires_at: int


def _int_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AuthConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise AuthConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _bool_setting(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise AuthConfigurationError(f"{name} must be true or false")


def get_auth_config() -> AuthConfig:
    username = os.getenv("CLINICIAN_USERNAME", "")
    password = os.getenv("CLINICIAN_PASSWORD", "")
    session_secret = os.getenv("CLINICIAN_SESSION_SECRET", "")

    if not username or not password or not session_secret:
        raise AuthConfigurationError(
            "CLINICIAN_USERNAME, CLINICIAN_PASSWORD, and "
            "CLINICIAN_SESSION_SECRET are all required"
        )
    if len(session_secret) < 32:
        raise AuthConfigurationError(
            "CLINICIAN_SESSION_SECRET must contain at least 32 characters"
        )

    return AuthConfig(
        username=username,
        password=password,
        session_secret=session_secret,
        session_ttl_seconds=_int_setting(
            "CLINICIAN_SESSION_TTL_SECONDS",
            DEFAULT_SESSION_TTL_SECONDS,
            60,
            24 * 60 * 60,
        ),
        cookie_secure=_bool_setting("CLINICIAN_COOKIE_SECURE", True),
        login_attempts=_int_setting(
            "CLINICIAN_LOGIN_MAX_ATTEMPTS", DEFAULT_LOGIN_ATTEMPTS, 1, 100
        ),
        login_window_seconds=_int_setting(
            "CLINICIAN_LOGIN_WINDOW_SECONDS",
            DEFAULT_LOGIN_WINDOW_SECONDS,
            1,
            60 * 60,
        ),
    )


def credentials_match(username: str, password: str, config: AuthConfig) -> bool:
    """Compare both fields without short-circuiting on the username."""

    username_matches = secrets.compare_digest(
        username.encode("utf-8"), config.username.encode("utf-8")
    )
    password_matches = secrets.compare_digest(
        password.encode("utf-8"), config.password.encode("utf-8")
    )
    return username_matches and password_matches


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_session(config: AuthConfig, now: int | None = None) -> tuple[str, int]:
    issued_at = int(time.time() if now is None else now)
    expires_at = issued_at + config.session_ttl_seconds
    payload = json.dumps(
        {
            "sub": config.username,
            "kind": "clinician",
            "iat": issued_at,
            "exp": expires_at,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded_payload = _encode(payload)
    signature = hmac.new(
        config.session_secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_encode(signature)}", expires_at


def verify_session(
    token: str | None, config: AuthConfig, now: int | None = None
) -> ClinicianSession:
    if not token:
        raise SessionError("session_required")
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        provided_signature = _decode(encoded_signature)
    except (ValueError, TypeError):
        raise SessionError("session_invalid") from None

    try:
        expected_signature = hmac.new(
            config.session_secret.encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
    except UnicodeEncodeError:
        raise SessionError("session_invalid") from None
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise SessionError("session_invalid")

    try:
        payload = json.loads(_decode(encoded_payload))
        username = payload["sub"]
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SessionError("session_invalid") from None

    current_time = int(time.time() if now is None else now)
    if payload.get("kind") != "clinician" or not secrets.compare_digest(
        str(username).encode("utf-8"), config.username.encode("utf-8")
    ):
        raise SessionError("session_invalid")
    if expires_at <= current_time:
        raise SessionError("session_expired")
    return ClinicianSession(username=str(username), expires_at=expires_at)


_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
_attempts_lock = threading.Lock()


def login_retry_after(client_key: str, config: AuthConfig) -> int | None:
    """Record a login attempt and return seconds to wait when over quota."""

    now = time.monotonic()
    cutoff = now - config.login_window_seconds
    with _attempts_lock:
        attempts = _attempts[client_key]
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= config.login_attempts:
            return max(1, int(config.login_window_seconds - (now - attempts[0])))
        attempts.append(now)
    return None


def reset_login_rate_limits() -> None:
    """Clear in-memory throttle state (used by tests and process lifecycle)."""

    with _attempts_lock:
        _attempts.clear()

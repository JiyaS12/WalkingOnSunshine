"""Bounded, non-retrying HTTP adapter for the separately deployed phone service."""

import hashlib
import hmac
import json
import os
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from integration_models import PhoneSnapshot


class PhoneConfigurationError(RuntimeError):
    pass


class PhoneOutcomeUnknown(RuntimeError):
    pass


class PhoneClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 10,
        transport: httpx.BaseTransport | None = None,
    ):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or (
                parsed.scheme == "http"
                and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
            or len(token) < 16
            or not 1 <= timeout <= 30
        ):
            raise PhoneConfigurationError("phone service is not configured")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.transport = transport

    def fingerprint(self, payload: dict) -> str:
        return hmac.new(
            self.token.encode(),
            json.dumps(payload, sort_keys=True).encode(),
            hashlib.sha256,
        ).hexdigest()

    def request(
        self, method: str, path: str, payload: dict | None = None
    ) -> PhoneSnapshot:
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=min(3, self.timeout)),
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
                headers={"Authorization": f"Bearer {self.token}"},
            ) as client:
                response = client.request(method, path, json=payload)
                response.raise_for_status()
                return PhoneSnapshot.model_validate(response.json())
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            raise PhoneOutcomeUnknown("phone service outcome is unknown") from exc


def get_phone_client() -> PhoneClient:
    try:
        return PhoneClient(
            os.getenv("PHONE_SERVICE_BASE_URL", "http://127.0.0.1:8001"),
            os.getenv("OPERATOR_TOKEN", ""),
            float(os.getenv("PHONE_SERVICE_TIMEOUT_SECONDS", "10")),
        )
    except ValueError as exc:
        raise PhoneConfigurationError("phone service is not configured") from exc

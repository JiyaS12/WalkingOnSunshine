"""Authenticated, bounded HTTP requests without transport retries or redirects."""

import asyncio
import os
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from .integration_contract import (
    CallResponse, PatientMetadata, PhoneSnapshot, RegisteredCall,
    StoredSurveyResponse, WalkingView,
)


class BackendError(RuntimeError):
    def __init__(self, status: int = 503):
        super().__init__("Main backend request could not be confirmed.")
        self.status = status


def validate_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.path not in {"", "/"} or any(c.isspace() for c in value)
        or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
    ):
        raise ValueError("Use an HTTPS origin, or HTTP on loopback only.")
    return value.rstrip("/")


class MainBackend:
    def __init__(
        self, base_url: str, token: str, timeout: float = 5,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = validate_origin(base_url)
        if not token or not 0 < timeout <= 30:
            raise ValueError("SURVEY_INGEST_TOKEN and a bounded timeout are required.")
        self.token = token
        self.timeout = timeout
        self.transport = transport

    @classmethod
    def from_env(cls) -> "MainBackend":
        return cls(
            os.environ.get("MAIN_BACKEND_URL", "http://127.0.0.1:8000"),
            os.environ.get("SURVEY_INGEST_TOKEN", ""),
        )

    async def request(self, method: str, path: str, body: dict[str, object] | None = None) -> object:
        try:
            async with asyncio.timeout(self.timeout):
                async with httpx.AsyncClient(
                    base_url=self.base_url, transport=self.transport, timeout=self.timeout,
                    follow_redirects=False, trust_env=False,
                    headers={"X-Survey-Token": self.token},
                ) as client:
                    response = await client.request(method, path, json=body)
                    if response.status_code != 200:
                        raise BackendError(response.status_code)
                    return response.json()
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            raise BackendError() from exc

    async def patient(self, patient_id: str) -> PatientMetadata:
        try:
            return PatientMetadata.model_validate(
                await self.request("GET", f"/api/integration/patients/{patient_id}")
            )
        except ValidationError as exc:
            raise BackendError() from exc

    def call_path(self, patient_id: str, call_id: str) -> str:
        return f"/api/integration/patients/{patient_id}/calls/{call_id}"

    async def call(self, patient_id: str, call_id: str) -> RegisteredCall:
        try:
            return CallResponse.model_validate(
                await self.request("GET", self.call_path(patient_id, call_id))
            ).call
        except ValidationError as exc:
            raise BackendError() from exc

    async def status(self, snapshot: PhoneSnapshot) -> None:
        await self.request(
            "POST", self.call_path(snapshot.patient_id, snapshot.call_id) + "/status",
            snapshot.model_dump(),
        )

    async def submit(self, payload: dict[str, object]) -> StoredSurveyResponse:
        try:
            return StoredSurveyResponse.model_validate(await self.request("POST", "/api/submit-survey", payload))
        except ValidationError as exc:
            raise BackendError() from exc

    async def walking(self, patient_id: str, call_id: str) -> WalkingView:
        try:
            return WalkingView.model_validate(
                await self.request("GET", self.call_path(patient_id, call_id) + "/walking")
            )
        except ValidationError as exc:
            raise BackendError() from exc

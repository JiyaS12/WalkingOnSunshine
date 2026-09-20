"""Twilio dispatch without automatic retry, including ambiguous HTTP outcomes.

References: https://www.twilio.com/docs/voice/api/call-resource
https://www.twilio.com/docs/messaging/api/message-resource
"""

import asyncio
import logging
from typing import Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from .config import TelephonySettings
from .twilio import TWILIO_API_ROOT

logger = logging.getLogger(__name__)


class ProviderRejected(RuntimeError):
    pass


class ProviderUnknown(RuntimeError):
    pass


class ProviderResult(BaseModel):
    sid: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    status: str


class PhoneProvider(Protocol):
    async def call(self, to_number: str, voice_url: str, status_url: str) -> ProviderResult: ...
    async def sms(self, to_number: str, body: str, status_url: str) -> ProviderResult: ...


class TwilioProvider:
    def __init__(self, settings: TelephonySettings, transport: httpx.AsyncBaseTransport | None = None):
        settings.require_outbound()
        self.settings = settings
        self.transport = transport

    async def _post(self, resource: str, fields: dict[str, str | list[str]]) -> ProviderResult:
        settings = self.settings
        try:
            async with asyncio.timeout(8):
                async with httpx.AsyncClient(
                    timeout=8, transport=self.transport, follow_redirects=False, trust_env=False,
                    auth=(settings.twilio_account_sid or "", settings.twilio_auth_token or ""),
                ) as client:
                    response = await client.post(
                        f"{TWILIO_API_ROOT}/Accounts/{settings.twilio_account_sid}/{resource}.json",
                        data=fields,
                    )
                    if 400 <= response.status_code < 500 and response.status_code != 408:
                        logger.warning(
                            "Twilio rejected %s dispatch (%d): error code %s",
                            resource, response.status_code, _rejection_code(response),
                        )
                        raise ProviderRejected("Provider rejected dispatch.")
                    if response.status_code not in {200, 201}:
                        raise ProviderUnknown("Provider outcome unknown.")
                    return ProviderResult.model_validate(response.json())
        except (httpx.HTTPError, TimeoutError, ValidationError, ValueError) as exc:
            raise ProviderUnknown("Provider outcome unknown.") from exc

    async def call(self, to_number: str, voice_url: str, status_url: str) -> ProviderResult:
        return await self._post("Calls", {
            "To": to_number, "From": self.settings.twilio_from_number or "",
            "Url": voice_url, "Method": "POST", "StatusCallback": status_url,
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
        })

    async def sms(self, to_number: str, body: str, status_url: str) -> ProviderResult:
        return await self._post("Messages", {
            "To": to_number, "From": self.settings.twilio_from_number or "",
            "Body": body, "StatusCallback": status_url,
        })


def _rejection_code(response: httpx.Response) -> str:
    """Twilio's numeric error code only; the message may echo the destination number."""

    try:
        body = response.json()
    except ValueError:
        return "unknown"
    code = body.get("code") if isinstance(body, dict) else None
    return str(code) if isinstance(code, int) else "unknown"


class FakePhoneProvider:
    """Injection-only offline provider; never selected by environment variables."""

    def __init__(self):
        self.calls = 0
        self.messages = 0
        self.last_body: str | None = None
        self.sms_outcome = "sent"

    async def call(self, to_number: str, voice_url: str, status_url: str) -> ProviderResult:
        self.calls += 1
        return ProviderResult(sid=f"CAfake{self.calls}", status="queued")

    async def sms(self, to_number: str, body: str, status_url: str) -> ProviderResult:
        self.messages += 1
        self.last_body = body
        if self.sms_outcome == "rejected":
            raise ProviderRejected()
        if self.sms_outcome == "unknown":
            raise ProviderUnknown()
        return ProviderResult(sid=f"SMfake{self.messages}", status=self.sms_outcome)

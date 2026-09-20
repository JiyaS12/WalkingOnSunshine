from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


class TelephonyConfigurationError(RuntimeError):
    """Raised when required telephony configuration is missing or malformed."""


@dataclass(frozen=True)
class TelephonySettings:
    deepgram_api_key: str | None
    twilio_account_sid: str | None
    twilio_auth_token: str | None
    twilio_from_number: str | None
    public_base_url: str | None
    stt_model: str
    tts_model: str
    utterance_end_ms: int
    max_call_seconds: int

    @property
    def deepgram_ready(self) -> bool:
        return bool(self.deepgram_api_key)

    @property
    def twilio_ready(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_from_number)

    @property
    def ready(self) -> bool:
        return self.deepgram_ready and self.twilio_ready and bool(self.public_base_url)

    def require_outbound(self) -> None:
        missing = [
            name
            for name, value in (
                ("DEEPGRAM_API_KEY", self.deepgram_api_key),
                ("TWILIO_ACCOUNT_SID", self.twilio_account_sid),
                ("TWILIO_AUTH_TOKEN", self.twilio_auth_token),
                ("TWILIO_FROM_NUMBER", self.twilio_from_number),
                ("PUBLIC_BASE_URL", self.public_base_url),
            )
            if not value
        ]
        if missing:
            raise TelephonyConfigurationError(
                "Missing telephony configuration: " + ", ".join(missing)
            )

    def webhook_url(self, path: str) -> str:
        return f"{self._base()}{path}"

    def websocket_url(self, path: str) -> str:
        parsed = urlparse(self._base())
        if parsed.scheme not in {"http", "https"}:
            raise TelephonyConfigurationError(
                "PUBLIC_BASE_URL must be an http(s) URL reachable from Twilio."
            )
        return f"wss://{parsed.netloc}{parsed.path}{path}"

    def _base(self) -> str:
        if not self.public_base_url:
            raise TelephonyConfigurationError("PUBLIC_BASE_URL is not configured.")
        return self.public_base_url.rstrip("/")


def load_settings(env: dict[str, str] | None = None) -> TelephonySettings:
    source = env if env is not None else os.environ
    return TelephonySettings(
        deepgram_api_key=source.get("DEEPGRAM_API_KEY") or None,
        twilio_account_sid=source.get("TWILIO_ACCOUNT_SID") or None,
        twilio_auth_token=source.get("TWILIO_AUTH_TOKEN") or None,
        twilio_from_number=source.get("TWILIO_FROM_NUMBER") or None,
        public_base_url=source.get("PUBLIC_BASE_URL") or None,
        stt_model=source.get("DEEPGRAM_STT_MODEL") or "nova-3",
        tts_model=source.get("DEEPGRAM_TTS_MODEL") or "aura-2-thalia-en",
        utterance_end_ms=int(source.get("UTTERANCE_END_MS") or 1200),
        max_call_seconds=int(source.get("MAX_CALL_SECONDS") or 600),
    )

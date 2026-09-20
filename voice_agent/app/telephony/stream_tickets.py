"""One-time tickets that tie a media-stream websocket to a call we placed.

Twilio signs its HTTP webhooks but not the media-stream websocket handshake, so
the websocket alone cannot prove who is on the other end. The signature-checked
``/twilio/voice`` webhook therefore issues a ticket per call and hands it to
Twilio inside the TwiML ``<Parameter>`` list; Twilio echoes it back in the
stream's ``start`` message, where it is redeemed exactly once. A stranger who
finds the public URL has no ticket and is disconnected before any survey,
transcription, or synthesis happens.
"""

from __future__ import annotations

import hmac
import secrets
import time
from typing import Callable

DEFAULT_TTL_SECONDS = 10 * 60


class StreamTickets:
    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._issued: dict[str, tuple[str, float]] = {}

    def issue(self, session_id: str) -> str:
        """Mint a fresh ticket for a call, replacing any earlier one."""

        self._expire()
        token = secrets.token_urlsafe(32)
        self._issued[session_id] = (token, self._clock() + self.ttl_seconds)
        return token

    def redeem(self, session_id: str, token: str | None) -> bool:
        """Consume the ticket for ``session_id`` if ``token`` matches it."""

        self._expire()
        issued = self._issued.pop(session_id, None)
        if issued is None or not token:
            return False
        expected, _ = issued
        return hmac.compare_digest(expected.encode(), token.encode())

    def _expire(self) -> None:
        now = self._clock()
        for session_id in [key for key, (_, until) in self._issued.items() if until <= now]:
            del self._issued[session_id]

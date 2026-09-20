from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Sequence
from urllib.parse import urlencode

import websockets

DEEPGRAM_LISTEN_URL = "wss://api.deepgram.com/v1/listen"
KEEPALIVE_TIMEOUT_SECONDS = 60.0
# Words the survey lives or dies on, boosted so a narrowband phone line does not
# turn "mild" into "my old". Nova-3 takes them as ``keyterm``; older models
# (including the phone-tuned ``nova-2-phonecall``) as weighted ``keywords``.
KEYWORD_BOOST = 1.5
# How long a pause Deepgram treats as the end of a phrase. Callers on a phone
# hesitate mid-answer ("like... a moderate amount"), so this is generous.
ENDPOINTING_MS = 500
ANSWER_KEYTERMS = (
    "none",
    "mild",
    "moderate",
    "severe",
    "extreme",
    "yes",
    "no",
    "repeat",
    "ready",
    "stop",
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeechEvent:
    """Normalized Deepgram streaming event.

    ``kind`` is one of ``speech_started``, ``transcript`` or ``utterance_end``.
    """

    kind: str
    text: str = ""
    is_final: bool = False


def listen_url(
    model: str,
    utterance_end_ms: int,
    keyterms: Sequence[str] = ANSWER_KEYTERMS,
) -> str:
    query = urlencode(
        {
            "model": model,
            "language": "en-US",
            "encoding": "mulaw",
            "sample_rate": 8000,
            "channels": 1,
            "punctuate": "true",
            "smart_format": "true",
            "interim_results": "true",
            "vad_events": "true",
            "endpointing": ENDPOINTING_MS,
            "utterance_end_ms": utterance_end_ms,
        }
    )
    if keyterms:
        if model.startswith("nova-3"):
            boost = [("keyterm", term) for term in keyterms]
        else:
            boost = [("keywords", f"{term}:{KEYWORD_BOOST}") for term in keyterms]
        query += "&" + urlencode(boost)
    return f"{DEEPGRAM_LISTEN_URL}?{query}"


def parse_message(raw: str | bytes) -> SpeechEvent | None:
    """Translate a raw Deepgram websocket message into a :class:`SpeechEvent`."""

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    message_type = payload.get("type")
    if message_type == "SpeechStarted":
        return SpeechEvent("speech_started")
    if message_type == "UtteranceEnd":
        return SpeechEvent("utterance_end")
    if message_type != "Results":
        return None
    try:
        transcript = payload["channel"]["alternatives"][0]["transcript"]
    except (KeyError, IndexError, TypeError):
        return None
    transcript = transcript.strip()
    if not transcript:
        return None
    return SpeechEvent("transcript", transcript, bool(payload.get("is_final")))


class DeepgramTranscriber:
    """Streaming speech-to-text over Deepgram's listen websocket.

    Audio is pushed in as raw 8 kHz mu-law frames, exactly as Twilio delivers
    them, and recognized speech comes back through :meth:`events`.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "nova-3",
        utterance_end_ms: int = 1200,
        connect: Callable[..., Awaitable[object]] | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.utterance_end_ms = utterance_end_ms
        self._connect = connect or websockets.connect
        self._socket = None
        self._closed = False

    async def _open(self) -> object:
        return await self._connect(
            listen_url(self.model, self.utterance_end_ms),
            additional_headers={"Authorization": f"Token {self.api_key}"},
            ping_timeout=KEEPALIVE_TIMEOUT_SECONDS,
        )

    async def _reconnect(self, dead: object) -> None:
        """Replace a socket that dropped mid-call, unless someone beat us to it."""

        if self._closed or self._socket is not dead:
            return
        logger.warning("Deepgram socket dropped; reconnecting")
        self._socket = None
        self._socket = await self._open()

    async def __aenter__(self) -> "DeepgramTranscriber":
        self._socket = await self._open()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def send_audio(self, frame: bytes) -> None:
        """Push one audio frame, reopening the socket if Deepgram dropped it."""

        if self._socket is None:
            raise RuntimeError("Transcriber is not connected.")
        socket = self._socket
        try:
            await socket.send(frame)
        except websockets.exceptions.WebSocketException:
            await self._reconnect(socket)
            if self._socket is not None:
                await self._socket.send(frame)

    async def finalize(self) -> None:
        if self._socket is not None:
            await self._socket.send(json.dumps({"type": "Finalize"}))

    async def close(self) -> None:
        self._closed = True
        socket, self._socket = self._socket, None
        if socket is None:
            return
        try:
            await socket.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
        await socket.close()

    async def events(self) -> AsyncIterator[SpeechEvent]:
        """Yield recognized speech for the life of the call, across reconnects."""

        if self._socket is None:
            raise RuntimeError("Transcriber is not connected.")
        while not self._closed:
            socket = self._socket
            if socket is None:
                return
            try:
                async for raw in socket:
                    event = parse_message(raw)
                    if event is not None:
                        yield event
            except websockets.exceptions.WebSocketException:
                pass
            if self._closed:
                return
            try:
                await self._reconnect(socket)
            except OSError:
                await asyncio.sleep(0.5)

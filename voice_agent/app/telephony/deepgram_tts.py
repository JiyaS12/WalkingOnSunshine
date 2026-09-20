from __future__ import annotations

import asyncio
import json
from urllib import error, request
from urllib.parse import urlencode

DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak"

# 8 kHz mu-law is one byte per sample, so a 20 ms frame is 160 bytes.
MULAW_FRAME_BYTES = 160


class SpeechSynthesisError(RuntimeError):
    """Raised when Deepgram could not synthesize the prompt audio."""


def speak_url(model: str) -> str:
    query = urlencode(
        {
            "model": model,
            "encoding": "mulaw",
            "sample_rate": 8000,
            "container": "none",
        }
    )
    return f"{DEEPGRAM_SPEAK_URL}?{query}"


def synthesize_mulaw(text: str, api_key: str, model: str, timeout: float = 20.0) -> bytes:
    """Render ``text`` to headerless 8 kHz mu-law audio suitable for Twilio."""

    if not text.strip():
        raise SpeechSynthesisError("Refusing to synthesize empty prompt text.")
    req = request.Request(
        speak_url(model),
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            audio = response.read()
    except error.HTTPError as exc:
        raise SpeechSynthesisError(
            f"Deepgram speech synthesis failed with status {exc.code}."
        ) from exc
    except Exception as exc:
        raise SpeechSynthesisError("Deepgram speech synthesis failed.") from exc
    if not audio:
        raise SpeechSynthesisError("Deepgram returned no audio for the prompt.")
    return audio


async def synthesize_mulaw_async(text: str, api_key: str, model: str) -> bytes:
    return await asyncio.to_thread(synthesize_mulaw, text, api_key, model)


def frames(audio: bytes, size: int = MULAW_FRAME_BYTES) -> list[bytes]:
    """Split mu-law audio into fixed-size frames for paced playback."""

    return [audio[offset : offset + size] for offset in range(0, len(audio), size)]

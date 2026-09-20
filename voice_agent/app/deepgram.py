"""Server-side Deepgram speech recognition and speech generation."""

from __future__ import annotations

import os
from functools import lru_cache
from collections.abc import Iterator

import httpx

DEFAULT_VOICE = "aura-2-thalia-en"


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    # Reuse connections for recognition and speech instead of repeating TLS
    # setup on every turn. HTTPX clients support use from multiple threads.
    return httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0))


def _http_error(code: int) -> str:
    if code in {401, 403}:
        return "Deepgram rejected the API key or its permissions. Check the server configuration."
    if code in {402, 429}:
        return "Deepgram is unavailable due to account credit or rate limits. Please try again later."
    return f"Deepgram could not process the request (HTTP {code}). Please try again."


def _post(endpoint: str, **kwargs) -> httpx.Response:
    key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Deepgram is not configured. Set DEEPGRAM_API_KEY and restart the app.")
    try:
        response = _client().post(
            f"https://api.deepgram.com/v1/{endpoint}",
            headers={"Authorization": f"Token {key}", **kwargs.pop("headers", {})},
            timeout=30.0,
            **kwargs,
        )
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(_http_error(exc.response.status_code)) from None
    except httpx.RequestError:
        raise RuntimeError("Could not reach Deepgram. Check the connection and try again.") from None


def transcribe_with_deepgram(audio: bytes, content_type: str) -> str:
    if not audio:
        raise ValueError("The recording was empty. Resume listening and speak your answer again.")
    response = _post(
        "listen", content=audio, headers={"Content-Type": content_type or "audio/webm"},
        params={"model": "nova-3", "smart_format": "true", "punctuate": "true", "language": "en-US"},
    )
    try:
        transcript = response.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
        if not isinstance(transcript, str):
            raise TypeError
    except (ValueError, KeyError, IndexError, TypeError):
        raise RuntimeError("Deepgram returned no usable transcript. Please record your answer again.") from None
    if not transcript.strip():
        raise RuntimeError(
            "No speech was detected. Check your microphone permission and input device, "
            "then resume listening and speak your answer again."
        )
    return transcript.strip()


def synthesize_with_deepgram(text: str) -> bytes:
    if not text.strip() or len(text) > 2000:
        raise ValueError("Speech text must contain between 1 and 2000 characters.")
    response = _post(
        "speak", json={"text": text},
        params={"model": os.getenv("DEEPGRAM_TTS_MODEL", DEFAULT_VOICE), "encoding": "mp3"},
    )
    if not response.headers.get("content-type", "").startswith("audio/") or not response.content:
        raise RuntimeError("Deepgram returned no playable speech. Please try again.")
    return response.content


def stream_speech_with_deepgram(text: str) -> Iterator[bytes]:
    """Yield MP3 bytes as they arrive, without buffering a complete utterance."""
    if not text.strip() or len(text) > 2000:
        raise ValueError("Speech text must contain between 1 and 2000 characters.")
    key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Deepgram is not configured. Set DEEPGRAM_API_KEY and restart the app.")
    try:
        with _client().stream(
            "POST", "https://api.deepgram.com/v1/speak",
            headers={"Authorization": f"Token {key}"},
            json={"text": text},
            params={"model": os.getenv("DEEPGRAM_TTS_MODEL", DEFAULT_VOICE), "encoding": "mp3"},
        ) as response:
            response.raise_for_status()
            if not response.headers.get("content-type", "").startswith("audio/"):
                raise RuntimeError("Deepgram returned no playable speech. Please try again.")
            received = False
            for chunk in response.iter_bytes():
                if chunk:
                    received = True
                    yield chunk
            if not received:
                raise RuntimeError("Deepgram returned no playable speech. Please try again.")
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(_http_error(exc.response.status_code)) from None
    except httpx.RequestError:
        raise RuntimeError("Could not reach Deepgram. Check the connection and try again.") from None

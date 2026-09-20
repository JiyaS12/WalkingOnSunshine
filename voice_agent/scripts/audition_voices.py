"""Render the survey opening in several Aura-2 voices so you can pick one by ear.

Usage: ``python scripts/audition_voices.py [voice ...]`` with DEEPGRAM_API_KEY set.
Writes one WAV per voice into ``voice_samples/``; open them with any player, then
put the winner in ``.env`` as ``DEEPGRAM_TTS_MODEL`` and restart ``phone_app.py``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.conversation_policy import INTRO  # noqa: E402
from app.telephony.deepgram_tts import DEEPGRAM_SPEAK_URL  # noqa: E402

# Warm, calm voices that suit a post-surgery check-in; thalia is the current default.
CANDIDATES = (
    "aura-2-thalia-en",
    "aura-2-helena-en",
    "aura-2-harmonia-en",
    "aura-2-vesta-en",
    "aura-2-cora-en",
    "aura-2-luna-en",
    "aura-2-arcas-en",
    "aura-2-orion-en",
    "aura-2-pluto-en",
)
OUTPUT_DIR = Path("voice_samples")


def render_wav(text: str, api_key: str, model: str) -> bytes:
    query = urlencode({"model": model, "encoding": "linear16", "sample_rate": 24000, "container": "wav"})
    req = request.Request(
        f"{DEEPGRAM_SPEAK_URL}?{query}",
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as response:
        return response.read()


def main(voices: list[str]) -> int:
    api_key = os.environ["DEEPGRAM_API_KEY"]
    OUTPUT_DIR.mkdir(exist_ok=True)
    failures = 0
    for model in voices or CANDIDATES:
        try:
            audio = render_wav(INTRO, api_key, model)
        except error.HTTPError as exc:
            failures += 1
            print(f"{model}: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:120]}")
            continue
        path = OUTPUT_DIR / f"{model}.wav"
        path.write_bytes(audio)
        print(f"{model}: wrote {path} ({len(audio) // 1024} KiB)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

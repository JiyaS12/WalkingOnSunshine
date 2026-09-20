"""Manual smoke check: synthesize a prompt and stream it back through Deepgram.

Usage: ``python scripts/check_deepgram.py`` with DEEPGRAM_API_KEY set. It verifies
the exact mu-law 8 kHz formats the phone call uses, without placing a call.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.telephony.deepgram_stt import DeepgramTranscriber  # noqa: E402
from app.telephony.deepgram_tts import frames, synthesize_mulaw  # noqa: E402

PHRASE = "Over the past week, how much hip pain have you experienced going up or down stairs?"


async def main() -> int:
    api_key = os.environ["DEEPGRAM_API_KEY"]
    audio = synthesize_mulaw(PHRASE, api_key, os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en"))
    print(f"tts: {len(audio)} mu-law bytes ({len(audio) / 8000:.1f}s)")

    transcripts: list[str] = []
    async with DeepgramTranscriber(api_key, utterance_end_ms=1000) as transcriber:

        async def reader() -> None:
            async for event in transcriber.events():
                print("event:", event)
                if event.kind == "transcript" and event.is_final:
                    transcripts.append(event.text)

        task = asyncio.create_task(reader())
        for frame in frames(audio):
            await transcriber.send_audio(frame)
            await asyncio.sleep(0.02)
        await transcriber.finalize()
        await asyncio.sleep(3)
        task.cancel()

    heard = " ".join(transcripts)
    print("heard:", heard)
    return 0 if "hip pain" in heard.casefold() else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

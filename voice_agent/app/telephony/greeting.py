"""A recorded greeting from the clinician, played before the automated survey.

The clip is raw 8 kHz mono mu-law, the format Twilio streams, so it goes out
frame by frame exactly like synthesized speech. Anything wrong with the file
disables the clip rather than the call: the survey still opens with its own intro.

Convert a recording with:
    ffmpeg -i greeting.m4a -ar 8000 -ac 1 -f mulaw assets/doctor_greeting.ulaw
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SAMPLE_RATE = 8000
MAX_GREETING_SECONDS = 60
# Trailing pause between the recording and the automated intro.
GREETING_TAIL_SECONDS = 0.8


def load_greeting_audio(path: str | None) -> bytes | None:
    if not path:
        return None
    try:
        audio = Path(path).read_bytes()
    except OSError as error:
        logger.warning("Doctor greeting clip unavailable (%s); calls open without it", error)
        return None
    if not audio:
        logger.warning("Doctor greeting clip is empty; calls open without it")
        return None
    if len(audio) > MAX_GREETING_SECONDS * SAMPLE_RATE:
        logger.warning(
            "Doctor greeting clip is longer than %ds; calls open without it", MAX_GREETING_SECONDS
        )
        return None
    logger.info("Doctor greeting clip loaded: %.1fs", len(audio) / SAMPLE_RATE)
    return audio

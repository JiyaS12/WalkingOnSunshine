from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .conversation_policy import (
    COMPLETE,
    GAIT_INTRO,
    GAIT_UNAVAILABLE,
    INTRO,
    LINK_DECLINED,
    LINK_FAILED,
    LINK_MISSING,
    LINK_NOT_RECEIVED,
    LINK_REMINDER,
    LINK_SENT,
    PAUSE_EXPIRED,
    WALKTHROUGH_CLOSING,
    WALKTHROUGH_COUNTDOWN,
    WALKTHROUGH_GUIDANCE,
)


@dataclass(frozen=True)
class VoiceTurn:
    speaker: str
    text: str


class VoiceAdapter:
    """Thin boundary for speech-to-text and text-to-speech integration.

    The adapter is intentionally simple: it only standardizes the conversation flow
    and keeps the AI logic decoupled from any specific provider. This repo does not
    implement a live telephony stack yet; it only prepares the interaction contract.
    """

    def __init__(self, tts: Callable[[str], str] | None = None, stt: Callable[[str], str] | None = None):
        self.tts = tts or (lambda text: text)
        self.stt = stt or (lambda text: text)

    def doctor_intro(self, condition_category: str) -> VoiceTurn:
        """Compatibility entry point; a real doctor's recording is a separate asset."""
        return VoiceTurn("assistant", self.tts(INTRO))

    def ask_question(self, question_prompt: str) -> VoiceTurn:
        return VoiceTurn("assistant", self.tts(question_prompt))

    def confirmation_prompt(self, confirmation_text: str) -> VoiceTurn:
        return VoiceTurn("assistant", self.tts(confirmation_text))

    def closing_script(self) -> VoiceTurn:
        return VoiceTurn("assistant", self.tts(COMPLETE))

    def gait_request(self) -> VoiceTurn:
        """Why the call is not over yet: the walking video the care team wants."""
        return VoiceTurn("assistant", self.tts(GAIT_INTRO))

    def link_sent_confirmation(self) -> VoiceTurn:
        """Spoken right after the gait-checker link is texted (or logged as a fallback)."""
        return VoiceTurn("assistant", self.tts(LINK_SENT))

    def link_reminder(self) -> VoiceTurn:
        """For a caller still finding the text; asked again, gently."""
        return VoiceTurn("assistant", self.tts(LINK_REMINDER))

    def link_missing(self) -> VoiceTurn:
        """The caller says the text has not arrived yet; give it a moment."""
        return VoiceTurn("assistant", self.tts(LINK_MISSING))

    def link_not_received(self) -> VoiceTurn:
        """The text never reached the caller; close without camera steps."""
        return VoiceTurn("assistant", self.tts(LINK_NOT_RECEIVED))

    def link_declined(self) -> VoiceTurn:
        """The caller asked to stop while we waited on the link."""
        return VoiceTurn("assistant", self.tts(LINK_DECLINED))

    def link_failed(self) -> VoiceTurn:
        """The text was promised but did not send; own it and close the call."""
        return VoiceTurn("assistant", self.tts(LINK_FAILED))

    def gait_unavailable(self) -> VoiceTurn:
        """Closing for a call that cannot text a link at all; promises nothing."""
        return VoiceTurn("assistant", self.tts(GAIT_UNAVAILABLE))

    def pause_expired(self) -> VoiceTurn:
        """A paused caller never came back; end truthfully and hand to a clinician."""
        return VoiceTurn("assistant", self.tts(PAUSE_EXPIRED))

    def walkthrough_guidance(self) -> VoiceTurn:
        """Live, step-by-step camera setup guidance -- fixed script, not model-generated."""
        return VoiceTurn("assistant", self.tts(WALKTHROUGH_GUIDANCE))

    def walkthrough_countdown(self) -> VoiceTurn:
        return VoiceTurn("assistant", self.tts(WALKTHROUGH_COUNTDOWN))

    def walkthrough_closing(self) -> VoiceTurn:
        """Warm, generic closing regardless of what happened during the walk -- the voice
        side has no visibility into the gait checker's own camera feed."""
        return VoiceTurn("assistant", self.tts(WALKTHROUGH_CLOSING))

    def normalize_input(self, transcript: str) -> str:
        return self.stt(transcript).strip()

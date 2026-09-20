from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable

from ..conversation_policy import COMPLETE, sms_body
from ..gait_handoff import GaitHandoff, GaitHandoffService
from ..persistence import InMemoryPersistence
from ..survey_engine import SafeSurveyEngine
from ..voice_adapter import VoiceAdapter

logger = logging.getLogger(__name__)

TERMINAL_STATES = {"complete", "escalated", "stopped"}
# How long the caller walks in front of the camera before we sign off.
WALK_SECONDS = 15.0

Speaker = Callable[[str], Awaitable[None]]
SmsSender = Callable[[str, str], Awaitable[None]]


class PhoneCallSession:
    """Drives one phone survey: spoken prompts out, recognized speech in.

    The class owns no transport. Audio arrives as finished utterance text and
    leaves through the ``speak`` coroutine, so the same session runs over
    Twilio, a local socket, or a test double.
    """

    def __init__(
        self,
        engine: SafeSurveyEngine,
        speak: Speaker,
        session_id: str,
        persistence: InMemoryPersistence | None = None,
        voice: VoiceAdapter | None = None,
        handoff_service: GaitHandoffService | None = None,
        max_silent_reprompts: int = 2,
        to_number: str | None = None,
        sms_sender: SmsSender | None = None,
        walk_seconds: float = WALK_SECONDS,
    ):
        self.engine = engine
        self.speak = speak
        self.session_id = session_id
        self.persistence = persistence or InMemoryPersistence()
        self.voice = voice or VoiceAdapter()
        self.handoff_service = handoff_service or GaitHandoffService()
        self.max_silent_reprompts = max_silent_reprompts
        self.to_number = to_number
        self.sms_sender = sms_sender
        self.walk_seconds = walk_seconds
        self.handoff: GaitHandoff | None = None
        self._buffer: list[str] = []
        self._last_prompt = ""
        self._silent_reprompts = 0
        self._awaiting_link = False

    @property
    def finished(self) -> bool:
        """Whether the call may hang up; the gait walkthrough outlives the survey."""

        return self.engine.session.state in TERMINAL_STATES and not self._awaiting_link

    @property
    def pending_transcript(self) -> str:
        return " ".join(self._buffer).strip()

    async def begin(self) -> None:
        patient = self.engine.patient
        record = self.persistence.start_call(
            self.session_id, patient.patient_code, patient.condition_category.value
        )
        if record.final_status is None:
            record.status = "in_progress"
        # ``start`` already opens with the intro script the voice adapter holds.
        first_prompt = await asyncio.to_thread(self.engine.start)
        await self._say(_spoken(first_prompt))

    def add_transcript(self, text: str) -> None:
        cleaned = text.strip()
        if cleaned:
            self._buffer.append(cleaned)

    def discard_pending(self) -> None:
        """Forget speech recognized while we were still talking over the line."""

        self._buffer.clear()

    async def flush_utterance(self) -> bool:
        """Answer whatever the caller just said. Returns True when the call is over."""

        transcript = self.pending_transcript
        self._buffer.clear()
        if not transcript:
            return self.finished
        self._silent_reprompts = 0
        self.persistence.append_transcript(self.session_id, f"patient: {transcript}")
        if self._awaiting_link:
            return await self._walk_the_caller_through_it()
        # Interpretation may call a language model, which must not block the
        # event loop that keeps call audio flowing in both directions.
        prompt, answer = await asyncio.to_thread(self.engine.handle_response, transcript)
        if answer is not None and answer.confirmed:
            self.persistence.record_answer(
                self.session_id,
                {"question_id": answer.question_id, "value": answer.normalized_value},
            )
        # The closing line is replaced by the gait request, which thanks the
        # caller itself; saying both is two goodbyes in a row.
        if prompt != COMPLETE:
            await self._say(_spoken(prompt))
        return await self._finish_if_done()

    async def handle_silence(self) -> bool:
        """Nudge a caller who has gone quiet, and give up after a few tries."""

        if self.finished:
            return True
        self._silent_reprompts += 1
        if self._awaiting_link:
            # The survey is already answered, so a quiet caller here is someone
            # hunting for the text, not someone to escalate.
            if self._silent_reprompts > self.max_silent_reprompts:
                return await self._walk_the_caller_through_it()
            await self._say(self.voice.link_reminder().text)
            return False
        if self._silent_reprompts > self.max_silent_reprompts:
            self.engine.session.state = "escalated"
            self.engine.session.needs_human_review = True
            await self._say(
                "I did not hear a response, so I will have a clinician follow up with you. Goodbye."
            )
            return await self._finish_if_done()
        await self._say(f"Sorry, I did not catch that. {self._last_prompt}")
        return False

    async def _finish_if_done(self) -> bool:
        if self.engine.session.state not in TERMINAL_STATES:
            return False
        if self.engine.session.state == "complete" and self.handoff is None:
            self.handoff = self.handoff_service.prepare(
                self.engine.patient.patient_code,
                self.engine.patient.condition_category.value,
            )
            await self._send_gait_handoff()
            return False
        if not self.finished:
            return False
        self.persistence.complete_call(self.session_id, self.engine.session.state)
        return True

    async def _send_gait_handoff(self) -> None:
        """Explain the walking video, text the link, then wait on the line for the
        caller to open it, rather than reciting instructions at a dial tone.

        A failed text does not stop the call from completing or the walkthrough
        from playing -- the patient still hears the guidance either way, and a
        failed send is recorded on ``self.handoff`` for follow-up.
        """

        assert self.handoff is not None
        await self._say(self.voice.gait_request().text)
        if self.sms_sender and self.to_number and self.handoff.link:
            try:
                await self.sms_sender(self.to_number, sms_body(self.handoff.link))
                self.handoff.sms_sent = True
            except Exception:
                logger.exception(
                    "Could not text the gait-checker link for session %s", self.session_id
                )
        self._awaiting_link = True
        self._silent_reprompts = 0
        await self._say(self.voice.link_sent_confirmation().text)

    async def _walk_the_caller_through_it(self) -> bool:
        """Camera setup, then the timed walk, once the caller says they are set."""

        self._awaiting_link = False
        await self._say(self.voice.walkthrough_guidance().text)
        await self._say(self.voice.walkthrough_countdown().text)
        await asyncio.sleep(self.walk_seconds)
        await self._say(self.voice.walkthrough_closing().text)
        self.persistence.complete_call(self.session_id, self.engine.session.state)
        return True

    async def _say(self, text: str) -> None:
        self._last_prompt = text
        self.persistence.append_transcript(self.session_id, f"assistant: {text}")
        await self.speak(text)


def _spoken(prompt: str) -> str:
    """Strip the on-screen header lines the engine adds for the text UI.

    Blank lines survive: they are the beats the phone bridge plays as silence.
    """

    paragraphs = []
    for paragraph in prompt.split("\n\n"):
        lines = [line for line in paragraph.splitlines() if line.strip()]
        spoken = [line for line in lines if not _is_header(line)] or lines
        if spoken:
            paragraphs.append(" ".join(spoken))
    return "\n\n".join(paragraphs)


def _is_header(line: str) -> bool:
    """A standalone label, as opposed to a sentence the caller should hear."""

    lowered = line.strip().casefold()
    return (
        lowered.endswith("survey")
        or lowered.startswith("condition:")
        or re.fullmatch(r"question \d+ of \d+\.?", lowered) is not None
    )

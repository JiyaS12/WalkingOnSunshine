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
# Silence intervals a paused caller gets before the call ends for review; the
# phone bridge fires one every SILENCE_TIMEOUT_SECONDS, so this is minutes, not
# the handful of reprompts an unanswered question gets.
MAX_PAUSED_SILENCES = 12
# How many times a caller may report the text has not arrived before we close.
MAX_LINK_MISSING = 2

_STOP_WORDS = re.compile(
    r"\b(stop|hang up|goodbye|bye|no thanks|not now|later|another time|rather not|can'?t do)\b"
)
_MISSING_WORDS = re.compile(
    r"\b(didn'?t|did not|haven'?t|have not|never|nothing|no text|no message|not yet|"
    r"not (?:got|gotten|received|come|arrived|here)|isn'?t here|hasn'?t (?:come|arrived))\b"
)
# "I'm not ready" / "don't have it open yet": a no dressed up in ready words.
_NOT_READY = re.compile(
    r"\b(?:(?:not|isn'?t|ain'?t|hasn'?t|won'?t|wouldn'?t|doesn'?t|didn'?t|can'?t|cannot)\s+"
    r"(?:(?:quite|yet|fully|really|even|actually|still|be|get|seem|seem to)\s+){0,2}"
    r"(?:ready|open(?:ed|ing)?|up|load(?:ed|ing)?|work(?:ing)?|set|done)"
    r"|(?:don'?t|do not|haven'?t|can'?t|cannot|couldn'?t)\s+(?:seem to\s+)?"
    r"(?:have|see|find|get|open|load)"
    r"|(?:don'?t|do not|not sure|unsure)\s+(?:think|know|believe)\b)"
)
# Clause boundaries: punctuation and the connectives a correction follows.
_CLAUSE_BREAK = re.compile(
    r"[,.;!?]+|\b(?:but|though|although|however|anyway|now|and now)\b"
)
# A clause about what happened earlier is background, not the caller's state.
_HISTORICAL = re.compile(
    r"\b(?:at first|before|earlier|initially|originally|the first time|"
    r"(?:was|wasn'?t|were|weren'?t|had|hadn'?t)(?: \w+){0,3}? "
    r"(?:open(?:ed|ing)?|up|loaded|loading|working|ready))\b"
)
# The text is in hand but nothing says it is open yet.
_ARRIVED_WORDS = re.compile(
    r"\b(?:it (?:arrived|came(?: through)?|showed up|just came|is here|'?s here)|"
    r"(?:got|received|have) (?:the|your|a) (?:text|message|link))\b"
)
# Words that assert the link is open in the caller's hand right now.
_READY_WORDS = re.compile(
    r"\b(ready|got it|open(?:ed)?|it'?s up|have it|see it|i'?m (?:set|on|there|in)|"
    r"all set|loaded)\b"
)
# A clause that is nothing but agreement; it means yes only on its own.
_FILLER_CLAUSE = re.compile(
    r"(?:(?:okay|ok|yes|yeah|yep|sure|go ahead|done|fine|alright|please|thanks|thank you)"
    r"(?:\s+|$))+"
)

# An utterance that stops on one of these has not reached its point yet.
_TRAILING_FILLER = re.compile(
    r"\b(?:like|um+|uh+|so|and|or|well|i'?d say|i would say|i think|i mean|it'?s|"
    r"maybe|probably|about|kind of|sort of|it was|i guess)[,.\s]*$"
)

Speaker = Callable[[str], Awaitable[None]]
SmsSender = Callable[[str, str], Awaitable[None]]


def lowest_confidence(current: float | None, new: float | None) -> float | None:
    """The weakest recognizer confidence across the pieces of one utterance."""

    if new is None:
        return current
    return new if current is None else min(current, new)


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
        max_paused_silences: int = MAX_PAUSED_SILENCES,
        speech_lead_seconds: float = 0.0,
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
        self.max_paused_silences = max_paused_silences
        # How far ``speak`` may return ahead of the caller actually hearing it.
        self.speech_lead_seconds = speech_lead_seconds
        self.handoff: GaitHandoff | None = None
        self._buffer: list[str] = []
        self._confidence: float | None = None
        self._last_prompt = ""
        self._silent_reprompts = 0
        self._paused_silences = 0
        self._link_missing = 0
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

    def add_transcript(self, text: str, confidence: float | None = None) -> None:
        cleaned = text.strip()
        if cleaned:
            self._buffer.append(cleaned)
            self._confidence = lowest_confidence(self._confidence, confidence)

    def trailing_off(self) -> bool:
        """Whether the caller paused mid-thought ("I'd say, like...")."""

        return bool(_TRAILING_FILLER.search(self.pending_transcript.lower()))

    def discard_pending(self) -> None:
        """Forget speech recognized while we were still talking over the line."""

        self._buffer.clear()
        self._confidence = None

    async def flush_utterance(self) -> bool:
        """Answer whatever the caller just said. Returns True when the call is over."""

        transcript = self.pending_transcript
        confidence = self._confidence
        self.discard_pending()
        if not transcript:
            return self.finished
        self._silent_reprompts = 0
        self._paused_silences = 0
        self.persistence.append_transcript(self.session_id, f"patient: {transcript}")
        if self._awaiting_link:
            return await self._handle_link_reply(transcript)
        # Interpretation may call a language model, which must not block the
        # event loop that keeps call audio flowing in both directions.
        prompt, answer = await asyncio.to_thread(self.engine.handle_response, transcript, confidence)
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
        if self.engine.session.state == "paused":
            # The caller asked for this quiet, so it is not a missed answer.
            # Stay on the line for "resume" or "stop", within reason.
            self._paused_silences += 1
            if self._paused_silences < self.max_paused_silences:
                return False
            self.engine.session.state = "escalated"
            self.engine.session.needs_human_review = True
            await self._say(self.voice.pause_expired().text)
            return await self._finish_if_done()
        self._silent_reprompts += 1
        if self._awaiting_link:
            # The survey is already answered, so a quiet caller here is someone
            # hunting for the text, not someone to escalate. Nor is silence a
            # yes: without a word from them the camera steps would go nowhere.
            if self._silent_reprompts > self.max_silent_reprompts:
                if self.handoff is not None:
                    self.handoff.notes.append("Caller went quiet before confirming the gait link.")
                await self._say(self.voice.link_no_reply().text)
                return await self._complete_without_walkthrough()
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
            self.handoff = await self.handoff_service.prepare(
                self.engine.patient.patient_code,
                self.engine.patient.condition_category.value,
                self.session_id,
            )
            return await self._send_gait_handoff()
        if not self.finished:
            return False
        self.persistence.complete_call(self.session_id, self.engine.session.state)
        return True

    async def _send_gait_handoff(self) -> bool:
        """Explain the walking video, text the link, then wait on the line for the
        caller to open it, rather than reciting instructions at a dial tone.

        The camera walkthrough only makes sense with a link in the caller's
        hand, so it is offered only once the text has actually gone out. When
        no link can be made, or the send fails, the caller hears a closing that
        promises nothing, the outcome is recorded on ``self.handoff`` for
        follow-up, and the call completes. Returns True when the call is over.
        """

        assert self.handoff is not None
        if not (self.sms_sender and self.to_number and self.handoff.link):
            if self.handoff.status == "prepared":
                self.handoff.status = "unavailable"
                self.handoff.notes.append("Gait link not sent: no SMS route for this call.")
            await self._say(self.voice.gait_unavailable().text)
            return await self._complete_without_walkthrough()
        await self._say(self.voice.gait_request().text)
        try:
            await self.sms_sender(self.to_number, sms_body(self.handoff.link))
        except Exception:
            logger.exception(
                "Could not text the gait-checker link for session %s", self.session_id
            )
            self.handoff.status = "failed"
            self.handoff.notes.append("Gait link not sent: the text message failed.")
            await self._say(self.voice.link_failed().text)
            return await self._complete_without_walkthrough()
        self.handoff.sms_sent = True
        self._awaiting_link = True
        self._silent_reprompts = 0
        await self._say(self.voice.link_sent_confirmation().text)
        return False

    async def _complete_without_walkthrough(self) -> bool:
        self._awaiting_link = False
        self.persistence.complete_call(self.session_id, self.engine.session.state)
        return True

    async def _handle_link_reply(self, transcript: str) -> bool:
        """Only an explicit "I have it" starts the camera steps.

        The text having been accepted by the carrier says nothing about it
        arriving, so "I never got it" is answered with patience and then an
        honest closing, "stop" is honoured, and anything unclear gets the
        reminder rather than a countdown the caller cannot follow.
        """

        intent = link_reply_intent(transcript)
        if intent == "stop":
            await self._say(self.voice.link_declined().text)
            return await self._complete_without_walkthrough()
        if intent == "missing":
            self._link_missing += 1
            if self._link_missing >= MAX_LINK_MISSING:
                if self.handoff is not None:
                    self.handoff.notes.append("Caller reported the gait link never arrived.")
                await self._say(self.voice.link_not_received().text)
                return await self._complete_without_walkthrough()
            await self._say(self.voice.link_missing().text)
            return False
        if intent == "ready":
            return await self._walk_the_caller_through_it()
        await self._say(self.voice.link_reminder().text)
        return False

    async def _walk_the_caller_through_it(self) -> bool:
        """Camera setup, then the timed walk, once the caller says they are set."""

        self._awaiting_link = False
        await self._say(self.voice.walkthrough_guidance().text)
        await self._say(self.voice.walkthrough_countdown().text)
        await asyncio.sleep(self.walk_seconds + self.speech_lead_seconds)
        await self._say(self.voice.walkthrough_closing().text)
        self.persistence.complete_call(self.session_id, self.engine.session.state)
        return True

    async def _say(self, text: str) -> None:
        self._last_prompt = text
        self.persistence.append_transcript(self.session_id, f"assistant: {text}")
        await self.speak(text)


def link_reply_intent(transcript: str) -> str:
    """Classify what a caller said while we wait for them to open the text.

    Returns ``"stop"``, ``"missing"``, ``"ready"`` or ``"unclear"``. A stop
    anywhere wins. Otherwise the caller is read clause by clause and the last
    clause that says something about the link decides, with two limits: a
    clause about earlier ("…although it didn't arrive at first", "I was able
    to open it, though…") is background and yields to any clause about now,
    and bare agreement ("yes", "okay") only counts as a yes when nothing else
    was said. Within a clause, missing and negated readiness beat ready words,
    so "no, I didn't get it" is not a yes, and "it arrived" is not "it is
    open", so it earns the reminder rather than the countdown. A reply that
    says both yes and no about right now is unclear, whatever the order.
    """

    text = transcript.lower()
    if _STOP_WORDS.search(text):
        return "stop"
    current: list[str] = []
    background: list[str] = []
    for clause in _clauses(text):
        if _MISSING_WORDS.search(clause):
            intent = "missing"
        elif _NOT_READY.search(clause):
            intent = "negated"
        elif _READY_WORDS.search(clause):
            intent = "ready"
        elif _ARRIVED_WORDS.search(clause):
            intent = "arrived"
        elif _FILLER_CLAUSE.fullmatch(clause):
            intent = "agreed"
        else:
            continue
        (background if _HISTORICAL.search(clause) else current).append(intent)
    if "ready" in current and ({"missing", "negated"} & set(current)):
        # The caller has said both yes and no about right now; a reminder
        # costs a few seconds, a countdown without the page costs the check.
        return "unclear"
    decisive = [i for i in current if i != "agreed"] or [i for i in background if i != "agreed"]
    if decisive:
        return decisive[-1] if decisive[-1] in {"missing", "ready"} else "unclear"
    if "agreed" in current:
        return "ready"
    return "unclear"


def _clauses(text: str) -> list[str]:
    """Split ``text`` at punctuation and connectives, dropping empty pieces."""

    return [clause.strip() for clause in _CLAUSE_BREAK.split(text) if clause.strip()]


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

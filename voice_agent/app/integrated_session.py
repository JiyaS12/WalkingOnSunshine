"""Combined survey and event-driven walking guidance, independent of audio transport."""

import asyncio
import copy
import re
from collections.abc import Awaitable, Callable

from . import conversation_policy as policy
from .answer_interpreter import AnswerInterpreter, clean_utterance, control_intent
from .integrated_service import IntegratedService
from .integration_contract import CallStatus, PhoneError, RegisteredCall
from .main_backend import BackendError
from .models import ConditionCategory, PatientRecord
from .patient_repository import InMemoryPatientRepository
from .survey_engine import SafeSurveyEngine
from .telephony.call_session import _TRAILING_FILLER, _spoken, link_reply_intent, lowest_confidence

Speaker = Callable[[str], Awaitable[None]]

# Page states that mean the patient has the link open, even if they never said so;
# _CAMERA_STATUSES additionally mean they are already past the Live Camera setup.
_CAMERA_STATUSES = {"calibrating", "ready", "capturing", "captured"}
_PAGE_OPEN_STATUSES = _CAMERA_STATUSES | {"page_ready"}
MAX_LINK_REMINDERS = 2
MAX_CONSENT_RETRIES = 2
_CONSENT_YES = re.compile(
    r"\b(?:yes|yeah|yep|yup|sure|okay|ok|of course|go ahead|please do|that's fine|that is fine|"
    r"sounds good|fine|absolutely|definitely|send it|text me|you can)\b"
)
_CONSENT_NO = re.compile(
    r"\b(?:no|nope|nah|not|don't|do not|rather not|no thanks|no thank you|never|skip|later)\b"
)
# "no problem" / "not a problem" are agreement, not refusal.
_CONSENT_NOT_REFUSAL = re.compile(r"\b(?:no|not a|not really a) (?:problem|worries|issue)\b")


def consent_intent(transcript: str) -> str:
    """Read a reply to the SMS consent question as ``"yes"``, ``"no"`` or ``"unclear"``.

    Any refusal wins over agreement ("yes but not now" is a no); agreement only
    counts when nothing in the reply refuses, so the text never goes out on a
    misheard or hedged answer.
    """
    text = _CONSENT_NOT_REFUSAL.sub(" yes ", transcript.lower())
    if _CONSENT_NO.search(text):
        return "no"
    if _CONSENT_YES.search(text):
        return "yes"
    return "unclear"


# The phone call asks only the condition questions; the generic intake fields
# stay in the submission contract as explicit nulls so main never infers them.
EMPTY_INTAKE: dict[str, object] = {
    "pain_scale": None,
    "fall_history": {"falls_last_6_months": None, "injured": None, "last_fall_description": None},
    "dizziness": None,
    "dizziness_notes": None,
    "primary_complaints": None,
}


class IntegratedSession:
    def __init__(
        self, service: IntegratedService, call: RegisteredCall, speak: Speaker,
        interpreter: AnswerInterpreter | None = None, end_playback: Callable[[], Awaitable[None]] | None = None,
        poll_seconds: float = 1, wait_seconds: float = 180, max_call_seconds: float = 600,
    ):
        patient = PatientRecord(call.patient_id, call.patient_id, ConditionCategory(call.condition_category))
        self.engine = SafeSurveyEngine(
            InMemoryPatientRepository({patient.patient_code: patient}), patient.patient_code, interpreter,
        )
        self.service = service
        self.call = call
        self.session_id = service.receipt(call.call_id).snapshot.phone_session_id or call.call_id
        self.speak = speak
        self.end_playback = end_playback
        self.poll_seconds = poll_seconds
        self.wait_seconds = wait_seconds
        self.max_call_seconds = max_call_seconds
        self.finished = False
        self.paused = False
        self.stage = "condition"
        self.link_open = False
        self._page_active = False
        self._buffer: list[str] = []
        self._confidence: float | None = None
        self._last_prompt = ""
        self._silences = 0
        self._link_reminders = 0
        self._consent_retries = 0
        self._background: asyncio.Task[None] | None = None
        self._deadline: asyncio.Task[None] | None = None
        self._speech_lock = asyncio.Lock()
        self.submission: dict[str, object] | None = None

    @property
    def pending_transcript(self) -> str:
        return " ".join(self._buffer).strip()

    def add_transcript(self, text: str, confidence: float | None = None) -> None:
        if len(self.pending_transcript) < 12000:
            self._buffer.append(text[:12000])
            self._confidence = lowest_confidence(self._confidence, confidence)

    def discard_pending(self) -> None:
        self._buffer.clear()
        self._confidence = None

    def trailing_off(self) -> bool:
        return bool(_TRAILING_FILLER.search(self.pending_transcript.lower()))

    async def begin(self) -> None:
        self._deadline = asyncio.create_task(self._expire())
        await self._say(policy.INTEGRATED_INTRO + policy.PARAGRAPH + _spoken(self.engine.start(greet=False)))

    async def _expire(self) -> None:
        await asyncio.sleep(self.max_call_seconds)
        await self.finish("completed", policy.INTEGRATED_CALL_LIMIT, "timeout")

    async def flush_utterance(self) -> bool:
        text = self.pending_transcript
        confidence = self._confidence
        self.discard_pending()
        if self.finished or not text:
            return self.finished
        self._silences = 0
        command = control_intent(text)
        if command == "stop" or (self.stage in {"submitting", "walking"} and link_reply_intent(text) == "stop"):
            await self.finish("stopped", policy.STOPPED, "stopped")
            return True
        if command == "pause":
            self.paused = True
            await self._say(policy.PAUSED, force=True)
            return False
        if self.paused:
            if command != "resume":
                return False
            self.paused = False
            await self._say(self._current_prompt())
            return False
        if command in {"repeat", "resume"}:
            await self._say(self._current_prompt())
            return False
        if self.stage == "condition":
            prompt, _ = await asyncio.to_thread(self.engine.handle_response, text, confidence)
            if self.engine.session.state in {"stopped", "escalated"}:
                await self.finish("stopped" if self.engine.session.state == "stopped" else "completed", _spoken(prompt), "needs_review")
                return True
            if self.engine.session.state == "complete":
                self.submission = self._payload()
                self.stage = "consent"
                await self._say(policy.INTEGRATED_GAIT_INTRO)
            else:
                await self._say(_spoken(prompt))
        elif self.stage == "consent":
            intent = consent_intent(text)
            if intent == "yes":
                self.stage = "submitting"
                await self._say(policy.INTEGRATED_CONSENT_GIVEN)
                self._background = asyncio.create_task(self._submit_and_wait())
            elif intent == "no" or self._consent_retries >= MAX_CONSENT_RETRIES:
                await self._decline_link()
            else:
                self._consent_retries += 1
                await self._say(policy.INTEGRATED_CONSENT_UNCLEAR)
        elif self.stage == "submitting":
            await self._say(policy.INTEGRATED_SAVING)
        else:
            intent = link_reply_intent(text)
            if intent == "missing" and not self.link_open:
                await self._say(policy.INTEGRATED_LINK_MISSING)
            elif intent == "ready" or clean_utterance(text) == "retry":
                self.link_open = True
                if self._page_active:
                    await self._say(policy.INTEGRATED_PAGE_SEEN)
                else:
                    await self._say(policy.INTEGRATED_CAMERA_SETUP)
            else:
                await self._say(self._current_prompt())
        return self.finished

    def _current_prompt(self) -> str:
        if self.stage == "condition":
            return _spoken(self.engine.start(greet=False))
        if self.stage == "consent":
            return policy.INTEGRATED_CONSENT_QUESTION
        if self.stage == "submitting":
            return policy.INTEGRATED_SAVING
        if not self.link_open:
            return policy.INTEGRATED_LINK_SENT
        return policy.INTEGRATED_WAITING

    def _payload(self) -> dict[str, object]:
        answers = self.engine.session.answers
        if self.engine.session.state != "complete" or not all(a.confirmed for a in answers):
            raise ValueError("Incomplete confirmed survey.")
        return {
            "patient_id": self.call.patient_id, "call_id": self.call.call_id,
            "submission_kind": "integrated",
            **copy.deepcopy(EMPTY_INTAKE),
            "condition_survey": {
                "instrument": "hoos_jr" if self.call.condition_category == "orthopedic" else "stroke_mobility",
                "version": "1", "condition_category": self.call.condition_category,
                "answers": [{
                    "question_id": a.question_id, "normalized_value": a.normalized_value,
                    "confirmed": a.confirmed, "acceptance_method": a.acceptance_method,
                    "confidence": a.confidence, "clarification_attempts": a.clarification_attempts,
                } for a in answers],
            },
        }

    async def _decline_link(self) -> None:
        """No spoken yes: store the confirmed answers, never text, and close."""
        assert self.submission is not None
        self.stage = "submitting"
        try:
            await self.service.submit(self.call.call_id, self.submission, send_link=False)
        except BackendError:
            await self.finish("completed", policy.INTEGRATED_SUBMIT_FAILED, "provider_unavailable")
            return
        await self.finish("completed", policy.INTEGRATED_CONSENT_DECLINED)

    async def _submit_and_wait(self) -> None:
        assert self.submission is not None
        try:
            snapshot = await self.service.submit(self.call.call_id, self.submission)
        except BackendError:
            await self.finish("completed", policy.INTEGRATED_SUBMIT_FAILED, "provider_unavailable")
            return
        if self.finished:
            return
        if snapshot.sms_status == "failed":
            await self.finish("completed", policy.INTEGRATED_SMS_FAILED, snapshot.error_code)
            return
        self.stage = "walking"
        await self._say(policy.INTEGRATED_LINK_SENT)
        await self._poll_walk()

    async def _poll_walk(self) -> None:
        deadline = asyncio.get_running_loop().time() + self.wait_seconds
        seen: tuple[str, int] | None = None
        while not self.finished and asyncio.get_running_loop().time() < deadline:
            if not self.paused:
                try:
                    view = await self.service.backend.walking(self.call.patient_id, self.call.call_id)
                except BackendError:
                    view = None
                if self.finished:
                    return
                if view is not None:
                    if view.call_id != self.call.call_id or view.attempt_id != self.call.attempt_id:
                        await self.finish("completed", policy.INTEGRATED_SCOPE_MISMATCH, "needs_review")
                        return
                    if self.paused:
                        continue
                    key = (view.status, view.last_sequence)
                    if view.status == "saved" and view.session_id:
                        await self.finish("completed", policy.INTEGRATED_SAVED)
                        return
                    if view.status == "stopped":
                        await self.finish("stopped", policy.INTEGRATED_PAGE_STOPPED, "stopped")
                        return
                    was_open = self.link_open
                    if view.status in _PAGE_OPEN_STATUSES or view.last_event == "permission_denied":
                        self.link_open = True
                    if view.status in _CAMERA_STATUSES or view.last_event == "permission_denied":
                        self._page_active = True
                    if key != seen:
                        seen = key
                        if view.last_event == "permission_denied":
                            await self._say(policy.INTEGRATED_PERMISSION_DENIED)
                        elif view.last_event == "recoverable_error":
                            await self._say(policy.INTEGRATED_PAGE_ERROR)
                        elif view.status == "page_ready" and not was_open:
                            self._page_active = True
                            await self._say(policy.INTEGRATED_PAGE_OPENED)
                        elif view.status == "ready":
                            await self._say(policy.INTEGRATED_READY)
                        elif view.status == "capturing":
                            await self._say(policy.INTEGRATED_CAPTURING)
                        elif view.status == "captured":
                            await self._say(policy.INTEGRATED_CAPTURED)
                if not self.link_open:
                    snapshot = self.service.receipt(self.call.call_id).snapshot
                    if snapshot.sms_status == "failed":
                        await self.finish("completed", policy.INTEGRATED_SMS_FAILED, snapshot.error_code)
                        return
            await asyncio.sleep(self.poll_seconds)
        if not self.finished:
            await self.finish("completed", policy.INTEGRATED_TIMED_OUT)

    async def handle_silence(self) -> bool:
        if self.stage == "submitting" or self.paused and self.stage == "walking":
            return self.finished
        if self.stage == "walking":
            # Quiet while they hunt for the text is not an unanswered question;
            # the page's status (or the poll deadline) decides how this ends.
            if not self.link_open and self._link_reminders < MAX_LINK_REMINDERS:
                self._link_reminders += 1
                await self._say(policy.INTEGRATED_LINK_REMINDER)
            return self.finished
        self._silences += 1
        if self._silences >= (12 if self.paused else 3):
            if self.stage == "consent":
                await self._decline_link()
            else:
                await self.finish("completed", policy.INTEGRATED_NO_RESPONSE, "needs_review")
        elif not self.paused:
            await self._say(self._current_prompt())
        return self.finished

    async def finish(self, status: CallStatus, text: str, error: PhoneError | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        current = asyncio.current_task()
        for task in (self._background, self._deadline):
            if task is not None and task is not current:
                task.cancel()
        await self.service.end(self.call.call_id, status, error)
        await self._say(text, closing=True)
        if self.end_playback is not None:
            await self.end_playback()

    async def disconnect(self) -> None:
        if not self.finished:
            self.finished = True
            for task in (self._background, self._deadline):
                if task is not None and task is not asyncio.current_task():
                    task.cancel()
            await self.service.end(self.call.call_id, "completed", "disconnected")

    async def _say(self, text: str, *, closing: bool = False, force: bool = False) -> None:
        async with self._speech_lock:
            if (self.finished or self.paused) and not closing and not force:
                return
            self._last_prompt = text
            await self.speak(text)

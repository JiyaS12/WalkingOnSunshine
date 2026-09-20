"""Combined survey and event-driven walking guidance, independent of audio transport."""

import asyncio
from collections.abc import Awaitable, Callable

from .answer_interpreter import AnswerInterpreter, clean_utterance, control_intent
from .generic_intake import GenericIntake
from .integrated_service import IntegratedService
from .integration_contract import CallStatus, PhoneError, RegisteredCall
from .main_backend import BackendError
from .models import ConditionCategory, PatientRecord
from .patient_repository import InMemoryPatientRepository
from .survey_engine import SafeSurveyEngine
from .telephony.call_session import _spoken, link_reply_intent

Speaker = Callable[[str], Awaitable[None]]


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
        self.generic = GenericIntake()
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
        self.stage = "generic"
        self._buffer: list[str] = []
        self._last_prompt = ""
        self._silences = 0
        self._background: asyncio.Task[None] | None = None
        self._deadline: asyncio.Task[None] | None = None
        self._speech_lock = asyncio.Lock()
        self.submission: dict[str, object] | None = None

    @property
    def pending_transcript(self) -> str:
        return " ".join(self._buffer).strip()

    def add_transcript(self, text: str) -> None:
        if len(self.pending_transcript) < 12000:
            self._buffer.append(text[:12000])

    def discard_pending(self) -> None:
        self._buffer.clear()

    async def begin(self) -> None:
        self._deadline = asyncio.create_task(self._expire())
        await self._say(
            "I am an automated survey helper. I will ask general intake questions, then your condition survey. "
            "You can say repeat, pause, resume, or stop. " + self.generic.prompt()
        )

    async def _expire(self) -> None:
        await asyncio.sleep(self.max_call_seconds)
        await self.finish("completed", "The call time limit has been reached. I cannot confirm a saved walking test.", "timeout")

    async def flush_utterance(self) -> bool:
        text = self.pending_transcript
        self.discard_pending()
        if self.finished or not text:
            return self.finished
        self._silences = 0
        command = control_intent(text)
        if command == "stop" or (self.stage in {"submitting", "walking"} and link_reply_intent(text) == "stop"):
            await self.finish("stopped", "We will stop here. Thank you for your time.", "stopped")
            return True
        if command == "pause":
            self.paused = True
            await self._say("Paused. Say resume or stop.")
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
        if self.stage == "generic":
            prompt = self.generic.handle(text)
            if self.generic.state in {"stopped", "needs_review"}:
                await self.finish("stopped" if self.generic.state == "stopped" else "completed", prompt, "needs_review")
                return True
            if self.generic.complete:
                self.stage = "condition"
                prompt = _spoken(self.engine.start())
            await self._say(prompt)
        elif self.stage == "condition":
            prompt, _ = await asyncio.to_thread(self.engine.handle_response, text)
            if self.engine.session.state in {"stopped", "escalated"}:
                await self.finish("stopped" if self.engine.session.state == "stopped" else "completed", _spoken(prompt), "needs_review")
                return True
            if self.engine.session.state == "complete":
                self.submission = self._payload()
                self.stage = "submitting"
                await self._say("Your answers are confirmed. I am checking that they are saved before requesting a text.")
                self._background = asyncio.create_task(self._submit_and_wait())
            else:
                await self._say(_spoken(prompt))
        else:
            if link_reply_intent(text) == "missing":
                await self._say("I cannot confirm the text reached you. I will not automatically send another. You can stop or continue waiting.")
            elif link_reply_intent(text) == "ready" or clean_utterance(text) == "retry":
                await self._say("Follow the page's camera and calibration steps. I will wait for its readiness confirmation.")
            else:
                await self._say("I am waiting for the page's status. You can say pause, repeat, or stop.")
        return self.finished

    def _current_prompt(self) -> str:
        if self.stage == "generic":
            return self.generic.prompt()
        if self.stage == "condition":
            return _spoken(self.engine.start())
        return "I am waiting for confirmation from the walking page. Say stop to end."

    def _payload(self) -> dict[str, object]:
        answers = self.engine.session.answers
        if not self.generic.complete or self.engine.session.state != "complete" or not all(a.confirmed for a in answers):
            raise ValueError("Incomplete confirmed survey.")
        return {
            "patient_id": self.call.patient_id, "call_id": self.call.call_id,
            "submission_kind": "integrated",
            **self.generic.payload(),
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

    async def _submit_and_wait(self) -> None:
        assert self.submission is not None
        try:
            snapshot = await self.service.submit(self.call.call_id, self.submission)
        except BackendError:
            await self.finish("completed", "I could not confirm saving the intake and requesting its link. No walking test has been confirmed saved.", "provider_unavailable")
            return
        if self.finished:
            return
        if snapshot.sms_status == "failed":
            await self.finish("completed", "Your intake is saved, but the text failed. Please contact your care team if you need help with the link.")
            return
        self.stage = "walking"
        await self._say(
            "Your intake is saved. The text request is recorded, but delivery may still be pending. "
            "When the link arrives, open it and follow the camera setup. I will wait for calibration."
        )
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
                        await self.finish("completed", "The walking page status does not match this call. I will stop guidance.", "needs_review")
                        return
                    if self.paused:
                        continue
                    key = (view.status, view.last_sequence)
                    if view.status == "saved" and view.session_id:
                        await self.finish("completed", "The backend confirms your walking test is saved. Thank you. Goodbye.")
                        return
                    if view.status == "stopped":
                        await self.finish("stopped", "The walking page has stopped the test. We will stop here.", "stopped")
                        return
                    if key != seen:
                        seen = key
                        if view.last_event == "permission_denied":
                            await self._say("The page reports camera permission was denied. You can allow camera access and retry on the page, or say stop.")
                        elif view.last_event == "recoverable_error":
                            await self._say("The page reports a problem. Please follow its retry instructions, or say stop. A saved test is not confirmed.")
                        elif view.status == "ready":
                            await self._say("The page confirms calibration is ready. When safe, start capture on the page and walk in view of the camera. About 15 seconds is a guide; I will wait for the saved result.")
                        elif view.status == "capturing":
                            await self._say("The page reports capture is running. Stop if you feel unsafe. I will wait for the saved result.")
                        elif view.status == "captured":
                            await self._say("Capture has finished. I am waiting for confirmation that the result is saved.")
            await asyncio.sleep(self.poll_seconds)
        if not self.finished:
            await self.finish("completed", "The waiting time has ended. I cannot confirm that a walking test was saved. You may check the page.")

    async def handle_silence(self) -> bool:
        if self.stage in {"submitting", "walking"}:
            return self.finished
        self._silences += 1
        if self._silences >= (12 if self.paused else 3):
            await self.finish("completed", "I did not hear a confirmed response. We will stop without submitting an incomplete intake.", "needs_review")
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

    async def _say(self, text: str, *, closing: bool = False) -> None:
        async with self._speech_lock:
            if (self.finished or self.paused) and not closing and not text.startswith("Paused"):
                return
            self._last_prompt = text
            await self.speak(text)

from __future__ import annotations

from . import conversation_policy as speech
from .answer_interpreter import (
    AnswerInterpreter, ExactAnswerInterpreter, Interpretation,
    MAX_TRANSCRIPT_CHARS, clean_utterance, control_intent, validated_interpretation,
)
from .models import SurveyAnswer, SurveySession
from .question_loader import load_question_bank

YES = {"yes", "yeah", "yep", "correct", "that's correct", "that's right",
       "yes that's correct", "yes that's right", "yes it is", "yes i agree",
       "i agree", "that sounds right", "yes that sounds right", "yes exactly"}
NO = {"no", "nope", "not correct", "that's not correct", "that's not right",
      "no not really", "no that's not right", "no it isn't", "i disagree"}
TERMINAL_SPEECH = {"complete": speech.COMPLETE, "escalated": speech.REVIEW, "stopped": speech.STOPPED}
MAX_CLARIFICATIONS = 3
# Speech recognition confidence below which a bare option label ("mild") is
# read back for confirmation instead of accepted outright. A phone line can
# turn "mild" into "mile"; the recognizer says how sure it was.
DEFAULT_CONFIRM_BELOW_CONFIDENCE = 0.7


class SafeSurveyEngine:
    """The engine owns questions, acceptance and progression; bridges are bounded."""

    def __init__(
        self, repository, patient_code: str, interpreter: AnswerInterpreter | None = None,
        confirm_below_confidence: float = DEFAULT_CONFIRM_BELOW_CONFIDENCE,
    ):
        self.repository = repository
        self.patient = repository.lookup_patient(patient_code)
        self.interpreter = interpreter or ExactAnswerInterpreter()
        self.confirm_below_confidence = confirm_below_confidence
        self.session = SurveySession(
            patient=self.patient,
            questions=load_question_bank(self.patient.condition_category),
        )

    def _clarification(self, question, acknowledgment=None) -> str:
        """Re-ask a question, listing the scale only when the caller needs it.

        The scale is read out with the first question, so repeating it on every
        stumble makes a phone call tedious; a second miss on the same question
        means the caller probably does need to hear it again.
        """

        return speech.clarification_text(
            question,
            acknowledgment,
            include_options=self.session.clarification_attempts >= 1,
            attempt=self.session.clarification_attempts,
        )

    def _question_text(self) -> str:
        return speech.question_text(
            self.session.current_question, self.session.current_index, len(self.session.questions),
        )

    def start(self, *, greet: bool = True) -> str:
        if self.session.state == "complete" and self.session.needs_human_review:
            return speech.COMPLETE_WITH_REVIEW
        if self.session.state in TERMINAL_SPEECH:
            return TERMINAL_SPEECH[self.session.state]
        if self.session.state == "paused":
            return speech.PAUSED
        if self.session.state != "awaiting_start":
            return self.session.last_confirmation_prompt or self._question_text()
        if self.session.current_question is None:
            self.session.state = "complete"
            return speech.COMPLETE
        self.session.state = "asking"
        if not greet:
            return speech.first_question_text(self.session.current_question, len(self.session.questions))
        return speech.opening_text(self.session.current_question, len(self.session.questions))

    def _retry(self, prompt: str) -> tuple[str, None]:
        self.session.clarification_attempts += 1
        if self.session.clarification_attempts >= MAX_CLARIFICATIONS:
            return self.skip_unresolved(), None
        return prompt, None

    def skip_unresolved(self, reason: str = "clarification_limit") -> str:
        """Advance without inventing an answer or terminating the survey."""
        question = self.session.current_question
        if question is None or self.session.state in TERMINAL_SPEECH:
            return self.start()
        self.session.needs_human_review = True
        self.session.unanswered_questions.append({
            "question_id": question.id, "reason": reason,
            "clarification_attempts": self.session.clarification_attempts,
        })
        self._clear_pending()
        self.session.current_index += 1
        self.session.clarification_attempts = 0
        self.session.state = "complete" if self.session.is_complete else "asking"
        if self.session.is_complete:
            return speech.SKIP_FOR_REVIEW + " " + speech.COMPLETE_WITH_REVIEW
        return f"{speech.SKIP_FOR_REVIEW} Let’s move to the next question. {self._question_text()}"

    def _clear_pending(self) -> None:
        self.session.pending_answer = None
        self.session.last_confirmation_prompt = None

    def _interpret(self, transcript: str) -> Interpretation:
        if not transcript.strip() or len(transcript) > MAX_TRANSCRIPT_CHARS:
            return Interpretation()
        pending = self.session.pending_answer
        try:
            result = self.interpreter.interpret(
                transcript, self.session.current_question,
                pending.normalized_value if pending else None,
            )
            return validated_interpretation(
                result, transcript, self.session.current_question,
                pending.normalized_value if pending else None,
            )
        except Exception:
            return Interpretation()

    def _accept_pending(self, acknowledgment=None, *, method="confirmation") -> tuple[str, SurveyAnswer]:
        answer = self.session.pending_answer
        answer.confirmed = True
        answer.acceptance_method = method
        answer.clarification_attempts = self.session.clarification_attempts
        self.session.answers.append(answer)
        self._clear_pending()
        self.session.current_index += 1
        self.session.clarification_attempts = 0
        self.session.state = "asking"
        if self.session.is_complete:
            self.session.state = "complete"
            return (speech.COMPLETE_WITH_REVIEW if self.session.needs_human_review else speech.COMPLETE), answer
        bridge = speech.validated_bridge(acknowledgment) or speech.accepted_bridge(
            answer.normalized_value, self.session.current_index
        )
        return f"{bridge} {self._question_text()}", answer

    def handle_response(
        self, transcript: str, confidence: float | None = None,
    ) -> tuple[str, SurveyAnswer | None]:
        """Advance the survey with what the patient said.

        ``confidence`` is the speech recognizer's confidence in ``transcript``
        (0-1), or None when unknown. It only affects a bare option label heard
        with low confidence, which is read back rather than accepted.
        """

        if self.session.state in TERMINAL_SPEECH:
            return self.start(), None
        if self.session.state == "awaiting_start":
            return self.start(), None

        question = self.session.current_question
        command = control_intent(transcript)
        normalized = clean_utterance(transcript)

        # Short explicit confirmations stay fast/offline. Longer responses are
        # classified in context below; the engine still owns accepting the value.
        if self.session.state == "awaiting_confirmation" and command is None:
            if normalized in YES:
                return self._accept_pending()
            if normalized in NO:
                self._clear_pending()
                self.session.state = "asking"
                return self._retry(f"Thanks for correcting me. {self._clarification(question)}")

        result = Interpretation(command) if command else self._interpret(transcript)
        if result.intent == "stop":
            self._clear_pending()
            self.session.state = "stopped"
            return speech.STOPPED, None
        if result.intent == "pause":
            self.session.state = "paused"
            return speech.PAUSED, None
        if self.session.state == "paused":
            if result.intent != "resume":
                return speech.PAUSED, None
            self.session.state = "awaiting_confirmation" if self.session.pending_answer else "asking"
            return self.session.last_confirmation_prompt or self._question_text(), None
        if result.intent == "confirm" and self.session.state == "awaiting_confirmation":
            return self._accept_pending(result.acknowledgment)
        if result.intent in {"repeat", "resume"}:
            repeated = f"Of course. {question.prompt} {speech.options_text(question)}"
            if self.session.pending_answer:
                repeated += f" {self.session.last_confirmation_prompt}"
            return repeated, None
        if result.intent == "medical_question":
            return self._retry(f"{speech.MEDICAL_BOUNDARY} {self.session.last_confirmation_prompt or self._question_text()}")
        if result.intent == "reject":
            self._clear_pending()
            self.session.state = "asking"
            return self._retry(f"Thanks for correcting me. {self._clarification(question)}")

        if result.intent == "select" and self._heard_clearly(confidence):
            # Naming an option is already the patient's decision, including a
            # clear correction to a pending proposal. No extra yes/no needed.
            self.session.pending_answer = SurveyAnswer(
                question_id=question.id, question_prompt=question.prompt,
                normalized_value=result.value, raw_response=transcript,
                confidence=confidence, clarification_attempts=self.session.clarification_attempts,
            )
            return self._accept_pending(result.acknowledgment, method="explicit_selection")
        misheard = result.intent == "select"
        if misheard:
            # The label was recognized, but the recognizer was not sure it heard
            # it right: propose it and let the patient confirm below.
            result = Interpretation("answer", result.value, result.evidence, result.acknowledgment)

        adjustment = result.intent in {"adjust_down", "adjust_up"}
        if adjustment:
            # Validation requires an existing proposal and the known ordered
            # scale. The LLM chooses direction, never an arbitrary new label.
            index = question.answer_options.index(self.session.pending_answer.normalized_value)
            value = question.answer_options[index + (-1 if result.intent == "adjust_down" else 1)]
        else:
            value = result.value

        if result.intent != "answer" and not adjustment:
            if self.session.pending_answer:
                prompt = self.session.last_confirmation_prompt
            else:
                self.session.state = "clarifying"
                prefix = "Thank you for sharing. Let’s come back to this question. " if result.intent == "off_topic" else ""
                prompt = prefix + self._clarification(question, result.acknowledgment)
            return self._retry(prompt)

        correction = self.session.pending_answer is not None
        if correction:
            # A correction is a new proposal, never an implicit confirmation.
            self._clear_pending()
            self.session.state = "asking"
            self.session.clarification_attempts += 1
            if self.session.clarification_attempts >= MAX_CLARIFICATIONS:
                return self.skip_unresolved(), None
        answer = SurveyAnswer(
            question_id=question.id,
            question_prompt=question.prompt,
            normalized_value=value,
            raw_response=transcript,
            # Recognizer confidence only; never a fabricated LLM probability.
            confidence=confidence if misheard else None,
            confirmed=False,
            clarification_attempts=self.session.clarification_attempts,
        )
        self.session.pending_answer = answer
        self.session.state = "awaiting_confirmation"
        self.session.last_confirmation_prompt = (
            speech.readback_text(question, value) if misheard
            else speech.confirmation_text(question, value, result.acknowledgment, correction=correction)
        )
        return self.session.last_confirmation_prompt, answer

    def _heard_clearly(self, confidence: float | None) -> bool:
        return confidence is None or confidence >= self.confirm_below_confidence

    def snapshot(self) -> dict[str, object]:
        pending = self.session.pending_answer
        return {
            "patient_code": self.patient.patient_code,
            "condition_category": self.patient.condition_category.value,
            "state": self.session.state,
            "current_index": self.session.current_index,
            "current_question": self.session.current_question.id if self.session.current_question else None,
            "answers": [
                {
                    "question_id": answer.question_id,
                    "normalized_value": answer.normalized_value,
                    "confirmed": answer.confirmed,
                    "acceptance_method": answer.acceptance_method,
                }
                for answer in self.session.answers
            ],
            "pending_answer": {
                "question_id": pending.question_id, "normalized_value": pending.normalized_value,
                "confirmed": False,
            } if pending else None,
            "skipped": list(self.session.skipped),
            "question_count": len(self.session.questions),
            "clarification_attempts": self.session.clarification_attempts,
            "needs_human_review": self.session.needs_human_review,
            "unanswered_questions": [dict(item) for item in self.session.unanswered_questions],
        }

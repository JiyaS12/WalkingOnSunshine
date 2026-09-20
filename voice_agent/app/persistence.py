from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Protocol

from .models import PatientRecord, SurveyAnswer
from .scoring import DEFAULT_LIKERT_SCORING, score_confirmed_responses

logger = logging.getLogger(__name__)

TERMINAL_STATUS = {
    "complete": "completed",
    "escalated": "needs_review",
    "stopped": "needs_review",
}


@dataclass
class TranscriptTurn:
    speaker: str
    text: str
    question_key: str | None = None


@dataclass
class CallRecord:
    session_id: str
    patient_code: str
    condition_category: str
    transcript: list[str] = field(default_factory=list)
    status: str = "in_progress"
    answers: list[dict[str, object]] = field(default_factory=list)
    final_status: str | None = None
    call_sid: str | None = None
    to_number: str | None = None
    carrier_status: str | None = None
    patient_record_id: str | None = None
    turns: list[TranscriptTurn] = field(default_factory=list)


class ConversationPersistence(Protocol):
    def persist_session_start(self, session_id: str, patient: PatientRecord, assistant_prompt: str) -> None: ...
    def persist_turn(
        self,
        session_id: str,
        patient_text: str,
        assistant_prompt: str,
        answer: SurveyAnswer | None,
        snapshot: dict[str, object],
    ) -> None: ...
    def list_results(self, patient_code: str | None = None) -> list[dict[str, object]]: ...


class InMemoryPersistence:
    """Persistence bridge for the safe runtime; replaceable with Supabase later."""

    def __init__(self):
        self.calls: dict[str, CallRecord] = {}

    def start_call(self, session_id: str, patient_code: str, condition_category: str) -> CallRecord:
        existing = self.calls.get(session_id)
        if existing is not None:
            return existing
        record = CallRecord(
            session_id=session_id,
            patient_code=patient_code,
            condition_category=condition_category,
        )
        self.calls[session_id] = record
        return record

    def append_transcript(self, session_id: str, text: str) -> None:
        record = self.calls.get(session_id)
        if record is not None:
            record.transcript.append(text)

    def record_answer(self, session_id: str, answer: dict[str, object]) -> None:
        record = self.calls.get(session_id)
        if record is not None:
            record.answers.append(answer)

    def complete_call(self, session_id: str, final_status: str = "completed") -> CallRecord:
        record = self.calls[session_id]
        # ``status`` is the call lifecycle; ``final_status`` is the outcome.
        record.status = "completed"
        record.final_status = final_status
        return record

    def persist_session_start(self, session_id: str, patient: PatientRecord, assistant_prompt: str) -> None:
        record = self.start_call(session_id, patient.patient_code, patient.condition_category.value)
        record.patient_record_id = patient.patient_id
        self._append_turn(session_id, "assistant", assistant_prompt, None)

    def persist_turn(
        self,
        session_id: str,
        patient_text: str,
        assistant_prompt: str,
        answer: SurveyAnswer | None,
        snapshot: dict[str, object],
    ) -> None:
        question_key = answer.question_id if answer is not None else snapshot.get("current_question")
        key = question_key if isinstance(question_key, str) else None
        self.append_transcript(session_id, patient_text)
        self._append_turn(session_id, "patient", patient_text, key)
        if assistant_prompt:
            self._append_turn(session_id, "assistant", assistant_prompt, key)
        if answer is not None:
            self.record_answer(session_id, {
                "question_id": answer.question_id,
                "value": answer.normalized_value,
                "confirmed": answer.confirmed,
                "raw_response": answer.raw_response,
            })
        engine_state = str(snapshot.get("state") or "")
        if engine_state in TERMINAL_STATUS:
            self.complete_call(session_id, TERMINAL_STATUS[engine_state])

    def list_results(self, patient_code: str | None = None) -> list[dict[str, object]]:
        records = self.calls.values()
        if patient_code:
            wanted = patient_code.strip()
            records = [record for record in records if record.patient_code == wanted]
        results = []
        for record in records:
            confirmed = [row for row in record.answers if row.get("confirmed")]
            # Last confirmed row per question is the source of truth.
            by_question: dict[str, dict[str, object]] = {}
            for row in confirmed:
                by_question[str(row["question_id"])] = row
            question_rows = [
                {"question_key": key, "scoring_map": DEFAULT_LIKERT_SCORING, "required": True}
                for key in by_question
            ]
            response_rows = [
                {
                    "question_key": key,
                    "confirmation_status": "confirmed",
                    "confirmed_value": row.get("value"),
                }
                for key, row in by_question.items()
            ]
            score = score_confirmed_responses(question_rows, response_rows) if question_rows else None
            completed = (record.final_status or record.status) == "completed"
            results.append({
                "patient_id": record.patient_code,
                "patient_uuid": record.patient_record_id,
                "session_id": record.session_id,
                "status": record.final_status or record.status,
                "total_score": str(score.total) if completed and score and score.complete else None,
                "transcript": "\n".join(f"{turn.speaker}: {turn.text}" for turn in record.turns),
                "survey_results": [
                    {
                        "question_key": row["question_id"],
                        "confirmed_value": row.get("value"),
                        "confirmation_status": "confirmed" if row.get("confirmed") else "pending",
                        "raw_patient_text": row.get("raw_response"),
                    }
                    for row in record.answers
                ],
            })
        return results

    def _append_turn(self, session_id: str, speaker: str, text: str, question_key: str | None) -> None:
        record = self.calls.get(session_id)
        if record is not None:
            record.turns.append(TranscriptTurn(speaker, text, question_key))


class CompositePersistence:
    """Always keep an in-memory copy; also write through to the database when configured."""

    def __init__(self, memory: InMemoryPersistence, database: ConversationPersistence):
        self.memory = memory
        self.database = database
        self.calls = memory.calls
        # Sessions with at least one failed database write; the database copy of
        # these is incomplete, so the in-memory copy is the one operators see.
        self.degraded: set[str] = set()

    def persist_session_start(self, session_id: str, patient: PatientRecord, assistant_prompt: str) -> None:
        self.memory.persist_session_start(session_id, patient, assistant_prompt)
        try:
            self.database.persist_session_start(session_id, patient, assistant_prompt)
        except Exception:
            self.degraded.add(session_id)
            logger.exception("Database persistence failed while starting session %s", session_id)

    def persist_turn(
        self,
        session_id: str,
        patient_text: str,
        assistant_prompt: str,
        answer: SurveyAnswer | None,
        snapshot: dict[str, object],
    ) -> None:
        self.memory.persist_turn(session_id, patient_text, assistant_prompt, answer, snapshot)
        try:
            self.database.persist_turn(session_id, patient_text, assistant_prompt, answer, snapshot)
        except Exception:
            self.degraded.add(session_id)
            logger.exception("Database persistence failed for session %s", session_id)

    def list_results(self, patient_code: str | None = None) -> list[dict[str, object]]:
        local = self.memory.list_results(patient_code)
        try:
            rows = self.database.list_results(patient_code)
        except Exception:
            logger.exception("Database result read failed; returning in-memory transcript results.")
            return local

        def session_of(row: dict[str, object]) -> str:
            return str(row.get("follow_up_label") or "").removeprefix("live:")

        trusted = [row for row in rows if session_of(row) not in self.degraded]
        stored = {session_of(row) for row in trusted}
        return trusted + [row for row in local if row["session_id"] not in stored]


def build_persistence() -> ConversationPersistence:
    """Use Supabase when server-side credentials exist, except under pytest."""
    from .database import DatabaseConversationStore

    memory = InMemoryPersistence()
    if os.getenv("PYTEST_CURRENT_TEST"):
        return memory
    database = DatabaseConversationStore.from_env()
    if database is None:
        return memory
    logger.info("Conversation persistence: Supabase (transcripts and confirmed survey results).")
    return CompositePersistence(memory, database)

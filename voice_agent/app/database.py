"""Server-side Supabase REST client for conversation persistence.

Uses the service-role key on the FastAPI process only. Never import this from
frontend code. Does not store raw audio.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from .models import PatientRecord, SurveyAnswer

TERMINAL_STATUS = {
    "complete": "completed",
    "escalated": "needs_review",
    "stopped": "needs_review",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupabaseRest:
    """Thin PostgREST helper. The service-role key stays in process env."""

    def __init__(self, url: str, service_role_key: str, client: httpx.Client | None = None):
        base = url.rstrip("/") + "/rest/v1"
        headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._client = client or httpx.Client(base_url=base, headers=headers, timeout=20.0)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def get(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = self._client.get(f"/{table}", params=params)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else [payload]

    def post(
        self,
        table: str,
        json: dict[str, Any] | list[dict[str, Any]],
        *,
        merge: bool = False,
        on_conflict: str | None = None,
    ) -> list[dict[str, Any]]:
        headers = {"Prefer": "return=representation"}
        params: dict[str, str] = {}
        if merge:
            headers["Prefer"] = "return=representation,resolution=merge-duplicates"
            if on_conflict:
                params["on_conflict"] = on_conflict
        response = self._client.post(f"/{table}", params=params or None, json=json, headers=headers)
        response.raise_for_status()
        if not response.content:
            return []
        payload = response.json()
        return payload if isinstance(payload, list) else [payload]

    def patch(self, table: str, params: dict[str, str], json: dict[str, Any]) -> list[dict[str, Any]]:
        headers = {"Prefer": "return=representation"}
        response = self._client.patch(f"/{table}", params=params, json=json, headers=headers)
        response.raise_for_status()
        if not response.content:
            return []
        payload = response.json()
        return payload if isinstance(payload, list) else [payload]


class DatabaseConversationStore:
    """Writes patient id, transcript turns, and survey results to Supabase."""

    def __init__(self, rest: SupabaseRest):
        self.rest = rest
        self._sessions: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_env(cls) -> DatabaseConversationStore | None:
        if os.getenv("CONVERSATION_STORE", "").strip().lower() == "in_memory":
            return None
        url = (os.getenv("SUPABASE_URL") or "").strip()
        key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if not url or not key:
            return None
        return cls(SupabaseRest(url, key))

    def persist_session_start(self, session_id: str, patient: PatientRecord, assistant_prompt: str) -> None:
        patient_row = self._ensure_patient(patient)
        template = self._template_for(patient.condition_category.value)
        questions = self.rest.get(
            "survey_questions",
            {
                "survey_template_id": f"eq.{template['id']}",
                "select": "id,question_key,question_order,scoring_map,required,question_text",
                "order": "question_order.asc",
            },
        )
        if not questions:
            raise RuntimeError(f"No survey questions found for template {template['id']}.")
        instance = self.rest.post("survey_instances", {
            "patient_id": patient_row["id"],
            "survey_template_id": template["id"],
            "follow_up_label": f"live:{session_id}",
            "status": "in_progress",
            "started_at": utc_now(),
        })[0]
        call = self.rest.post("call_sessions", {
            "survey_instance_id": instance["id"],
            "external_call_id": session_id,
            "provider": "desktop_demo",
            "status": "in_progress",
            "started_at": utc_now(),
        })[0]
        self._sessions[session_id] = {
            "patient_id": patient_row["id"],
            "patient_code": patient.patient_code,
            "survey_instance_id": instance["id"],
            "call_session_id": call["id"],
            "questions": {row["question_key"]: row for row in questions},
            "turn_index": 0,
            "response_ids": {},
        }
        self._append_turn(session_id, "assistant", assistant_prompt, None)
        self.rest.post("audit_events", {
            "entity_type": "survey_instance",
            "entity_id": instance["id"],
            "action": "survey_started",
            "actor_type": "system",
            "metadata": {"patient_id": patient.patient_code, "session_id": session_id},
        })

    def persist_turn(
        self,
        session_id: str,
        patient_text: str,
        assistant_prompt: str,
        answer: SurveyAnswer | None,
        snapshot: dict[str, object],
    ) -> None:
        if session_id not in self._sessions:
            raise RuntimeError(f"Unknown conversation session '{session_id}'.")
        question_key = answer.question_id if answer is not None else snapshot.get("current_question")
        key = question_key if isinstance(question_key, str) else None
        self._append_turn(session_id, "patient", patient_text, key)
        if assistant_prompt:
            self._append_turn(session_id, "assistant", assistant_prompt, key)
        if answer is not None:
            self._upsert_response(session_id, answer, patient_text)
        engine_state = str(snapshot.get("state") or "")
        if engine_state in TERMINAL_STATUS:
            self._complete(session_id, TERMINAL_STATUS[engine_state], engine_state)

    def list_results(self, patient_code: str | None = None) -> list[dict[str, object]]:
        params = {
            "select": (
                "patient_uuid,patient_id,survey_instance_id,call_session_id,"
                "follow_up_label,survey_status,total_score,started_at,completed_at,transcript,survey_results"
            ),
            "order": "completed_at.desc.nullslast",
        }
        if patient_code:
            params["patient_id"] = f"eq.{patient_code.strip()}"
        rows = self.rest.get("patient_conversation_results", params)
        return [
            {
                "patient_id": row.get("patient_id"),
                "patient_uuid": row.get("patient_uuid"),
                "survey_instance_id": row.get("survey_instance_id"),
                "call_session_id": row.get("call_session_id"),
                "follow_up_label": row.get("follow_up_label"),
                "status": row.get("survey_status"),
                "total_score": row.get("total_score"),
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
                "transcript": row.get("transcript") or "",
                "survey_results": row.get("survey_results") or [],
            }
            for row in rows
        ]

    def _ensure_patient(self, patient: PatientRecord) -> dict[str, Any]:
        existing = self.rest.get(
            "patients",
            {"display_id": f"eq.{patient.patient_code}", "select": "id,display_id,condition_category"},
        )
        if existing:
            return existing[0]
        created = self.rest.post("patients", {
            "display_id": patient.patient_code,
            "condition_category": patient.condition_category.value,
            "is_synthetic": True,
        })
        if not created:
            raise RuntimeError(f"Could not create synthetic patient {patient.patient_code}.")
        return created[0]

    def _template_for(self, condition_category: str) -> dict[str, Any]:
        rows = self.rest.get(
            "survey_templates",
            {
                "condition_category": f"eq.{condition_category}",
                "is_active": "eq.true",
                "select": "id,name,condition_category",
            },
        )
        if not rows:
            raise RuntimeError(f"No active survey template for condition_category={condition_category}.")
        preferred = "HOOS JR" if condition_category == "orthopedic" else "Stroke function subset"
        for row in rows:
            if row.get("name") == preferred:
                return row
        return rows[0]

    def _append_turn(self, session_id: str, speaker: str, text: str, question_key: str | None) -> None:
        state = self._sessions[session_id]
        state["turn_index"] += 1
        self.rest.post("conversation_turns", {
            "patient_id": state["patient_id"],
            "survey_instance_id": state["survey_instance_id"],
            "call_session_id": state["call_session_id"],
            "turn_index": state["turn_index"],
            "speaker": speaker,
            "text": text,
            "question_key": question_key,
        })

    def _upsert_response(self, session_id: str, answer: SurveyAnswer, patient_text: str) -> None:
        state = self._sessions[session_id]
        question = state["questions"].get(answer.question_id)
        if question is None:
            raise RuntimeError(f"Unknown question_key '{answer.question_id}' for this survey.")
        payload: dict[str, Any] = {
            "survey_instance_id": state["survey_instance_id"],
            "survey_question_id": question["id"],
            "call_session_id": state["call_session_id"],
            "raw_patient_text": answer.raw_response or patient_text,
            "ai_proposed_value": answer.normalized_value,
            "evidence_text": answer.raw_response,
        }
        if answer.confirmed:
            payload["confirmation_status"] = "confirmed"
            payload["confirmed_value"] = answer.normalized_value
            payload["confirmation_text"] = patient_text
        else:
            payload["confirmation_status"] = "pending"
            payload["confirmed_value"] = None
        existing_id = state["response_ids"].get(answer.question_id)
        if existing_id:
            rows = self.rest.patch("survey_responses", {"id": f"eq.{existing_id}"}, payload)
        else:
            rows = self.rest.post(
                "survey_responses",
                payload,
                merge=True,
                on_conflict="survey_instance_id,survey_question_id",
            )
        if rows:
            state["response_ids"][answer.question_id] = rows[0]["id"]
        entity_id = state["response_ids"].get(answer.question_id) or str(uuid4())
        self.rest.post("audit_events", {
            "entity_type": "survey_response",
            "entity_id": entity_id,
            "action": "answer_confirmed" if answer.confirmed else "answer_proposed",
            "actor_type": "patient" if answer.confirmed else "ai",
            "metadata": {
                "question_key": answer.question_id,
                "value": answer.normalized_value,
                "confirmed": answer.confirmed,
            },
        })

    def _complete(self, session_id: str, status: str, engine_state: str) -> None:
        state = self._sessions[session_id]
        instance_payload: dict[str, Any] = {"status": status}
        call_payload: dict[str, Any] = {"status": "completed" if status == "completed" else "failed"}
        if status in {"completed", "needs_review"}:
            ended = utc_now()
            if status == "completed":
                instance_payload["completed_at"] = ended
            call_payload["ended_at"] = ended
        self.rest.patch("survey_instances", {"id": f"eq.{state['survey_instance_id']}"}, instance_payload)
        self.rest.patch("call_sessions", {"id": f"eq.{state['call_session_id']}"}, call_payload)
        if engine_state == "escalated":
            self.rest.post("review_flags", {
                "survey_instance_id": state["survey_instance_id"],
                "call_session_id": state["call_session_id"],
                "flag_type": "user_requested_human",
                "severity": "medium",
                "reason": "Survey engine escalated for human review after repeated clarification failures.",
            })
        action = {
            "complete": "survey_completed",
            "escalated": "review_flag_created",
            "stopped": "survey_completed",
        }[engine_state]
        self.rest.post("audit_events", {
            "entity_type": "survey_instance",
            "entity_id": state["survey_instance_id"],
            "action": action,
            "actor_type": "system",
            "metadata": {"status": status, "engine_state": engine_state, "session_id": session_id},
        })

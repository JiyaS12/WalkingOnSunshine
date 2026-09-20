from uuid import uuid4

from app.database import DatabaseConversationStore
from app.models import ConditionCategory, PatientRecord, SurveyAnswer
from app.patient_repository import InMemoryPatientRepository
from app.persistence import InMemoryPersistence
from app.question_loader import HOOS_JR_QUESTIONS
from app.survey_engine import SafeSurveyEngine


class FakeRest:
    def __init__(self):
        self.tables = {
            "patients": [
                {"id": "pat-417", "display_id": "RGN-0417", "condition_category": "orthopedic"},
            ],
            "survey_templates": [
                {"id": "tmpl-hoos", "name": "HOOS JR", "condition_category": "orthopedic", "is_active": True},
            ],
            "survey_questions": [
                {
                    "id": f"q-{question.id}",
                    "question_key": question.id,
                    "question_order": index + 1,
                    "scoring_map": {"none": 0, "mild": 1, "moderate": 2, "severe": 3, "extreme": 4},
                    "required": True,
                    "question_text": question.prompt,
                }
                for index, question in enumerate(HOOS_JR_QUESTIONS)
            ],
            "survey_instances": [],
            "call_sessions": [],
            "conversation_turns": [],
            "survey_responses": [],
            "audit_events": [],
            "review_flags": [],
            "patient_conversation_results": [],
        }

    def get(self, table, params):
        rows = list(self.tables.get(table, []))
        for key, value in params.items():
            if key in {"select", "order"}:
                continue
            if isinstance(value, str) and value.startswith("eq."):
                wanted = value[3:]
                rows = [row for row in rows if key not in row or self._eq(row.get(key), wanted)]
        return rows

    @staticmethod
    def _eq(actual, wanted: str) -> bool:
        if isinstance(actual, bool):
            return wanted.lower() == ("true" if actual else "false")
        return str(actual) == wanted

    def post(self, table, json, merge=False, on_conflict=None):
        rows = json if isinstance(json, list) else [json]
        stored = []
        for row in rows:
            item = dict(row)
            item.setdefault("id", str(uuid4()))
            self.tables.setdefault(table, []).append(item)
            stored.append(item)
        return stored

    def patch(self, table, params, json):
        updated = []
        for key, value in params.items():
            wanted = value[3:] if isinstance(value, str) and value.startswith("eq.") else value
            for row in self.tables.get(table, []):
                if str(row.get(key)) == wanted:
                    row.update(json)
                    updated.append(row)
        return updated


def test_in_memory_results_include_patient_id_transcript_and_answers():
    repo = InMemoryPersistence()
    patient = InMemoryPatientRepository().lookup_patient("RGN-0417")
    repo.persist_session_start("sess-1", patient, "Hello, this is the automated survey helper.")
    answer = SurveyAnswer(
        question_id="hoos_stairs",
        question_prompt="stairs",
        normalized_value="mild",
        raw_response="it was mild",
        confidence=None,
        confirmed=True,
    )
    repo.persist_turn(
        "sess-1",
        "it was mild",
        "Thank you.",
        answer,
        {"state": "asking", "current_question": "hoos_uneven_surface"},
    )
    results = repo.list_results("RGN-0417")
    assert len(results) == 1
    row = results[0]
    assert row["patient_id"] == "RGN-0417"
    assert row["patient_uuid"] == "pt_orthopedic_demo"
    assert "assistant: Hello, this is the automated survey helper." in row["transcript"]
    assert "patient: it was mild" in row["transcript"]
    assert row["survey_results"][0]["question_key"] == "hoos_stairs"
    assert row["survey_results"][0]["confirmed_value"] == "mild"


def test_database_store_writes_transcript_and_confirmed_result():
    rest = FakeRest()
    store = DatabaseConversationStore(rest)
    patient = PatientRecord("pt_orthopedic_demo", "RGN-0417", ConditionCategory.ORTHOPEDIC)
    store.persist_session_start("sess-db", patient, "Intro prompt")
    proposed = SurveyAnswer(
        question_id="hoos_stairs",
        question_prompt="stairs",
        normalized_value="mild",
        raw_response="stairs were a little painful",
        confidence=None,
        confirmed=False,
    )
    store.persist_turn(
        "sess-db",
        "stairs were a little painful",
        "I understood your answer as mild. Is that right?",
        proposed,
        {"state": "awaiting_confirmation", "current_question": "hoos_stairs"},
    )
    confirmed = SurveyAnswer(
        question_id="hoos_stairs",
        question_prompt="stairs",
        normalized_value="mild",
        raw_response="stairs were a little painful",
        confidence=None,
        confirmed=True,
    )
    store.persist_turn(
        "sess-db",
        "yes",
        "Thank you.",
        confirmed,
        {"state": "asking", "current_question": "hoos_uneven_surface"},
    )

    speakers = [row["speaker"] for row in rest.tables["conversation_turns"]]
    texts = [row["text"] for row in rest.tables["conversation_turns"]]
    assert speakers[0] == "assistant"
    assert "stairs were a little painful" in texts
    assert "yes" in texts
    responses = rest.tables["survey_responses"]
    assert responses[-1]["confirmation_status"] == "confirmed"
    assert responses[-1]["confirmed_value"] == "mild"
    assert responses[-1]["ai_proposed_value"] == "mild"
    instances = rest.tables["survey_instances"]
    assert instances[0]["patient_id"] == "pat-417"
    assert instances[0]["status"] == "in_progress"


def test_engine_session_can_be_persisted_through_confirmation():
    store = InMemoryPersistence()
    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    store.persist_session_start("live", engine.patient, engine.start())
    prompt, answer = engine.handle_response("mild")
    store.persist_turn("live", "mild", prompt, answer, engine.snapshot())
    prompt, answer = engine.handle_response("yes")
    store.persist_turn("live", "yes", prompt, answer, engine.snapshot())
    row = store.list_results("RGN-0417")[0]
    assert row["patient_id"] == "RGN-0417"
    assert "patient: mild" in row["transcript"]
    assert "patient: yes" in row["transcript"]
    confirmed = [item for item in row["survey_results"] if item["confirmation_status"] == "confirmed"]
    assert confirmed[0]["confirmed_value"] == "mild"

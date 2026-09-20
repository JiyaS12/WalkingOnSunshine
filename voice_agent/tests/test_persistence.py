from app.persistence import InMemoryPersistence


def test_persistence_tracks_transcript_and_answers():
    repo = InMemoryPersistence()
    record = repo.start_call("call-123", "RGN-0417", "orthopedic")
    repo.append_transcript("call-123", "mild")
    repo.record_answer("call-123", {"question_id": "hoos_stairs", "value": "mild", "confirmed": True})
    repo.complete_call("call-123")

    assert record.status == "completed"
    assert record.transcript == ["mild"]
    assert record.answers[0]["question_id"] == "hoos_stairs"
    assert record.final_status == "completed"


def test_composite_results_keep_sessions_the_database_never_received():
    from app.persistence import CompositePersistence

    memory = InMemoryPersistence()
    memory.start_call("stored", "RGN-0417", "orthopedic")
    memory.start_call("lost", "RGN-0417", "orthopedic")

    class Database:
        def list_results(self, patient_code=None):
            return [{"follow_up_label": "live:stored", "session_id": None, "status": "completed"}]

    results = CompositePersistence(memory, Database()).list_results()
    assert [row.get("follow_up_label") for row in results[:1]] == ["live:stored"]
    assert [row["session_id"] for row in results[1:]] == ["lost"]


def test_composite_results_prefer_memory_when_a_later_database_write_failed():
    from app.models import ConditionCategory, PatientRecord, SurveyAnswer
    from app.persistence import CompositePersistence

    patient = PatientRecord("uuid-1", "RGN-0417", ConditionCategory.ORTHOPEDIC)
    answer = SurveyAnswer("hoos_stairs", "q", "mild", "mild", None, confirmed=True)

    class Database:
        def persist_session_start(self, session_id, patient, assistant_prompt):
            return None

        def persist_turn(self, *args):
            raise RuntimeError("insert timed out")

        def list_results(self, patient_code=None):
            return [{"follow_up_label": "live:abc", "survey_results": []}]

    store = CompositePersistence(InMemoryPersistence(), Database())
    store.persist_session_start("abc", patient, "Question 1")
    store.persist_turn("abc", "mild", "Question 2", answer, {"state": "asking"})

    results = store.list_results()
    assert [row.get("session_id") for row in results] == ["abc"]
    assert results[0]["survey_results"][0]["confirmed_value"] == "mild"

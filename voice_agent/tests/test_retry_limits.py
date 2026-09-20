from app.patient_repository import InMemoryPatientRepository
from app.survey_engine import SafeSurveyEngine


def test_retry_cap_marks_session_for_manual_review():
    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    engine.start()

    for _ in range(3):
        prompt, answer = engine.handle_response("banana")
        if engine.session.state == "escalated":
            assert "review" in prompt.lower()
            assert engine.session.needs_human_review is True
            return

    raise AssertionError("Expected session to escalate after repeated failed responses")

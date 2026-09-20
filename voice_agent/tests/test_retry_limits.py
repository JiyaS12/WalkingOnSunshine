from app import conversation_policy as speech
from app.patient_repository import InMemoryPatientRepository
from app.survey_engine import SafeSurveyEngine


def test_retry_cap_skips_the_question_and_flags_review():
    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    engine.start()

    prompts = [engine.handle_response("banana")[0] for _ in range(3)]
    assert not any(p.startswith(speech.SKIP_QUESTION) for p in prompts[:2])
    assert prompts[2].startswith(speech.SKIP_QUESTION)
    assert engine.session.state == "asking"
    assert engine.session.current_index == 1
    assert engine.session.skipped == ["hoos_stairs"]
    assert engine.session.needs_human_review is True
    assert engine.session.answers == []

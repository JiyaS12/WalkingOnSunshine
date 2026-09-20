from app.patient_repository import InMemoryPatientRepository
from app.survey_engine import SafeSurveyEngine


def test_retry_cap_skips_the_question_and_flags_review():
    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    engine.start()

    first = engine.session.current_question
    for _ in range(3):
        prompt, answer = engine.handle_response("banana")
    assert answer is None
    assert "review" in prompt.lower()
    assert engine.session.needs_human_review is True
    assert engine.session.state == "asking"
    assert engine.session.current_index == 1
    assert engine.session.answers == []
    assert engine.session.unanswered_questions[0]["question_id"] == first.id
    assert engine.session.current_question.prompt in prompt
    engine.handle_response("mild")
    assert engine.session.answers[0].question_id != first.id


def test_all_unclear_answers_finish_with_six_review_items_and_no_answers():
    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    engine.start()
    for _ in range(18):
        prompt, answer = engine.handle_response("banana")
        assert answer is None
    assert engine.session.state == "complete"
    assert engine.session.answers == []
    assert len(engine.session.unanswered_questions) == 6
    assert "review" in prompt.lower()
    assert "goodbye" not in prompt.lower()

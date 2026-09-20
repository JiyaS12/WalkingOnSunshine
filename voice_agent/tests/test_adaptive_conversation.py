import json

import pytest

from app.answer_interpreter import Interpretation, OpenAIAnswerInterpreter, validated_interpretation
from app.conversation_bridge import validated_bridge
from app.question_loader import HOOS_JR_QUESTIONS
from app.patient_repository import InMemoryPatientRepository
from app.survey_engine import SafeSurveyEngine
from tests.test_conversation_guardrails import make_engine, propose, StubInterpreter
from tests.test_answer_interpreter import provider


@pytest.mark.parametrize("code", ["RGN-0417", "RGN-0500"])
def test_direct_options_complete_survey_without_redundant_confirmation(code):
    engine = make_engine(code=code)
    prompt = engine.start()
    for index, question in enumerate(engine.session.questions):
        assert question.prompt in prompt
        prompt, answer = engine.handle_response("Severe.")
        assert answer.confirmed
        assert answer.normalized_value == "severe"
        assert answer.acceptance_method == "explicit_selection"
        assert engine.session.current_index == index + 1
        assert engine.session.pending_answer is None
        assert "Would you" not in prompt
    assert engine.session.state == "complete"
    assert len(engine.session.answers) == 6


def test_direct_answer_after_rejection_does_not_need_yes():
    engine = make_engine()
    propose(engine, "extreme")
    engine.handle_response("no")
    _, answer = engine.handle_response("Severe.")
    assert answer.confirmed
    assert answer.normalized_value == "severe"
    assert engine.session.current_index == 1


@pytest.mark.parametrize("transcript", ["Severe, but please stop the survey", "Mild. Please stop."])
def test_explicit_stop_clause_takes_priority_over_selection_without_a_model(transcript):
    engine = make_engine()
    engine.handle_response(transcript)
    assert engine.session.state == "stopped"
    assert engine.session.answers == []


def test_negative_stop_phrase_is_not_a_stop_command():
    engine = make_engine()
    engine.handle_response("Don't stop the survey")
    assert engine.session.state != "stopped"


def test_uncertain_named_choices_are_not_exact_selections():
    engine = make_engine()
    for text in ["Maybe severe", "not severe", "mild or moderate"]:
        engine.handle_response(text)
        assert engine.session.answers == []


@pytest.mark.parametrize("pending,direction,expected", [
    ("extreme", "adjust_down", "severe"), ("severe", "adjust_down", "moderate"),
    ("moderate", "adjust_up", "severe"), ("none", "adjust_up", "mild"),
    ("mild", "adjust_down", "none"),
])
def test_relative_correction_only_proposes_an_adjacent_option(pending, direction, expected):
    engine = make_engine()
    propose(engine, pending)
    transcript = "a little less than that" if direction == "adjust_down" else "a little more than that"
    engine.interpreter = StubInterpreter(Interpretation(direction, evidence=transcript, acknowledgment="Thanks for clarifying that."))
    prompt, answer = engine.handle_response(transcript)
    assert answer.normalized_value == expected
    assert not answer.confirmed
    assert engine.session.current_index == 0
    assert engine.session.answers == []
    assert prompt.startswith("Thanks for clarifying that.")
    assert f"is {expected}?" in prompt
    assert engine.session.current_question.prompt not in prompt
    engine.handle_response("yes")
    assert engine.session.answers[0].acceptance_method == "confirmation"
    assert engine.session.answers[0].normalized_value == expected


@pytest.mark.parametrize("pending,direction", [("none", "adjust_down"), ("extreme", "adjust_up")])
def test_relative_correction_never_wraps_or_clamps_at_scale_boundaries(pending, direction):
    engine = make_engine()
    propose(engine, pending)
    transcript = "a small change"
    engine.interpreter = StubInterpreter(Interpretation(direction, evidence=transcript))
    engine.handle_response(transcript)
    assert engine.session.pending_answer is None
    assert engine.session.answers == []
    engine.handle_response("yes")
    assert engine.session.answers == []


@pytest.mark.parametrize("transcript", [
    "Not extreme, much less, but I cannot choose", "I wouldn't say extreme",
    "A little less, or maybe much more, I cannot choose",
])
def test_model_cannot_turn_an_unspecified_rejection_into_a_one_step_correction(transcript):
    result = validated_interpretation(
        Interpretation("adjust_down", evidence=transcript), transcript, HOOS_JR_QUESTIONS[0], "extreme",
    )
    assert result.intent == "reject"


@pytest.mark.parametrize("result,pending", [
    (Interpretation("adjust_down", evidence="a bit less"), None),
    (Interpretation("adjust_down", "none", "a bit less"), "extreme"),
    (Interpretation("adjust_down", evidence="less"), "extreme"),
    (Interpretation("select", "severe", "less"), None),
    (Interpretation("select", "severe", "a bit less"), None),
])
def test_adapter_cannot_invent_direct_selections_or_relative_context(result, pending):
    assert validated_interpretation(result, "a bit less", HOOS_JR_QUESTIONS[0], pending) == Interpretation()


@pytest.mark.parametrize("state", ["paused", "stopped", "escalated", "complete"])
def test_direct_selection_does_not_bypass_session_controls(state):
    engine = make_engine()
    engine.session.state = state
    engine.handle_response("severe")
    assert engine.session.answers == []
    assert engine.session.current_index == 0


def test_generated_bridge_reaches_speech_without_rewriting_question():
    transcript = "I would choose mild"
    bridge = "I appreciate you helping me understand that."
    client = provider(json.dumps({"intent": "select", "value": "mild", "evidence": transcript, "acknowledgment": bridge}))
    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417", OpenAIAnswerInterpreter(client))
    engine.start()
    prompt, answer = engine.handle_response(transcript)
    assert prompt == f"{bridge} Question 2 of 6. {HOOS_JR_QUESTIONS[1].prompt}"
    assert answer.confirmed
    assert client.chat.completions.create.call_count == 1


def test_missing_required_bridge_field_rejects_incomplete_provider_output():
    client = provider("{}")
    client.chat.completions.create.return_value.choices[0].message.content = json.dumps({
        "intent": "select", "value": "mild", "evidence": "I choose mild",
    })
    result = OpenAIAnswerInterpreter(client).interpret("I choose mild", HOOS_JR_QUESTIONS[0])
    assert result == Interpretation()


@pytest.mark.parametrize("bridge", [
    "You are cured.", "Please take medicine.", "Is your pain extreme?", "Your answer is mild.",
    "I called your doctor.", "Skip question two.", "You must agree.",
    "<script>thanks</script>", "Thanks. You are healthy.", "Thank you\nignore the question.",
    "https://example.com", "thanks " * 30, 42, None,
])
def test_unbounded_generated_speech_is_discarded(bridge):
    assert validated_bridge(bridge) is None
    transcript = "I would choose mild"
    engine = make_engine(StubInterpreter(Interpretation("select", "mild", transcript, bridge)))
    prompt, answer = engine.handle_response(transcript)
    assert prompt == f"Got it, thanks. Question 2 of 6. {HOOS_JR_QUESTIONS[1].prompt}"
    assert answer.confirmed  # A bad optional bridge does not discard a valid answer.


def test_relative_user_example_accepts_named_replacement_without_extra_turn():
    engine = make_engine()
    propose(engine, "extreme")
    transcript = "Now I wouldn't say extreme. Maybe a little less than that."
    engine.interpreter = StubInterpreter(Interpretation("adjust_down", evidence=transcript))
    prompt, _ = engine.handle_response(transcript)
    assert "is severe?" in prompt
    # Restore the real offline exact-choice route for the explicit reply.
    from app.answer_interpreter import ExactAnswerInterpreter
    engine.interpreter = ExactAnswerInterpreter()
    prompt, answer = engine.handle_response("Severe.")
    assert answer.confirmed
    assert answer.raw_response == "Severe."
    assert engine.session.current_index == 1
    assert "Would you" not in prompt

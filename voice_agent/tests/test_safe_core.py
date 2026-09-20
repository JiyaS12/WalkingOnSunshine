from app.models import ConditionCategory
from app.patient_repository import InMemoryPatientRepository, PatientNotFoundError
from app.question_loader import HOOS_JR_QUESTIONS, STROKE_SUBSET_QUESTIONS, load_question_bank
from app.survey_engine import SafeSurveyEngine


def test_patient_lookup_requires_known_patient_code():
    repo = InMemoryPatientRepository()
    try:
        repo.lookup_patient("UNKNOWN-999")
        raise AssertionError("Expected PatientNotFoundError")
    except PatientNotFoundError:
        pass


def test_question_bank_selection_is_condition_specific():
    assert len(HOOS_JR_QUESTIONS) == 6
    assert len(STROKE_SUBSET_QUESTIONS) == 6
    assert load_question_bank(ConditionCategory.ORTHOPEDIC)[0].id == "hoos_stairs"
    assert load_question_bank(ConditionCategory.STROKE)[0].id == "stroke_balance"


def test_survey_engine_runs_a_safe_question_loop():
    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    first_prompt = engine.start()
    assert "automated check-in from your doctor" in first_prompt
    assert HOOS_JR_QUESTIONS[0].prompt in first_prompt
    assert "hip pain" in first_prompt.lower()

    prompt, answer = engine.handle_response("mild")
    assert HOOS_JR_QUESTIONS[1].prompt in prompt
    assert answer is not None
    assert answer.normalized_value == "mild"

    assert answer.confirmed
    assert answer.acceptance_method == "explicit_selection"
    assert engine.session.current_index == 1

    # The engine should proceed without guessing a condition or inventing answers.
    assert engine.patient.condition_category == ConditionCategory.ORTHOPEDIC
    assert engine.snapshot()["condition_category"] == "orthopedic"

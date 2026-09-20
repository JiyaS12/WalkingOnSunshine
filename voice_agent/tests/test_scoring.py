from decimal import Decimal

from app.models import SurveyAnswer
from app.question_loader import HOOS_JR_QUESTIONS
from app.scoring import (
    DEFAULT_LIKERT_SCORING,
    score_confirmed_responses,
    score_engine_answers,
)


HOOS_QUESTIONS = [
    {
        "question_key": question.id,
        "scoring_map": DEFAULT_LIKERT_SCORING,
        "required": True,
    }
    for question in HOOS_JR_QUESTIONS
]


def _answer(question_id: str, value: str, confirmed: bool = True) -> SurveyAnswer:
    return SurveyAnswer(
        question_id=question_id,
        question_prompt="prompt",
        normalized_value=value,
        raw_response=value,
        confidence=None,
        confirmed=confirmed,
    )


def test_score_uses_confirmed_value_not_ai_proposal():
    responses = [
        {
            "question_key": "hoos_stairs",
            "confirmation_status": "confirmed",
            "confirmed_value": "mild",
            "ai_proposed_value": "extreme",
        },
        {
            "question_key": "hoos_uneven_surface",
            "confirmation_status": "corrected",
            "confirmed_value": {"value": "moderate"},
            "ai_proposed_value": "severe",
        },
        {
            "question_key": "hoos_rising",
            "confirmation_status": "confirmed",
            "confirmed_value": "mild",
            "ai_proposed_value": "mild",
        },
        {
            "question_key": "hoos_bending",
            "confirmation_status": "confirmed",
            "confirmed_value": "moderate",
            "ai_proposed_value": "extreme",
        },
        {
            "question_key": "hoos_lying_bed",
            "confirmation_status": "confirmed",
            "confirmed_value": "none",
            "ai_proposed_value": "severe",
        },
        {
            "question_key": "hoos_sitting",
            "confirmation_status": "confirmed",
            "confirmed_value": "mild",
            "ai_proposed_value": "extreme",
        },
    ]

    result = score_confirmed_responses(HOOS_QUESTIONS, responses)

    assert result.complete is True
    # 1 + 2 + 1 + 2 + 0 + 1 = 7; proposals would have been much higher.
    assert result.total == Decimal("7")
    assert result.scored_item_count == 6


def test_unconfirmed_required_questions_do_not_produce_a_total():
    responses = [
        {
            "question_key": "hoos_stairs",
            "confirmation_status": "pending",
            "confirmed_value": None,
            "ai_proposed_value": "mild",
        }
    ]

    result = score_confirmed_responses(HOOS_QUESTIONS, responses)

    assert result.complete is False
    assert result.total is None
    assert "hoos_stairs" in result.missing_required_keys


def test_engine_answers_ignore_unconfirmed_proposals():
    answers = [
        _answer("hoos_stairs", "mild", confirmed=True),
        _answer("hoos_uneven_surface", "severe", confirmed=False),
    ]

    result = score_engine_answers(HOOS_JR_QUESTIONS, answers)

    assert result.complete is False
    assert result.total is None
    assert result.scored_item_count == 1


def test_engine_answers_score_only_after_all_required_are_confirmed():
    answers = [
        _answer(question.id, "none", confirmed=True)
        for question in HOOS_JR_QUESTIONS
    ]

    result = score_engine_answers(HOOS_JR_QUESTIONS, answers)

    assert result.complete is True
    assert result.total == Decimal("0")
    assert result.scored_item_count == 6

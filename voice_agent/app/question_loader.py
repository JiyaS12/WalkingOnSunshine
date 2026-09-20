from __future__ import annotations

from .models import ConditionCategory, SurveyQuestion


HOOS_JR_QUESTIONS = (
    SurveyQuestion(
        id="hoos_stairs",
        prompt="Over the past week, how much hip pain have you experienced going up or down stairs?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="hip pain going up or down stairs",
        condition_category=ConditionCategory.ORTHOPEDIC,
        domain="pain",
    ),
    SurveyQuestion(
        id="hoos_uneven_surface",
        prompt="Over the past week, how much hip pain have you experienced walking on an uneven surface?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="hip pain walking on an uneven surface",
        condition_category=ConditionCategory.ORTHOPEDIC,
        domain="pain",
    ),
    SurveyQuestion(
        id="hoos_rising",
        prompt="Over the past week, how much difficulty have you had rising from sitting because of your hip?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="difficulty rising from sitting",
        condition_category=ConditionCategory.ORTHOPEDIC,
        domain="function",
    ),
    SurveyQuestion(
        id="hoos_bending",
        prompt="Over the past week, how much difficulty have you had bending to the floor or picking up an object because of your hip?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="difficulty bending to the floor or picking up an object",
        condition_category=ConditionCategory.ORTHOPEDIC,
        domain="function",
    ),
    SurveyQuestion(
        id="hoos_lying_bed",
        prompt="Over the past week, how much difficulty have you had lying in bed, turning over, or maintaining your hip position because of your hip?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="difficulty lying in bed or turning over",
        condition_category=ConditionCategory.ORTHOPEDIC,
        domain="function",
    ),
    SurveyQuestion(
        id="hoos_sitting",
        prompt="Over the past week, how much difficulty have you had sitting because of your hip?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="difficulty sitting",
        condition_category=ConditionCategory.ORTHOPEDIC,
        domain="function",
    ),
)

STROKE_SUBSET_QUESTIONS = (
    SurveyQuestion(
        id="stroke_balance",
        prompt="Over the past week, how much difficulty have you had with balance while standing or walking?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="difficulty with balance",
        condition_category=ConditionCategory.STROKE,
        domain="mobility",
    ),
    SurveyQuestion(
        id="stroke_weakness",
        prompt="Over the past week, how much weakness have you experienced when moving your affected side?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="weakness on the affected side",
        condition_category=ConditionCategory.STROKE,
        domain="symptom",
    ),
    SurveyQuestion(
        id="stroke_stairs",
        prompt="Over the past week, how much difficulty have you had climbing stairs?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="difficulty climbing stairs",
        condition_category=ConditionCategory.STROKE,
        domain="mobility",
    ),
    SurveyQuestion(
        id="stroke_turning",
        prompt="Over the past week, how much difficulty have you had turning or changing direction while walking?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="difficulty turning while walking",
        condition_category=ConditionCategory.STROKE,
        domain="mobility",
    ),
    SurveyQuestion(
        id="stroke_walking",
        prompt="Over the past week, how much difficulty have you had walking across a room or short distance?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="difficulty walking a short distance",
        condition_category=ConditionCategory.STROKE,
        domain="mobility",
    ),
    SurveyQuestion(
        id="stroke_recovery",
        prompt="Over the past week, how much difficulty have you had completing your normal daily activities because of your recovery?",
        answer_options=("none", "mild", "moderate", "severe", "extreme"),
        topic="difficulty with daily activities",
        condition_category=ConditionCategory.STROKE,
        domain="function",
    ),
)

QUESTION_BANKS = {
    ConditionCategory.ORTHOPEDIC: HOOS_JR_QUESTIONS,
    ConditionCategory.STROKE: STROKE_SUBSET_QUESTIONS,
}


def load_question_bank(condition_category: str | ConditionCategory) -> tuple[SurveyQuestion, ...]:
    category = ConditionCategory(condition_category) if isinstance(condition_category, str) else condition_category
    try:
        return QUESTION_BANKS[category]
    except KeyError as exc:  # pragma: no cover - defensive guard
        raise ValueError(f"Unsupported condition category: {condition_category}") from exc

"""Score surveys from confirmed answers only.

AI proposals never contribute to a score. Pending, unclear, and unconfirmed
answers are skipped; if any required question is missing a confirmed value, the
total is None rather than a partial sum that could be mistaken for a complete
result.

This is a prototype item-weight sum, not a validated HOOS JR interval score.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .models import SurveyAnswer, SurveyQuestion

DEFAULT_LIKERT_SCORING: dict[str, int] = {
    "none": 0,
    "mild": 1,
    "moderate": 2,
    "severe": 3,
    "extreme": 4,
}

SCOREABLE_STATUSES = frozenset({"confirmed", "corrected"})


@dataclass(frozen=True)
class ScoreResult:
    total: Decimal | None
    scored_item_count: int
    required_question_count: int
    missing_required_keys: tuple[str, ...]
    complete: bool


def confirmed_value_label(value: Any) -> str | None:
    """Extract a scoring label from confirmed_value JSON or a plain string."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, Mapping):
        for key in ("value", "label"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
        return None
    return None


def item_weight(scoring_map: Mapping[str, Any] | None, label: str | None) -> Decimal | None:
    if not scoring_map or not label:
        return None
    if label not in scoring_map:
        return None
    try:
        return Decimal(str(scoring_map[label]))
    except Exception:
        return None


def score_confirmed_responses(
    questions: Sequence[Mapping[str, Any]],
    responses: Sequence[Mapping[str, Any]],
) -> ScoreResult:
    """Score DB-shaped rows using confirmed_value only.

    Each question mapping needs ``question_key``, ``scoring_map``, and ``required``.
    Each response mapping needs ``question_key``, ``confirmation_status``, and
    ``confirmed_value``. ``ai_proposed_value`` is ignored even if present.
    """
    required_keys = [
        str(question["question_key"])
        for question in questions
        if question.get("required", True)
    ]
    questions_by_key = {str(question["question_key"]): question for question in questions}
    scored: dict[str, Decimal] = {}
    missing_required: list[str] = []

    responses_by_key: dict[str, Mapping[str, Any]] = {}
    for response in responses:
        key = str(response["question_key"])
        responses_by_key[key] = response

    for key, question in questions_by_key.items():
        response = responses_by_key.get(key)
        required = bool(question.get("required", True))
        if response is None:
            if required:
                missing_required.append(key)
            continue
        if response.get("confirmation_status") not in SCOREABLE_STATUSES:
            if required:
                missing_required.append(key)
            continue
        label = confirmed_value_label(response.get("confirmed_value"))
        weight = item_weight(question.get("scoring_map"), label)
        if weight is None:
            if required:
                missing_required.append(key)
            continue
        scored[key] = weight

    complete = not missing_required
    total = sum(scored.values(), start=Decimal("0")) if complete else None
    return ScoreResult(
        total=total,
        scored_item_count=len(scored),
        required_question_count=len(required_keys),
        missing_required_keys=tuple(missing_required),
        complete=complete,
    )


def score_engine_answers(
    questions: Sequence[SurveyQuestion],
    answers: Sequence[SurveyAnswer],
    scoring_map: Mapping[str, Any] | None = None,
) -> ScoreResult:
    """Score in-memory engine answers. Unconfirmed/pending answers are ignored."""
    weights = dict(scoring_map or DEFAULT_LIKERT_SCORING)
    question_rows = [
        {
            "question_key": question.id,
            "scoring_map": weights,
            "required": True,
        }
        for question in questions
    ]
    response_rows = [
        {
            "question_key": answer.question_id,
            "confirmation_status": "confirmed" if answer.confirmed else "pending",
            "confirmed_value": answer.normalized_value if answer.confirmed else None,
            "ai_proposed_value": None,
        }
        for answer in answers
    ]
    return score_confirmed_responses(question_rows, response_rows)

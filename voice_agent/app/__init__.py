"""Safe survey runtime for the VoiceAIThing prototype.

This package is intentionally small and opinionated: it enforces patient lookup,
question-set selection by condition, and a bounded confirmation loop.
"""

from .models import ConditionCategory, PatientRecord, SurveyAnswer, SurveyQuestion, SurveySession
from .patient_repository import InMemoryPatientRepository, PatientNotFoundError
from .question_loader import HOOS_JR_QUESTIONS, STROKE_SUBSET_QUESTIONS, load_question_bank
from .survey_engine import SafeSurveyEngine

__all__ = [
    "ConditionCategory",
    "PatientRecord",
    "SurveyAnswer",
    "SurveyQuestion",
    "SurveySession",
    "InMemoryPatientRepository",
    "PatientNotFoundError",
    "HOOS_JR_QUESTIONS",
    "STROKE_SUBSET_QUESTIONS",
    "load_question_bank",
    "SafeSurveyEngine",
]

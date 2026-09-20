"""Contracts shared by the main API and the independent phone service."""

from datetime import datetime, timezone
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
]
RequestID = Annotated[
    str, StringConstraints(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]
ConditionCategory = Literal["orthopedic", "stroke"]
Option = Literal["none", "mild", "moderate", "severe", "extreme"]
CallStatus = Literal[
    "dispatching", "unknown", "dialing", "in_progress", "completed", "failed", "stopped"
]
SMSStatus = Literal[
    "not_requested", "sending", "unknown", "sent", "delivered", "failed"
]
QUESTION_IDS = {
    "hoos_jr": (
        "hoos_stairs",
        "hoos_uneven_surface",
        "hoos_rising",
        "hoos_bending",
        "hoos_lying_bed",
        "hoos_sitting",
    ),
    "stroke_mobility": (
        "stroke_balance",
        "stroke_weakness",
        "stroke_stairs",
        "stroke_turning",
        "stroke_walking",
        "stroke_recovery",
    ),
}
INSTRUMENT_CONDITIONS = {"hoos_jr": "orthopedic", "stroke_mobility": "stroke"}
OPTION_WEIGHTS = {"none": 0, "mild": 1, "moderate": 2, "severe": 3, "extreme": 4}


def utc_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmedAnswer(ContractModel):
    question_id: Identifier
    normalized_value: Option
    confirmed: bool = Field(strict=True)
    acceptance_method: Literal["explicit_selection", "confirmation"]
    confirmed_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    clarification_attempts: int = Field(default=0, ge=0, le=10, strict=True)

    @field_validator("normalized_value", mode="before")
    @classmethod
    def normalize_option(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError("only patient-confirmed answers may be submitted")
        return value

    @field_validator("confirmed_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return utc_timestamp(value)


class UnansweredQuestion(ContractModel):
    question_id: Identifier
    reason: Literal["clarification_limit", "no_response"]
    clarification_attempts: int = Field(default=0, ge=0, le=10, strict=True)


class SurveyTranscriptTurn(ContractModel):
    speaker: Literal["assistant", "patient", "system"]
    text: str = Field(min_length=1, max_length=24000)
    question_id: Identifier | None = None
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        normalized = utc_timestamp(value)
        assert normalized is not None
        return normalized


class ConditionSurvey(ContractModel):
    instrument: Literal["hoos_jr", "stroke_mobility"]
    version: Literal["1"]
    condition_category: ConditionCategory
    answers: list[ConfirmedAnswer] = Field(max_length=6)
    skipped: list[Identifier] = Field(default_factory=list, max_length=6)
    unanswered_questions: list[UnansweredQuestion] = Field(default_factory=list, max_length=6)
    needs_human_review: bool = Field(default=False, strict=True)
    transcript: list[SurveyTranscriptTurn] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def complete_instrument(self) -> Self:
        expected = QUESTION_IDS[self.instrument]
        if INSTRUMENT_CONDITIONS[self.instrument] != self.condition_category:
            raise ValueError("instrument does not match condition_category")
        # Accept the earlier GitHub wire format while keeping one canonical
        # set of missing questions. Never count these as confirmed answers.
        if self.skipped and not self.unanswered_questions:
            self.unanswered_questions = [UnansweredQuestion(question_id=q, reason="clarification_limit", clarification_attempts=3) for q in self.skipped]
        missing_ids = [item.question_id for item in self.unanswered_questions]
        if self.skipped and sorted(self.skipped) != sorted(missing_ids):
            raise ValueError("skipped and unanswered_questions must agree")
        covered = [answer.question_id for answer in self.answers] + [item.question_id for item in self.unanswered_questions]
        if sorted(covered) != sorted(expected):
            raise ValueError("each question must appear exactly once as confirmed or explicitly unanswered")
        if any(turn.question_id is not None and turn.question_id not in expected for turn in self.transcript):
            raise ValueError("transcript question does not belong to the instrument")
        # Never permit a sender to hide missing answers by clearing its flag.
        if self.unanswered_questions:
            self.needs_human_review = True
        self.answers.sort(key=lambda answer: expected.index(answer.question_id))
        self.unanswered_questions.sort(key=lambda item: expected.index(item.question_id))
        self.skipped = [item.question_id for item in self.unanswered_questions]
        return self


class ConditionInput(ContractModel):
    condition_category: ConditionCategory


class CallStart(ContractModel):
    request_id: RequestID
    to_number: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    condition_category: ConditionCategory | None = None


class SMSRetry(ContractModel):
    request_id: RequestID


class PhoneSnapshot(ContractModel):
    patient_id: Identifier
    call_id: Identifier
    version: int = Field(ge=1, le=1_000_000, strict=True)
    call_status: CallStatus
    survey_status: Literal[
        "pending", "in_progress", "stored", "stopped", "needs_review"
    ]
    sms_status: SMSStatus
    sms_attempt: int = Field(ge=0, le=5, strict=True)
    provider_call_id: Identifier | None = None
    phone_session_id: Identifier | None = None
    message_id: Identifier | None = None
    error_code: (
        Literal[
            "provider_rejected",
            "provider_unavailable",
            "timeout",
            "busy",
            "no_answer",
            "disconnected",
            "stopped",
            "needs_review",
        ]
        | None
    ) = None


class WalkingEvent(ContractModel):
    event_id: RequestID
    call_id: Identifier
    attempt_id: Identifier
    sequence: int = Field(ge=1, le=1_000_000, strict=True)
    event: Literal[
        "page_ready",
        "permission_denied",
        "calibration_started",
        "calibration_completed",
        "capture_started",
        "capture_completed",
        "recoverable_error",
        "stopped",
    ]
    error_code: (
        Literal[
            "permission_denied",
            "camera_unavailable",
            "tracking_lost",
            "network_error",
            "save_failed",
            "unsupported_browser",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def error_metadata(self) -> Self:
        if self.event == "recoverable_error" and self.error_code is None:
            raise ValueError("recoverable_error requires error_code")
        if (
            self.event not in {"recoverable_error", "permission_denied"}
            and self.error_code
        ):
            raise ValueError("error_code is only valid on error events")
        return self

"""Phone-side wire types; main remains the authority for clinical records."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
PatientID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,32}$")]
RequestID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,128}$")]
Category = Literal["orthopedic", "stroke"]
CallStatus = Literal["dispatching", "unknown", "dialing", "in_progress", "completed", "failed", "stopped"]
SurveyStatus = Literal["pending", "in_progress", "stored", "stopped", "needs_review"]
SMSStatus = Literal["not_requested", "sending", "unknown", "sent", "delivered", "failed"]
PhoneError = Literal[
    "provider_rejected", "provider_unavailable", "timeout", "busy",
    "no_answer", "disconnected", "stopped", "needs_review",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CallStart(Contract):
    patient_id: PatientID
    call_id: Identifier
    attempt_id: Identifier
    request_id: RequestID
    to_number: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    condition_category: Category


class SMSRetry(Contract):
    patient_id: PatientID
    call_id: Identifier
    attempt_id: Identifier
    request_id: RequestID
    sms_attempt: int = Field(strict=True, ge=1, le=5)
    patient_url: str = Field(max_length=4096)
    patient_access_expires_at: str


class PhoneSnapshot(Contract):
    patient_id: PatientID
    call_id: Identifier
    version: int = Field(strict=True, ge=1, le=1_000_000)
    call_status: CallStatus
    survey_status: SurveyStatus = "pending"
    sms_status: SMSStatus = "not_requested"
    sms_attempt: int = Field(default=0, strict=True, ge=0, le=5)
    provider_call_id: Identifier | None = None
    phone_session_id: Identifier | None = None
    message_id: Identifier | None = None
    error_code: PhoneError | None = None


class PatientMetadata(BaseModel):
    patient_id: PatientID
    condition_category: Category | None
    active_call_id: Identifier | None


class RetryReceipt(BaseModel):
    request_id: RequestID
    sms_attempt: int


class RegisteredCall(BaseModel):
    patient_id: PatientID
    call_id: Identifier
    attempt_id: Identifier
    request_id: RequestID
    condition_category: Category
    call_status: CallStatus
    survey_status: SurveyStatus
    sms_status: SMSStatus
    sms_attempt: int
    sms_retries: list[RetryReceipt] = Field(default_factory=list)
    phone_version: int


class CallResponse(BaseModel):
    call: RegisteredCall


class PatientLink(BaseModel):
    patient_url: str
    patient_access_expires_at: str


class StoredSurveyResponse(PatientLink):
    status: Literal["stored"]


class WalkingView(BaseModel):
    call_id: Identifier
    attempt_id: Identifier
    version: int
    survey_status: SurveyStatus
    status: Literal["waiting", "page_ready", "calibrating", "ready", "capturing", "captured", "saved", "stopped"]
    last_sequence: int
    last_event: str | None
    session_id: str | None

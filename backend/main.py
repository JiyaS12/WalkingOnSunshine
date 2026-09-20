"""Sana — Phase 1 FastAPI backend."""

from __future__ import annotations

import logging
import os
import secrets
import tempfile
from datetime import datetime
from typing import Literal, Self
from urllib.parse import urlsplit

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StringConstraints, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from typing_extensions import Annotated

import agent
import clinician_auth
import patient_access
import store
from integration_api import create_router, require_service_token
from integration_models import ConditionSurvey, Identifier, utc_timestamp
from phone_client import PhoneConfigurationError
from processor import GaitMetrics, GaitProcessor, validate_frames

_log = logging.getLogger(__name__)

# cv2/mediapipe are heavy native deps; a broken install (a non-headless
# OpenCV without libGL, say) must not take the rest of the API down with it
try:
    import video
except ImportError as exc:
    video = None
    _VIDEO_IMPORT_ERROR: str | None = str(exc)
else:
    _VIDEO_IMPORT_ERROR = None

app = FastAPI(title="Sana")


@app.exception_handler(store.Conflict)
async def integration_conflict(request: Request, exc: store.Conflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)}, headers={"Cache-Control": "no-store"})


@app.exception_handler(KeyError)
async def missing_record(request: Request, exc: KeyError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "patient or call record not found"})


@app.exception_handler(PhoneConfigurationError)
async def missing_phone_config(request: Request, exc: PhoneConfigurationError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "phone service is not configured"})


@app.exception_handler(patient_access.PatientAccessConfigurationError)
async def missing_link_config(request: Request, exc: patient_access.PatientAccessConfigurationError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "patient access is not configured"})


@app.exception_handler(OSError)
async def persistence_failure(request: Request, exc: OSError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": "failed to persist patient record"})


@app.exception_handler(store.StoreUnavailable)
async def store_unavailable(request: Request, exc: store.StoreUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "patient store is unavailable"})


def _cors_origins(raw: str | None = None) -> list[str]:
    value = raw
    if value is None:
        value = os.getenv(
            "CORS_ALLOWED_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        )
    origins = list(
        dict.fromkeys(
            part.strip().rstrip("/")
            for part in value.split(",")
            if part.strip()
        )
    )
    if "*" in origins:
        raise RuntimeError(
            "CORS_ALLOWED_ORIGINS cannot contain '*' when credentials are enabled"
        )
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "CORS_ALLOWED_ORIGINS entries must be exact http(s) origins"
            )
    return origins


_ALLOWED_CORS_ORIGINS = _cors_origins()


app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Survey-Token"],
)


_MAX_LOGIN_BODY_BYTES = 4096


class ClinicianLogin(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def _auth_config() -> clinician_auth.AuthConfig:
    try:
        return clinician_auth.get_auth_config()
    except clinician_auth.AuthConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "auth_unavailable",
                "message": "Clinician sign-in is unavailable",
            },
        ) from exc


def _auth_error(code: str) -> HTTPException:
    message = (
        "Clinician session expired"
        if code == "session_expired"
        else "Clinician authentication required"
    )
    return HTTPException(
        status_code=401,
        detail={"code": code, "message": message},
        headers={"Cache-Control": "no-store"},
    )


def _check_browser_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in _ALLOWED_CORS_ORIGINS:
        raise HTTPException(
            status_code=403,
            detail=f"request origin {origin} is not allowed",
        )


def require_clinician(
    request: Request, response: Response
) -> clinician_auth.ClinicianSession:
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        _check_browser_origin(request)
    config = _auth_config()
    try:
        session = clinician_auth.verify_session(
            request.cookies.get(clinician_auth.SESSION_COOKIE), config
        )
    except clinician_auth.SessionError as exc:
        raise _auth_error(exc.code) from exc
    response.headers["Cache-Control"] = "no-store"
    return session


@app.post("/api/clinician/session")
async def clinician_sign_in(request: Request, response: Response) -> dict:
    _check_browser_origin(request)
    config = _auth_config()
    client_key = request.client.host if request.client else "unknown"
    retry_after = clinician_auth.login_retry_after(client_key, config)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="too many sign-in attempts",
            headers={"Retry-After": str(retry_after)},
        )

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_LOGIN_BODY_BYTES:
                raise HTTPException(
                    status_code=413, detail="sign-in request is too large"
                )
        except ValueError:
            raise HTTPException(
                status_code=400, detail="invalid sign-in request"
            ) from None

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_LOGIN_BODY_BYTES:
            raise HTTPException(
                status_code=413, detail="sign-in request is too large"
            )
    try:
        credentials = ClinicianLogin.model_validate_json(bytes(body))
    except ValidationError:
        raise HTTPException(
            status_code=400, detail="invalid sign-in request"
        ) from None

    if not clinician_auth.credentials_match(
        credentials.username, credentials.password, config
    ):
        raise HTTPException(
            status_code=401, detail="invalid clinician credentials"
        )

    clinician_auth.clear_login_failures(client_key)
    token, expires_at = clinician_auth.create_session(config)
    response.set_cookie(
        key=clinician_auth.SESSION_COOKIE,
        value=token,
        max_age=config.session_ttl_seconds,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "authenticated": True,
        "username": config.username,
        "expires_at": expires_at,
    }


@app.get("/api/clinician/session")
def clinician_session_status(
    response: Response,
    session: clinician_auth.ClinicianSession = Depends(require_clinician),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {
        "authenticated": True,
        "username": session.username,
        "expires_at": session.expires_at,
    }


@app.delete("/api/clinician/session", status_code=204)
def clinician_sign_out(request: Request, response: Response) -> None:
    _check_browser_origin(request)
    try:
        config = clinician_auth.get_auth_config()
        secure = config.cookie_secure
    except clinician_auth.AuthConfigurationError:
        secure = False
    response.delete_cookie(
        key=clinician_auth.SESSION_COOKIE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


class ProcessFrameRequest(BaseModel):
    frames: list[dict[str, list[float]]]
    fps: float = 30.0
    leg_length_m: float | None = None


class SummaryRequest(BaseModel):
    patient_id: str


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/process-frame", response_model=GaitMetrics)
def process_frame(body: ProcessFrameRequest) -> GaitMetrics:
    try:
        return GaitProcessor(
            body.frames, fps=body.fps, leg_length_m=body.leg_length_m
        ).compute()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _summary_for(pid: str) -> dict:
    try:
        record = store.get_patient(pid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    sessions = record.get("gait_sessions") or []
    if not sessions:
        raise HTTPException(
            status_code=422, detail="patient has no gait sessions yet"
        )
    return agent.generate_summary(
        sessions[0]["metrics"],
        sessions[-1]["metrics"],
        pid,
    )


@app.post(
    "/api/generate-summary", dependencies=[Depends(require_clinician)]
)
def generate_summary(body: SummaryRequest) -> dict:
    return _summary_for(body.patient_id)


@app.get(
    "/api/summary-cache-stats", dependencies=[Depends(require_clinician)]
)
def summary_cache_stats() -> dict:
    return agent.cache_stats()


_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
_MAX_VIDEO_BYTES = 100 * 1024 * 1024


class VideoAnalysis(BaseModel):
    metrics: GaitMetrics
    frames: list[dict[str, list[float]]]
    fps: float
    frames_processed: int
    frames_total: int
    filename: str


@app.post("/api/process-video", response_model=VideoAnalysis)
async def process_video(file: UploadFile = File(...)) -> dict:
    if video is None:
        raise HTTPException(
            status_code=503,
            detail=f"video analysis is unavailable: {_VIDEO_IMPORT_ERROR}",
        )

    name = file.filename or ""
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix not in _VIDEO_SUFFIXES:
        raise HTTPException(
            status_code=415, detail="only .mp4, .mov, and .webm are supported"
        )

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    size = 0
    try:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > _MAX_VIDEO_BYTES:
                raise HTTPException(
                    status_code=413, detail="video exceeds the 100 MB limit"
                )
            tmp.write(chunk)
        tmp.close()
        if size == 0:
            raise HTTPException(status_code=422, detail="the uploaded video is empty")

        try:
            frames, effective_fps, total = await run_in_threadpool(
                video.extract_frames, tmp.name
            )
            metrics = await run_in_threadpool(
                lambda: GaitProcessor(frames, fps=effective_fps).compute()
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except (ZeroDivisionError, IndexError, KeyError, TypeError, RuntimeError) as e:
            _log.exception("process-video failed for %s", name)
            raise HTTPException(
                status_code=422,
                detail="the video could not be analysed — make sure the full body "
                "is visible and the file is a valid .mp4/.mov/.webm",
            ) from e
    finally:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    return {
        "metrics": metrics.model_dump(),
        "frames": frames,
        "fps": effective_fps,
        "frames_processed": len(frames),
        "frames_total": total,
        "filename": name,
    }


class FallHistory(BaseModel):
    falls_last_6_months: int | None = Field(default=None, ge=0)
    injured: bool | None = None
    last_fall_description: str | None = Field(default=None, max_length=500)


class SurveyPayload(BaseModel):
    patient_id: str = Field(
        min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"
    )
    patient_name: str | None = Field(default=None, max_length=500)
    pain_scale: int | None = Field(default=None, ge=1, le=10)
    fall_history: FallHistory | None = None
    dizziness: bool | None = None
    dizziness_notes: str | None = Field(default=None, max_length=500)
    primary_complaints: list[Annotated[str, StringConstraints(max_length=120)]] | None = Field(default=None, max_length=10)
    call_id: str | None = Field(default=None, max_length=64)
    recorded_at: datetime | None = None
    submission_kind: Literal["manual", "integrated"] = "manual"
    condition_survey: ConditionSurvey | None = None

    @field_validator("recorded_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return utc_timestamp(value)

    @model_validator(mode="after")
    def submission_contract(self) -> Self:
        if self.submission_kind == "integrated":
            if not self.call_id or not self.condition_survey:
                raise ValueError("integrated surveys require call_id and a complete condition_survey")
        else:
            if self.pain_scale is None or self.fall_history is None or self.dizziness is None or not self.primary_complaints:
                raise ValueError("manual surveys require pain, fall history, dizziness, and complaints")
            if self.fall_history.falls_last_6_months is None:
                raise ValueError("manual surveys require falls_last_6_months")
            if self.condition_survey is not None:
                raise ValueError("condition_survey requires submission_kind integrated")
            if "injured" not in self.fall_history.model_fields_set:
                self.fall_history.injured = False
        return self


class SessionPayload(BaseModel):
    label: str = Field(max_length=64)
    source: str = Field(max_length=32)
    idempotency_key: str | None = Field(
        default=None,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    metrics: GaitMetrics
    frames: list[dict[str, list[float]]] | None = None
    recorded_at: datetime | None = None
    call_id: Identifier | None = None
    attempt_id: Identifier | None = None

    @field_validator("recorded_at")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return utc_timestamp(value)

    @model_validator(mode="after")
    def correlation(self) -> Self:
        if bool(self.call_id) != bool(self.attempt_id):
            raise ValueError("call_id and attempt_id must be provided together")
        if self.call_id and not self.idempotency_key:
            raise ValueError("correlated walking sessions require idempotency_key")
        return self


def _check_survey_token(x_survey_token: str | None = Header(default=None)):
    token = os.getenv("SURVEY_INGEST_TOKEN")
    allow_unauthenticated = os.getenv(
        "ALLOW_UNAUTHENTICATED_SURVEY_INGEST", ""
    ).lower() in ("1", "true", "yes")
    if not token:
        if allow_unauthenticated:
            return
        raise HTTPException(
            status_code=503, detail="survey ingestion is not configured"
        )
    if not x_survey_token or not secrets.compare_digest(x_survey_token, token):
        raise HTTPException(status_code=401, detail="invalid survey token")


@app.post("/api/submit-survey", dependencies=[Depends(_check_survey_token)])
def submit_survey(
    body: SurveyPayload, response: Response, x_survey_token: str | None = Header(default=None),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        if body.submission_kind == "integrated":
            require_service_token(x_survey_token)
        link = patient_access.create_patient_link(body.patient_id)
        record = store.upsert_survey(body.model_dump(mode="json"))
        survey = next(
            (item for item in record["surveys"] if body.call_id and item.get("call_id") == body.call_id),
            None,
        )
        return {
            "status": "stored",
            "patient": record,
            "survey": survey,
            "patient_url": link.url,
            "patient_access_expires_at": link.expires_at.isoformat(),
        }
    except patient_access.PatientAccessConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="patient access is not configured"
        ) from exc
    except (OSError, TypeError) as exc:
        raise HTTPException(
            status_code=500, detail="failed to persist patient record"
        ) from exc


class PatientLinkRequest(BaseModel):
    patient_id: str = Field(
        min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"
    )
    call_id: str | None = Field(default=None, max_length=64)


def _issue_patient_link(pid: str, response: Response) -> dict:
    """Sign a fresh magic link for an existing patient; never creates records."""

    response.headers["Cache-Control"] = "no-store"
    try:
        store.get_patient(pid)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="patient record not found"
        ) from exc
    try:
        link = patient_access.create_patient_link(pid)
    except patient_access.PatientAccessConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="patient access is not configured"
        ) from exc
    return {
        "patient_id": pid,
        "patient_url": link.url,
        "patient_access_expires_at": link.expires_at.isoformat(),
    }


@app.post("/api/voice/patient-link", dependencies=[Depends(_check_survey_token)])
def voice_patient_link(body: PatientLinkRequest, response: Response) -> dict:
    """Issue the signed patient magic link for the voice agent to text.

    The voice agent never holds the signing secret; it exchanges the shared
    ingest token for the exact URL the patient page expects. Unknown patients
    are rejected rather than created so the phone call can close honestly.
    """

    return _issue_patient_link(body.patient_id, response)


@app.post(
    "/api/patients/{pid}/link", dependencies=[Depends(require_clinician)]
)
def clinician_patient_link(pid: str, response: Response) -> dict:
    """Let a signed-in clinician copy the same magic link the call texts."""

    return _issue_patient_link(pid, response)


@app.get("/api/patients", dependencies=[Depends(require_clinician)])
def list_patients(q: str | None = None) -> list[dict]:
    return store.list_patients(q)


@app.get(
    "/api/patients/{pid}", dependencies=[Depends(require_clinician)]
)
def get_patient(pid: str) -> dict:
    try:
        return store.get_patient(pid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _store_patient_session(pid: str, body: SessionPayload, *, require_active_correlation: bool = False) -> dict:
    if body.frames is not None:
        if len(body.frames) > 300:
            raise HTTPException(
                status_code=422,
                detail="frames are capped at 300 per session",
            )
        try:
            validate_frames(body.frames)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="each frame needs left/right hip, knee, ankle as [x, y, z]",
            ) from exc
    try:
        return store.add_session(
            pid,
            {
                "label": body.label,
                "source": body.source,
                "idempotency_key": body.idempotency_key,
                "metrics": body.metrics.model_dump(),
                "frames": body.frames,
                "call_id": body.call_id,
                "attempt_id": body.attempt_id,
                "recorded_at": (
                    body.recorded_at.isoformat()
                    if body.recorded_at
                    else None
                ),
            },
            require_active_correlation=require_active_correlation,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except store.Conflict:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except (OSError, TypeError) as exc:
        raise HTTPException(
            status_code=500, detail="failed to persist patient record"
        ) from exc


@app.post(
    "/api/patients/{pid}/sessions",
    dependencies=[Depends(require_clinician)],
)
def add_patient_session(pid: str, body: SessionPayload) -> dict:
    return _store_patient_session(pid, body)


PATIENT_SESSION_COOKIE = "sana_patient_session"
_DEFAULT_PATIENT_SESSION_TTL = 4 * 60 * 60


def _patient_access_denied() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="invalid or expired patient access",
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
    )


def _patient_session_ttl() -> int:
    raw = os.getenv("PATIENT_SESSION_TTL_SECONDS")
    if raw is None:
        return _DEFAULT_PATIENT_SESSION_TTL
    try:
        ttl = int(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=503, detail="patient access is not configured"
        ) from exc
    if not 60 <= ttl <= 7 * 24 * 60 * 60:
        raise HTTPException(
            status_code=503, detail="patient access is not configured"
        )
    return ttl


def _cookie_secure() -> bool:
    try:
        return clinician_auth.get_auth_config().cookie_secure
    except clinician_auth.AuthConfigurationError:
        raw = os.getenv("CLINICIAN_COOKIE_SECURE", "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}


def _verify_patient_token(token: str, pid: str) -> None:
    try:
        patient_access.verify_token(token, pid)
    except patient_access.PatientAccessConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="patient access is not configured"
        ) from exc
    except patient_access.PatientAccessError as exc:
        raise _patient_access_denied() from exc


def _require_patient_access(
    pid: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Accept either a bearer link token or the patient session cookie
    issued by /api/auth/verify."""

    parts = authorization.split() if authorization else []
    if len(parts) == 2 and parts[0].lower() == "bearer":
        _verify_patient_token(parts[1], pid)
        return
    cookie = request.cookies.get(PATIENT_SESSION_COOKIE)
    if not cookie:
        raise _patient_access_denied()
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        _check_browser_origin(request)
    _verify_patient_token(cookie, pid)


@app.get("/api/auth/verify")
def verify_patient_link(
    response: Response,
    patient_id: str = Query(min_length=1, max_length=64),
    token: str = Query(min_length=1, max_length=2048),
) -> dict:
    """Exchange a signed magic-link token for a patient session cookie."""

    response.headers["Cache-Control"] = "no-store"
    _verify_patient_token(token, patient_id)
    try:
        store.get_patient(patient_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="patient record not found"
        ) from exc
    ttl = _patient_session_ttl()
    try:
        session_token, expires_at = patient_access.generate_token(
            patient_id, ttl_seconds=ttl
        )
    except patient_access.PatientAccessConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="patient access is not configured"
        ) from exc
    response.set_cookie(
        key=PATIENT_SESSION_COOKIE,
        value=session_token,
        max_age=ttl,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )
    return {
        "authenticated": True,
        "patient_id": patient_id,
        "expires_at": int(expires_at.timestamp()),
    }


@app.delete("/api/auth/verify", status_code=204)
def end_patient_session(request: Request, response: Response) -> None:
    _check_browser_origin(request)
    response.delete_cookie(
        key=PATIENT_SESSION_COOKIE,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def _patient_view(record: dict) -> dict:
    """Return only fields needed by the patient walking-test experience."""

    return {
        "patient_id": record["patient_id"],
        "name": record.get("name"),
        "gait_sessions": record.get("gait_sessions", []),
    }


@app.get("/api/patient-access/{pid}")
def get_patient_with_access(
    pid: str,
    response: Response,
    _: None = Depends(_require_patient_access),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return _patient_view(store.get_patient(pid))
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="patient record not found"
        ) from exc


@app.post("/api/patient-access/{pid}/summary")
def patient_summary_with_access(
    pid: str,
    response: Response,
    _: None = Depends(_require_patient_access),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return _summary_for(pid)


@app.post("/api/patient-access/{pid}/sessions")
def add_patient_session_with_access(
    pid: str,
    body: SessionPayload,
    response: Response,
    _: None = Depends(_require_patient_access),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return _patient_view(_store_patient_session(pid, body, require_active_correlation=True))


app.include_router(create_router(require_clinician, _require_patient_access))


_PID_PATH = Path(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")


@app.post(
    "/api/patients/{pid}/ensure-demo",
    dependencies=[Depends(require_clinician)],
)
def ensure_demo_patient(pid: str = _PID_PATH) -> dict:
    allow_create = not os.getenv("SURVEY_INGEST_TOKEN") or os.getenv(
        "ALLOW_DEMO_PATIENTS", ""
    ).lower() in ("1", "true", "yes")
    survey = SurveyPayload(
        patient_id=pid,
        patient_name=pid,
        pain_scale=3,
        fall_history=FallHistory(
            falls_last_6_months=0,
            injured=False,
            last_fall_description=None,
        ),
        dizziness=False,
        primary_complaints=["Demo profile — auto-created for testing"],
        call_id="demo-auto",
    )
    try:
        record, created = store.ensure_patient(
            pid, survey.model_dump(mode="json"), allow_create=allow_create
        )
    except store.CreationDisabled as exc:
        raise HTTPException(
            status_code=403,
            detail=(
                "demo patient creation is disabled when survey ingestion is "
                "token-protected; set ALLOW_DEMO_PATIENTS=1 to enable"
            ),
        ) from exc
    except (OSError, TypeError) as exc:
        raise HTTPException(
            status_code=500, detail="failed to persist patient record"
        ) from exc
    return {"created": created, "patient": record}


@app.post(
    "/api/patients/{pid}/synthesis",
    dependencies=[Depends(require_clinician)],
)
def patient_synthesis(pid: str) -> dict:
    try:
        record = store.get_patient(pid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not record.get("surveys") or not record.get("gait_sessions"):
        raise HTTPException(
            status_code=422,
            detail="patient needs at least one survey and one gait session",
        )
    if record.get("active_call_id"):
        call = store.get_call(pid, record["active_call_id"])
        surveys = [
            survey for survey in record["surveys"]
            if survey.get("call_id") == call["call_id"]
        ]
        session_index = next((
            index for index, session in enumerate(record["gait_sessions"])
            if session.get("session_id") == call["walking"]["session_id"]
            and session.get("call_id") == call["call_id"]
            and session.get("attempt_id") == call["attempt_id"]
        ), None)
        if call["walking"]["status"] != "saved" or not surveys or session_index is None:
            raise HTTPException(
                status_code=422,
                detail="active call needs its confirmed survey and saved walking session",
            )
        record = {
            **record,
            "surveys": surveys,
            "gait_sessions": record["gait_sessions"][:session_index + 1],
        }
    return agent.generate_synthesis(record)

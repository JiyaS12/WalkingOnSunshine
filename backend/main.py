"""GaitGuard AI — Phase 1 FastAPI backend."""

from __future__ import annotations

import os
import secrets
import tempfile
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Path,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from starlette.concurrency import run_in_threadpool
from typing_extensions import Annotated

import agent
import clinician_auth
import patient_access
import store
from processor import GaitMetrics, GaitProcessor, validate_frames

# cv2/mediapipe are heavy native deps; a broken install (a non-headless
# OpenCV without libGL, say) must not take the rest of the API down with it
try:
    import video
except ImportError as exc:
    video = None
    _VIDEO_IMPORT_ERROR: str | None = str(exc)
else:
    _VIDEO_IMPORT_ERROR = None

app = FastAPI(title="GaitGuard AI")


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
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
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
        raise HTTPException(status_code=403, detail="request origin is not allowed")


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
        clinician_auth.record_failed_login(client_key, config)
        raise HTTPException(
            status_code=400, detail="invalid sign-in request"
        ) from None

    if not clinician_auth.credentials_match(
        credentials.username, credentials.password, config
    ):
        clinician_auth.record_failed_login(client_key, config)
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


@app.post(
    "/api/generate-summary", dependencies=[Depends(require_clinician)]
)
def generate_summary(body: SummaryRequest) -> dict:
    try:
        record = store.get_patient(body.patient_id)
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
        body.patient_id,
    )


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

        try:
            frames, effective_fps, total = await run_in_threadpool(
                video.extract_frames, tmp.name
            )
            metrics = await run_in_threadpool(
                lambda: GaitProcessor(frames, fps=effective_fps).compute()
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
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
    falls_last_6_months: int = Field(ge=0)
    injured: bool = False
    last_fall_description: str | None = Field(default=None, max_length=500)


class SurveyPayload(BaseModel):
    patient_id: str = Field(
        min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"
    )
    patient_name: str | None = Field(default=None, max_length=500)
    pain_scale: int = Field(ge=1, le=10)
    fall_history: FallHistory
    dizziness: bool
    dizziness_notes: str | None = Field(default=None, max_length=500)
    primary_complaints: list[Annotated[str, StringConstraints(max_length=120)]] = Field(min_length=1, max_length=10)
    call_id: str | None = Field(default=None, max_length=64)
    recorded_at: datetime | None = None


class SessionPayload(BaseModel):
    label: str = Field(max_length=64)
    source: str = Field(max_length=32)
    metrics: GaitMetrics
    frames: list[dict[str, list[float]]] | None = None
    recorded_at: datetime | None = None


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
def submit_survey(body: SurveyPayload, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        link = patient_access.create_patient_link(body.patient_id)
        return {
            "status": "stored",
            "patient": store.upsert_survey(body.model_dump(mode="json")),
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


def _store_patient_session(pid: str, body: SessionPayload) -> dict:
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
                "metrics": body.metrics.model_dump(),
                "frames": body.frames,
                "recorded_at": (
                    body.recorded_at.isoformat()
                    if body.recorded_at
                    else None
                ),
            },
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
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


def _require_patient_access(
    pid: str, authorization: str | None = Header(default=None)
) -> None:
    parts = authorization.split() if authorization else []
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="invalid or expired patient access",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        patient_access.verify_token(parts[1], pid)
    except patient_access.PatientAccessConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="patient access is not configured"
        ) from exc
    except patient_access.PatientAccessError as exc:
        raise HTTPException(
            status_code=401,
            detail="invalid or expired patient access",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


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


@app.post("/api/patient-access/{pid}/sessions")
def add_patient_session_with_access(
    pid: str,
    body: SessionPayload,
    response: Response,
    _: None = Depends(_require_patient_access),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return _patient_view(_store_patient_session(pid, body))


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
    return agent.generate_synthesis(record)

"""GaitGuard AI — Phase 1 FastAPI backend."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime

from fastapi import Depends, FastAPI, File, Header, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StringConstraints
from starlette.concurrency import run_in_threadpool
from typing_extensions import Annotated

import agent
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.post("/api/generate-summary")
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


@app.get("/api/summary-cache-stats")
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
    if token and x_survey_token != token:
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


@app.get("/api/patients")
def list_patients(q: str | None = None) -> list[dict]:
    return store.list_patients(q)


@app.get("/api/patients/{pid}")
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


@app.post("/api/patients/{pid}/sessions")
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


@app.post("/api/patients/{pid}/synthesis")
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

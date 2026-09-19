"""GaitGuard AI — Phase 1 FastAPI backend."""

from __future__ import annotations

import os
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

import agent
import simulator
import store
from processor import GaitMetrics, GaitProcessor, validate_frames

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
    patient_id: str | None = None
    metrics_day1: GaitMetrics | None = None
    metrics_day14: GaitMetrics | None = None


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


@app.get("/api/get-simulation")
def get_simulation(day: int | None = None) -> dict:
    try:
        if day is None:
            return simulator.get_comparison()
        return simulator.get_simulation(day)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/generate-summary")
def generate_summary(body: SummaryRequest | None = None) -> dict:
    comparison = simulator.get_comparison()
    patient_id = (body.patient_id if body else None) or comparison["patient_id"]
    day1 = (
        body.metrics_day1.model_dump() if body and body.metrics_day1
        else comparison["day_1"]
    )
    day14 = (
        body.metrics_day14.model_dump() if body and body.metrics_day14
        else comparison["day_14"]
    )
    return agent.generate_summary(day1, day14, patient_id)


@app.get("/api/summary-cache-stats")
def summary_cache_stats() -> dict:
    return agent.cache_stats()


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
def submit_survey(body: SurveyPayload) -> dict:
    try:
        return {
            "status": "stored",
            "patient": store.upsert_survey(body.model_dump(mode="json")),
        }
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


@app.post("/api/patients/{pid}/sessions")
def add_patient_session(pid: str, body: SessionPayload) -> dict:
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

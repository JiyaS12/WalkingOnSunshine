"""GaitGuard AI — Phase 1 FastAPI backend."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import agent
import simulator
from processor import GaitMetrics, GaitProcessor

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
        return GaitProcessor(body.frames, fps=body.fps).compute()
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

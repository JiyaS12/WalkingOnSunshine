"""GaitGuard AI — Phase 1 FastAPI backend."""

from __future__ import annotations

import os
import tempfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import agent
import simulator
import video
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


_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
_MAX_VIDEO_BYTES = 100 * 1024 * 1024


@app.post("/api/process-video")
async def process_video(file: UploadFile = File(...)) -> dict:
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
        "filename": file.filename,
    }

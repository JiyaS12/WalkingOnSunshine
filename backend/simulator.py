"""Loads the mock cohort dataset and runs the gait processor on sessions."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from processor import GaitProcessor

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "mock_cohort.json"

_cohorts: dict[str, dict] = {}


def load_cohort(path: Path | str = DATA_PATH) -> dict:
    key = str(Path(path).resolve())
    if key not in _cohorts:
        with open(path) as f:
            _cohorts[key] = json.load(f)
    return _cohorts[key]


def _session_result(session: dict) -> dict:
    metrics = GaitProcessor(session["frames"], fps=session["fps"]).compute()
    return {
        "day": session["day"],
        "fps": session["fps"],
        "frame_count": len(session["frames"]),
        "metrics": metrics.model_dump(),
        "frames": copy.deepcopy(session["frames"]),
    }


def get_simulation(day: int | None = None) -> dict:
    cohort = load_cohort()
    sessions = cohort["sessions"]
    results = {
        name: _session_result(session)
        for name, session in sessions.items()
        if day is None or session["day"] == day
    }
    if day is not None:
        for result in results.values():
            return result
        raise KeyError(f"no session for day {day}")
    return {"patient_id": cohort["patient_id"], "sessions": results}


def get_comparison() -> dict:
    cohort = load_cohort()
    sessions = cohort["sessions"]
    day1 = _session_result(sessions["day_1"])
    day14 = _session_result(sessions["day_14"])
    m1, m14 = day1["metrics"], day14["metrics"]
    deltas = {
        key: round(m14[key] - m1[key], 4)
        for key in m1
        if isinstance(m1[key], (int, float))
    }
    return {
        "patient_id": cohort["patient_id"],
        "cohort": cohort["cohort"],
        "day_1": m1,
        "day_14": m14,
        "deltas": deltas,
    }

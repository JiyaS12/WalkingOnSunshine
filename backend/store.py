"""Patient record store for GaitGuard AI.

JSON-persisted at backend/.cache/patients.json; seeded on first load from
data/mock_patients.json. Session metrics for the seeded cohort patient are
computed at load time from the simulator (metrics stay null in the JSON).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import simulator

CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "patients.json"
SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "mock_patients.json"

_MAX_TEXT = 500
_TEXT_FIELDS = ("patient_name", "last_fall_description", "dizziness_notes")

_patients: dict[str, dict] | None = None
_cache_path = CACHE_PATH


def _persist() -> None:
    try:
        _cache_path.parent.mkdir(parents=True, exist_ok=True)
        _cache_path.write_text(json.dumps(_patients))
    except Exception:
        pass


def _fill_seed_metrics(record: dict) -> dict:
    """RGN-0417's seeded sessions carry frames_ref; compute metrics from the
    cohort simulator once on load."""
    for session in record.get("gait_sessions", []):
        ref = session.get("frames_ref")
        if ref and session.get("metrics") is None:
            day = simulator.load_cohort()["sessions"][ref]["day"]
            session["metrics"] = simulator.get_simulation(day)["metrics"]
    return record


def _load() -> dict:
    global _patients
    if _patients is not None:
        return _patients
    try:
        _patients = json.loads(_cache_path.read_text())
    except Exception:
        _patients = {
            p["patient_id"]: _fill_seed_metrics(p)
            for p in json.loads(SEED_PATH.read_text())
        }
        _persist()
    return _patients


def _summary_row(record: dict) -> dict:
    surveys = record.get("surveys", [])
    sessions = record.get("gait_sessions", [])
    latest_survey = surveys[-1] if surveys else None
    latest_session = sessions[-1] if sessions else None
    metrics = (latest_session or {}).get("metrics") or {}
    return {
        "patient_id": record["patient_id"],
        "name": record.get("name"),
        "age": record.get("age"),
        "latest_survey_at": (latest_survey or {}).get("recorded_at"),
        "pain_scale": (latest_survey or {}).get("pain_scale"),
        "dizziness": (latest_survey or {}).get("dizziness"),
        "falls_last_6_months": ((latest_survey or {}).get("fall_history") or {})
        .get("falls_last_6_months"),
        "latest_fall_risk": metrics.get("fall_risk_score"),
        "latest_asymmetry_pct": metrics.get("asymmetry_pct"),
        "primary_complaints": (latest_survey or {}).get(
            "primary_complaints", []
        ),
    }


def list_patients(q: str | None = None) -> list[dict]:
    rows = [_summary_row(r) for r in _load().values()]
    if q:
        ql = q.lower()
        rows = [
            r
            for r in rows
            if ql in r["patient_id"].lower()
            or ql in (r.get("name") or "").lower()
            or any(ql in c.lower() for c in r["primary_complaints"])
        ]
    return rows


def get_patient(pid: str) -> dict:
    patients = _load()
    if pid not in patients:
        raise KeyError(f"unknown patient: {pid}")
    record = json.loads(json.dumps(patients[pid]))  # deep copy
    sessions = simulator.load_cohort()["sessions"]
    for session in record.get("gait_sessions", []):
        ref = session.get("frames_ref")
        if ref and ref in sessions:
            session["frames"] = sessions[ref]["frames"]
    return record


def upsert_survey(survey: dict) -> dict:
    patients = _load()
    pid = survey["patient_id"]
    for holder in (survey, survey.get("fall_history") or {}):
        for field in _TEXT_FIELDS:
            if isinstance(holder.get(field), str):
                holder[field] = holder[field].strip()[:_MAX_TEXT]
    if survey.get("recorded_at") is None:
        survey["recorded_at"] = datetime.now(timezone.utc).isoformat()
    record = patients.get(pid)
    if record is None:
        record = {
            "patient_id": pid,
            "name": survey.get("patient_name") or pid,
            "age": None,
            "cohort": None,
            "surveys": [],
            "gait_sessions": [],
        }
        patients[pid] = record
    elif survey.get("patient_name"):
        record["name"] = survey["patient_name"]
    record["surveys"].append(survey)
    _persist()
    return record


def add_session(pid: str, session: dict) -> dict:
    patients = _load()
    if pid not in patients:
        raise KeyError(f"unknown patient: {pid}")
    if session.get("recorded_at") is None:
        session["recorded_at"] = datetime.now(timezone.utc).isoformat()
    session.setdefault("frames_ref", None)
    session.setdefault("frames", None)
    patients[pid]["gait_sessions"].append(session)
    _persist()
    return patients[pid]


def reset_for_tests(path: Path | str) -> None:
    global _patients, _cache_path
    _patients = None
    _cache_path = Path(path)

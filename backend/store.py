"""Patient record store for Sana.

JSON-persisted at backend/.cache/patients.json; seeded on first load from
data/mock_patients.json.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "patients.json"
SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "mock_patients.json"

_MAX_TEXT = 500
_MAX_SESSIONS = 50
_TEXT_FIELDS = ("patient_name", "last_fall_description", "dizziness_notes")

_patients: dict[str, dict] | None = None
_cache_path = CACHE_PATH
_lock = threading.Lock()


def _persist() -> None:
    _cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _cache_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_patients))
    os.replace(tmp, _cache_path)


def _load() -> dict:
    global _patients
    if _patients is not None:
        return _patients
    seeds = {p["patient_id"]: p for p in json.loads(SEED_PATH.read_text())}
    try:
        cached = json.loads(_cache_path.read_text())
    except Exception:
        _patients = seeds
        _persist()
        return _patients
    # Seed patients added after the cache was written are merged in; existing
    # records (and their surveys/sessions) are never overwritten.
    missing = {pid: p for pid, p in seeds.items() if pid not in cached}
    _patients = {**cached, **missing}
    if missing:
        _persist()
    return _patients


def _sort_by_recorded_at(items: list[dict]) -> None:
    """Sort in place by recorded_at; entries missing/invalid timestamps keep
    their append position (stable sort past the end)."""
    def key(item: dict) -> tuple[int, datetime]:
        raw = item.get("recorded_at")
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (0, dt)
        except (TypeError, ValueError):
            return (1, datetime.max.replace(tzinfo=timezone.utc))

    items.sort(key=key)


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
    with _lock:
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
    with _lock:
        patients = _load()
        if pid not in patients:
            raise KeyError(f"unknown patient: {pid}")
        return json.loads(json.dumps(patients[pid]))  # deep copy


class CreationDisabled(Exception):
    """Raised when a new patient record would be created but creation is disabled."""


def _append_survey_locked(patients: dict, survey: dict) -> dict:
    """Mutate `patients` to append `survey`; caller must hold `_lock`.

    Builds on a deep copy and persists; on persist failure the original
    record is restored (or deleted if newly created) and the error
    propagates.
    """
    pid = survey["patient_id"]
    old_record = patients.get(pid)
    created = old_record is None
    if created:
        new_record = {
            "patient_id": pid,
            "name": survey.get("patient_name") or pid,
            "age": None,
            "cohort": None,
            "surveys": [],
            "gait_sessions": [],
        }
    else:
        new_record = json.loads(json.dumps(old_record))  # deep copy
        if survey.get("patient_name"):
            new_record["name"] = survey["patient_name"]
    new_record["surveys"].append(survey)
    _sort_by_recorded_at(new_record["surveys"])
    patients[pid] = new_record
    try:
        _persist()
    except Exception:
        if created:
            del patients[pid]
        else:
            patients[pid] = old_record
        raise
    return new_record


def upsert_survey(survey: dict) -> dict:
    pid = survey["patient_id"]
    for field in _TEXT_FIELDS:
        if isinstance(survey.get(field), str):
            survey[field] = survey[field].strip()[:_MAX_TEXT]
    if survey.get("recorded_at") is None:
        survey["recorded_at"] = datetime.now(timezone.utc).isoformat()
    with _lock:
        patients = _load()
        return _append_survey_locked(patients, survey)


def ensure_patient(
    pid: str, survey: dict, allow_create: bool = True
) -> tuple[dict, bool]:
    """Return (record, created). Under a single lock acquisition, returns the
    existing record without mutating if `pid` exists; otherwise appends
    `survey` exactly as `upsert_survey` would for a new patient.
    """
    survey = dict(survey)
    survey["patient_id"] = pid
    for field in _TEXT_FIELDS:
        if isinstance(survey.get(field), str):
            survey[field] = survey[field].strip()[:_MAX_TEXT]
    if survey.get("recorded_at") is None:
        survey["recorded_at"] = datetime.now(timezone.utc).isoformat()
    with _lock:
        patients = _load()
        if pid in patients:
            return json.loads(json.dumps(patients[pid])), False
        if not allow_create:
            raise CreationDisabled(pid)
        return _append_survey_locked(patients, survey), True


def add_session(pid: str, session: dict) -> dict:
    with _lock:
        patients = _load()
        if pid not in patients:
            raise KeyError(f"unknown patient: {pid}")
        old_record = patients[pid]
        if len(old_record["gait_sessions"]) >= _MAX_SESSIONS:
            raise ValueError("patient already has 50 sessions")
        if session.get("recorded_at") is None:
            session["recorded_at"] = datetime.now(timezone.utc).isoformat()
        session.setdefault("frames", None)
        new_record = json.loads(json.dumps(old_record))  # deep copy
        new_record["gait_sessions"].append(session)
        _sort_by_recorded_at(new_record["gait_sessions"])
        patients[pid] = new_record
        try:
            _persist()
        except Exception:
            patients[pid] = old_record
            raise
        return new_record


def reset_for_tests(path: Path | str) -> None:
    global _patients, _cache_path
    _patients = None
    _cache_path = Path(path)

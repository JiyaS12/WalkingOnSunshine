"""Patient record store for Sana.

JSON-persisted at backend/.cache/patients.json; seeded on first load from
data/mock_patients.json.
"""

from __future__ import annotations

import json
import hashlib
import os
import threading
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "patients.json"
SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "mock_patients.json"

_MAX_TEXT = 500
_MAX_SESSIONS = 50
_TEXT_FIELDS = ("patient_name", "last_fall_description", "dizziness_notes")

_patients: dict[str, dict] | None = None
_cache_path = CACHE_PATH
_lock = threading.Lock()
_MAX_CALLS = 100
_MAX_EVENTS = 200


class Conflict(ValueError):
    """An idempotency key, scope, or state transition conflicts with stored data."""


class StoreUnavailable(RuntimeError):
    """The authoritative store exists but cannot be loaded safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _public_call(call: dict) -> dict:
    result = deepcopy(call)
    result.pop("_fingerprint", None)
    result.pop("_phone_snapshot", None)
    return result


def _public_record(record: dict) -> dict:
    result = deepcopy(record)
    result.setdefault("condition_category", None)
    result.setdefault("condition_source", None)
    result.setdefault("active_call_id", None)
    result.setdefault("calls", [])
    for survey in result.get("surveys", []):
        survey.pop("_fingerprint", None)
    if "calls" in result:
        result["calls"] = [_public_call(call) for call in result["calls"]]
    return result


def _commit(patients: dict, pid: str, record: dict) -> None:
    old = patients.get(pid)
    patients[pid] = record
    try:
        _persist()
    except Exception:
        if old is None:
            del patients[pid]
        else:
            patients[pid] = old
        raise


def _mutate_patient(pid: str, mutate: Callable[[dict], None]) -> dict:
    with _lock:
        patients = _load()
        if pid not in patients:
            raise KeyError("patient record not found")
        record = deepcopy(patients[pid])
        mutate(record)
        _commit(patients, pid, record)
        return _public_record(record)


def _persist() -> None:
    _cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _cache_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(_patients))
    os.replace(tmp, _cache_path)


def _load() -> dict:
    global _patients
    if _patients is not None:
        return _patients
    try:
        loaded = json.loads(_cache_path.read_text())
        if not isinstance(loaded, dict) or not all(isinstance(record, dict) for record in loaded.values()):
            raise StoreUnavailable("invalid patient store")
    except FileNotFoundError:
        _patients = _read_seeds()
        try:
            _persist()
        except Exception:
            _patients = None
            raise
        return _patients
    except (OSError, ValueError) as exc:
        raise StoreUnavailable("patient store could not be loaded") from exc
    # Seed patients added after the cache was written are merged in memory on
    # every load (the next mutation's _persist writes them through); existing
    # records are never overwritten, and a missing seed file never blocks
    # serving a valid cache.
    try:
        seeds = _read_seeds()
    except Exception:
        seeds = {}
    _patients = {**{pid: p for pid, p in seeds.items() if pid not in loaded}, **loaded}
    return _patients


def _read_seeds() -> dict[str, dict]:
    return {p["patient_id"]: p for p in json.loads(SEED_PATH.read_text())}


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
        "condition_category": record.get("condition_category"),
        "active_call_id": record.get("active_call_id"),
        "latest_survey_at": (latest_survey or {}).get("recorded_at"),
        "pain_scale": (latest_survey or {}).get("pain_scale"),
        "dizziness": (latest_survey or {}).get("dizziness"),
        "falls_last_6_months": ((latest_survey or {}).get("fall_history") or {})
        .get("falls_last_6_months"),
        "latest_fall_risk": metrics.get("fall_risk_score"),
        "latest_asymmetry_pct": metrics.get("asymmetry_pct"),
        "primary_complaints": (latest_survey or {}).get("primary_complaints") or [],
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
        return _public_record(patients[pid])


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
    if survey.get("submission_kind") == "integrated":
        call = _find_call(new_record, survey["call_id"])
        if new_record.get("active_call_id") != call["call_id"]:
            raise Conflict("survey call is no longer active")
        if call["condition_category"] != survey["condition_survey"]["condition_category"]:
            raise Conflict("survey condition does not match clinician-selected call condition")
        if call["survey_status"] in {"stopped", "needs_review"}:
            raise Conflict("survey is already terminal")
        call["survey_status"] = "stored"
        call["survey_id"] = survey["survey_id"]
        _touch(call)
    _commit(patients, pid, new_record)
    return _public_record(new_record)


def upsert_survey(survey: dict) -> dict:
    survey = deepcopy(survey)
    pid = survey["patient_id"]
    for field in _TEXT_FIELDS:
        if isinstance(survey.get(field), str):
            survey[field] = survey[field].strip()[:_MAX_TEXT]
    with _lock:
        patients = _load()
        record = patients.get(pid, {})
        call_id = survey.get("call_id")
        fingerprint = _fingerprint(survey)
        if call_id:
            for existing in record.get("surveys", []):
                if existing.get("call_id") == call_id:
                    if existing.get("_fingerprint") != fingerprint:
                        raise Conflict("call_id already has a different survey")
                    return _public_record(record)
        known_call = next(
            (call for call in record.get("calls", []) if call["call_id"] == call_id), None
        )
        if known_call and survey.get("submission_kind") != "integrated":
            raise Conflict("registered calls require a complete integrated survey")
        if survey.get("submission_kind") == "integrated":
            if not record:
                raise KeyError("patient record not found")
            if not call_id:
                raise Conflict("integrated surveys require a registered call")
            _find_call(record, call_id)
        survey["_fingerprint"] = fingerprint
        survey["survey_id"] = str(uuid4())
        if survey.get("recorded_at") is None:
            survey["recorded_at"] = _now()
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


def add_session(pid: str, session: dict, *, require_active_correlation: bool = False) -> dict:
    session = deepcopy(session)
    with _lock:
        patients = _load()
        if pid not in patients:
            raise KeyError(f"unknown patient: {pid}")
        old_record = patients[pid]
        if require_active_correlation and old_record.get("active_call_id") and not session.get("call_id"):
            raise Conflict("active calls require call_id and attempt_id on session saves")
        idempotency_key = session.get("idempotency_key")
        for item in old_record["gait_sessions"]:
            if idempotency_key is not None and item.get("idempotency_key") == idempotency_key:
                if item.get("call_id") or session.get("call_id"):
                    candidate = dict(session)
                    if candidate.get("recorded_at") is None:
                        candidate["recorded_at"] = item["recorded_at"]
                    stored = {key: value for key, value in item.items() if key != "session_id"}
                    if candidate != stored:
                        raise Conflict("idempotency_key already has a different walking session")
                return _public_record(old_record)
        if len(old_record["gait_sessions"]) >= _MAX_SESSIONS:
            raise ValueError("patient already has 50 sessions")
        if session.get("recorded_at") is None:
            session["recorded_at"] = datetime.now(timezone.utc).isoformat()
        session.setdefault("frames", None)
        new_record = json.loads(json.dumps(old_record))  # deep copy
        session["session_id"] = str(uuid4())
        if session.get("call_id"):
            call = _active_attempt(new_record, session["call_id"], session["attempt_id"])
            if call["survey_status"] != "stored":
                raise Conflict("walking requires a stored survey")
            if call["walking"]["status"] in {"saved", "stopped"}:
                raise Conflict("walking attempt is already terminal")
            if not session["metrics"].get("gait_detected"):
                raise Conflict("a saved walking attempt requires detected gait")
            call["walking"]["status"] = "saved"
            call["walking"]["session_id"] = session["session_id"]
            _touch(call)
        new_record["gait_sessions"].append(session)
        _sort_by_recorded_at(new_record["gait_sessions"])
        _commit(patients, pid, new_record)
        return _public_record(new_record)


def _find_call(record: dict, call_id: str) -> dict:
    for call in record.get("calls", []):
        if call["call_id"] == call_id:
            return call
    raise KeyError("call record not found")


def get_call(pid: str, call_id: str) -> dict:
    return _find_call(get_patient(pid), call_id)


def _touch(call: dict) -> None:
    call["version"] += 1
    call["updated_at"] = _now()


def set_condition(pid: str, category: str) -> dict:
    def update(record: dict) -> None:
        record["condition_category"] = category
        record["condition_source"] = "clinician"
    return _mutate_patient(pid, update)


def reserve_call(pid: str, request_id: str, category: str | None, fingerprint: str) -> tuple[dict, bool]:
    with _lock:
        patients = _load()
        if pid not in patients:
            raise KeyError("patient record not found")
        record = deepcopy(patients[pid])
        calls = record.setdefault("calls", [])
        for call in calls:
            if call["request_id"] == request_id:
                if call["_fingerprint"] != fingerprint:
                    raise Conflict("request_id already has a different call request")
                return _public_call(call), False
        category = category or record.get("condition_category")
        if category not in {"orthopedic", "stroke"}:
            raise Conflict("condition_category must be supplied by a clinician")
        if any(call["call_status"] not in {"completed", "failed", "stopped"} for call in calls):
            raise Conflict("patient already has an active or unresolved call")
        if len(calls) >= _MAX_CALLS:
            raise Conflict("patient already has 100 calls")
        now = _now()
        call = {
            "call_id": str(uuid4()), "patient_id": pid, "request_id": request_id,
            "attempt_id": str(uuid4()), "condition_category": category,
            "call_status": "dispatching", "survey_status": "pending", "survey_id": None,
            "sms_status": "not_requested", "sms_attempt": 0, "sms_retries": [],
            "provider_call_id": None, "phone_session_id": None, "message_id": None,
            "error_code": None, "version": 1, "phone_version": 0,
            "created_at": now, "updated_at": now, "_fingerprint": fingerprint,
            "walking": {
                "status": "waiting", "last_sequence": 0, "last_event": None,
                "session_id": None, "events": [],
            },
        }
        calls.append(call)
        record["active_call_id"] = call["call_id"]
        record["condition_category"] = category
        record["condition_source"] = "clinician"
        _commit(patients, pid, record)
        return _public_call(call), True


_CALL_TRANSITIONS = {
    "dispatching": {"unknown", "dialing", "in_progress", "completed", "failed", "stopped"},
    "unknown": {"dialing", "in_progress", "completed", "failed", "stopped"},
    "dialing": {"in_progress", "completed", "failed", "stopped"},
    "in_progress": {"completed", "failed", "stopped"},
    "completed": set(), "failed": set(), "stopped": set(),
}
_SURVEY_TRANSITIONS = {
    "pending": {"in_progress", "stopped", "needs_review"},
    "in_progress": {"stopped", "needs_review"},
    "stored": set(), "stopped": set(), "needs_review": set(),
}
_SMS_TRANSITIONS = {
    "not_requested": {"sending", "unknown", "sent", "delivered", "failed"},
    "sending": {"unknown", "sent", "delivered", "failed"},
    "unknown": {"sent", "delivered", "failed"},
    "sent": {"delivered", "failed"}, "delivered": set(), "failed": set(),
}


def _transition(current: str, target: str, allowed: dict) -> str:
    if target == current:
        return current
    if target not in allowed[current]:
        raise Conflict(f"invalid transition from {current} to {target}")
    return target


def apply_phone_snapshot(pid: str, call_id: str, snapshot: dict) -> dict:
    def update(record: dict) -> None:
        call = _find_call(record, call_id)
        if snapshot["patient_id"] != pid or snapshot["call_id"] != call_id:
            raise Conflict("phone snapshot scope mismatch")
        if snapshot["version"] < call["phone_version"]:
            return
        if snapshot["version"] == call["phone_version"]:
            if snapshot != call.get("_phone_snapshot"):
                raise Conflict("phone version already has a different snapshot")
            return
        call["call_status"] = _transition(call["call_status"], snapshot["call_status"], _CALL_TRANSITIONS)
        if snapshot["survey_status"] == "stored":
            if call["survey_status"] != "stored":
                raise Conflict("only survey ingestion can mark a survey stored")
        elif call["survey_status"] != "stored":
            call["survey_status"] = _transition(call["survey_status"], snapshot["survey_status"], _SURVEY_TRANSITIONS)
        if snapshot["sms_attempt"] > call["sms_attempt"]:
            raise Conflict("SMS attempt has not been reserved")
        if snapshot["sms_attempt"] == call["sms_attempt"]:
            if snapshot["sms_status"] != "not_requested" and call["survey_status"] != "stored":
                raise Conflict("SMS requires a stored survey")
            call["sms_status"] = _transition(call["sms_status"], snapshot["sms_status"], _SMS_TRANSITIONS)
            message_id = snapshot.get("message_id")
            if message_id:
                if call["message_id"] and call["message_id"] != message_id:
                    raise Conflict("message identifier cannot change within an SMS attempt")
                call["message_id"] = message_id
        for key in ("provider_call_id", "phone_session_id"):
            value = snapshot.get(key)
            if value:
                if call[key] and call[key] != value:
                    raise Conflict("phone identifiers cannot change")
                call[key] = value
        call["error_code"] = snapshot.get("error_code")
        call["phone_version"] = snapshot["version"]
        call["_phone_snapshot"] = snapshot
        _touch(call)
    return _find_call(_mutate_patient(pid, update), call_id)


def mark_dispatch_unknown(pid: str, call_id: str, sms_attempt: int | None = None) -> dict:
    def update(record: dict) -> None:
        call = _find_call(record, call_id)
        if sms_attempt is None and call["call_status"] == "dispatching":
            call["call_status"] = "unknown"
        elif sms_attempt == call["sms_attempt"] and call["sms_status"] == "sending":
            call["sms_status"] = "unknown"
        else:
            return
        _touch(call)
    return _find_call(_mutate_patient(pid, update), call_id)


def reserve_sms_retry(pid: str, call_id: str, request_id: str) -> tuple[dict, bool]:
    created = False
    def update(record: dict) -> None:
        nonlocal created
        call = _find_call(record, call_id)
        if any(retry["request_id"] == request_id for retry in call["sms_retries"]):
            return
        if record.get("active_call_id") != call_id:
            raise Conflict("call is no longer active")
        if call["survey_status"] != "stored" or call["sms_status"] != "failed":
            raise Conflict("SMS retry requires a stored survey and definitively failed SMS")
        if call["sms_attempt"] >= 5:
            raise Conflict("SMS retry limit reached")
        call["sms_attempt"] += 1
        call["sms_status"] = "sending"
        call["message_id"] = None
        call["sms_retries"].append({
            "request_id": request_id, "sms_attempt": call["sms_attempt"], "created_at": _now(),
        })
        _touch(call)
        created = True
    return _find_call(_mutate_patient(pid, update), call_id), created


def _active_attempt(record: dict, call_id: str, attempt_id: str) -> dict:
    call = _find_call(record, call_id)
    if record.get("active_call_id") != call_id or call["attempt_id"] != attempt_id:
        raise Conflict("walking attempt is not active for this patient")
    return call


def walking_view(call: dict) -> dict:
    return {
        "call_id": call["call_id"], "attempt_id": call["attempt_id"],
        "version": call["version"], "survey_status": call["survey_status"],
        **{key: value for key, value in call["walking"].items() if key != "events"},
    }


_WALK_PROGRESS = {
    "page_ready": "page_ready", "calibration_started": "calibrating",
    "calibration_completed": "ready", "capture_started": "capturing",
    "capture_completed": "captured",
}
_WALK_RANK = {name: rank for rank, name in enumerate(
    ("waiting", "page_ready", "calibrating", "ready", "capturing", "captured", "saved")
)}


def add_walking_event(pid: str, event: dict) -> dict:
    def update(record: dict) -> None:
        call = _active_attempt(record, event["call_id"], event["attempt_id"])
        if call["survey_status"] != "stored":
            raise Conflict("walking requires a stored survey")
        walking = call["walking"]
        for existing in walking["events"]:
            if existing["event_id"] == event["event_id"]:
                if {key: value for key, value in existing.items() if key != "received_at"} != event:
                    raise Conflict("event_id already has a different event")
                return
        if event["sequence"] <= walking["last_sequence"]:
            return
        if walking["status"] in {"saved", "stopped"}:
            return
        if len(walking["events"]) >= _MAX_EVENTS:
            raise Conflict("walking event limit reached")
        target = _WALK_PROGRESS.get(event["event"])
        if target and _WALK_RANK[target] > _WALK_RANK[walking["status"]]:
            walking["status"] = target
        if event["event"] == "stopped":
            walking["status"] = "stopped"
        walking["last_sequence"] = event["sequence"]
        walking["last_event"] = event["event"]
        walking["events"].append({**event, "received_at": _now()})
        _touch(call)
    return walking_view(_find_call(_mutate_patient(pid, update), event["call_id"]))


def reset_for_tests(path: Path | str) -> None:
    global _patients, _cache_path
    _patients = None
    _cache_path = Path(path)

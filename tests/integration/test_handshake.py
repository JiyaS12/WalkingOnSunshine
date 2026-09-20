"""Authenticated HTTP handshake across the two separate Python environments."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "tests"))
import gait_gen  # noqa: E402

OPERATOR = "offline-operator-token-32-characters"
INGEST = "offline-ingest-token-32-characters"
PASSWORD = "offline-clinician-password"
SERVICE_HEADERS = {"X-Survey-Token": INGEST}


def wait_ready(client: httpx.Client, path: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert process.poll() is None, "Fixture process exited; inspect process log"
        try:
            if client.get(path).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    pytest.fail("Fixture process did not become ready")


@pytest.fixture
def services(tmp_path):
    main_socket, phone_socket = socket.socket(), socket.socket()
    for listener in (main_socket, phone_socket):
        listener.bind(("127.0.0.1", 0))
    main_url = f"http://127.0.0.1:{main_socket.getsockname()[1]}"
    phone_url = f"http://127.0.0.1:{phone_socket.getsockname()[1]}"
    store_path = tmp_path / "patients.json"
    store_path.write_text(json.dumps({
        pid: {"patient_id": pid, "name": "Synthetic patient", "surveys": [], "gait_sessions": []}
        for pid in ("RGN-9001", "RGN-9002")
    }))
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PYTHONUNBUFFERED": "1",
        "OPENAI_API_KEY": "",
        "SURVEY_EXTRACTOR": "exact",
        "SURVEY_INGEST_TOKEN": INGEST,
        "OPERATOR_TOKEN": OPERATOR,
        "PATIENT_LINK_SIGNING_SECRET": "offline-link-signing-secret-32-characters",
        "PATIENT_APP_BASE_URL": "http://localhost:3000",
        "CLINICIAN_USERNAME": "offline",
        "CLINICIAN_PASSWORD": PASSWORD,
        "CLINICIAN_SESSION_SECRET": "offline-session-secret-32-characters",
        "CLINICIAN_COOKIE_SECURE": "false",
        "FIXTURE_STORE": str(store_path),
        "FIXTURE_CACHE": str(tmp_path / "summaries.json"),
        "PHONE_RECEIPTS_PATH": str(tmp_path / "receipts.sqlite3"),
        "PHONE_SERVICE_BASE_URL": phone_url,
        "MAIN_BACKEND_URL": main_url,
        "PUBLIC_BASE_URL": phone_url,
    }
    processes = []
    logs = []
    try:
        for name, folder, listener in (
            ("main_fixture", "backend", main_socket),
            ("phone_fixture", "voice_agent", phone_socket),
        ):
            python = ROOT / folder / ".venv/bin/python"
            assert python.is_file(), f"Install the separate {folder} environment"
            log = (tmp_path / f"{name}.log").open("w")
            logs.append(log)
            processes.append(subprocess.Popen(
                [str(python), "-m", "uvicorn", f"{name}:app",
                 "--fd", str(listener.fileno()), "--no-access-log"],
                cwd=tmp_path,
                env={**env, "PYTHONPATH": os.pathsep.join((
                    str(ROOT / "tests/integration"), str(ROOT / folder),
                ))},
                pass_fds=(listener.fileno(),), stdout=log, stderr=subprocess.STDOUT,
            ))
        with (
            httpx.Client(base_url=main_url, trust_env=False, timeout=10) as main,
            httpx.Client(base_url=phone_url, trust_env=False, timeout=10,
                         headers={"Authorization": f"Bearer {OPERATOR}"}) as phone,
        ):
            wait_ready(main, "/api/health", processes[0])
            wait_ready(phone, "/api/config", processes[1])
            yield main, phone, store_path
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()
        main_socket.close()
        phone_socket.close()


def ok(response: httpx.Response):
    assert response.status_code == 200, response.text
    return response.json()


def poll(phone: httpx.Client, call_id: str, predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = ok(phone.get(f"/fixture/calls/{call_id}"))
        if predicate(state):
            return state
        time.sleep(0.02)
    pytest.fail("Phone state did not converge")


@pytest.mark.parametrize(("condition", "sms_outcome", "unknown"), [
    ("orthopedic", "sent", False),
    ("stroke", "sent", True),
    ("orthopedic", "rejected", False),
    ("stroke", "unknown", False),
])
def test_real_main_phone_handshake(services, condition, sms_outcome, unknown):
    main, phone, store_path = services
    pid = "RGN-9001"
    calls_path = f"/api/patients/{pid}/calls"
    assert main.get(calls_path).status_code == 401
    assert main.get(f"/api/integration/patients/{pid}",
                    headers={"X-Survey-Token": "invalid"}).status_code == 401
    ok(main.post("/api/clinician/session", json={"username": "offline", "password": PASSWORD}))
    assert main.post(calls_path, json={
        "request_id": "call-request-0001", "to_number": "+15555550101",
    }).status_code == 409
    ok(main.patch(f"/api/patients/{pid}/condition", json={
        "condition_category": condition,
    }))
    ok(phone.post("/fixture/scenario", json={"sms_outcome": sms_outcome}))
    start = {"request_id": "call-request-0001", "to_number": "+15555550101"}
    call = ok(main.post(calls_path, json=start))["call"]
    call_id = call["call_id"]
    assert call["call_status"] == "dialing"
    assert ok(main.post(calls_path, json=start))["call"]["call_id"] == call_id
    assert phone.get(f"/api/calls/{call_id}", headers={
        "Authorization": "Bearer invalid",
    }).status_code == 401
    ok(phone.post(f"/fixture/calls/{call_id}/begin"))
    answers = ["unknown"] * 3 if unknown else ["7", "2", "yes"]
    for answer in answers:
        ok(phone.post(f"/fixture/calls/{call_id}/utterance", json={"text": answer}))
    for _ in range(6):
        ok(phone.post(f"/fixture/calls/{call_id}/utterance", json={"text": "mild"}))
    state = poll(phone, call_id, lambda row: row["snapshot"]["sms_status"] in {
        "sent", "failed", "unknown",
    })
    assert state["calls"] == 1
    assert state["messages"] == 1
    assert state["snapshot"]["sms_status"] == {
        "sent": "sent", "rejected": "failed", "unknown": "unknown",
    }[sms_outcome]
    payload = state["submission"]
    assert payload["pain_scale"] == (None if unknown else 7)
    assert payload["fall_history"]["falls_last_6_months"] == (None if unknown else 2)
    assert payload["fall_history"]["injured"] is None
    assert payload["fall_history"]["last_fall_description"] is None
    assert payload["dizziness"] == (None if unknown else True)
    assert payload["dizziness_notes"] is None
    assert payload["primary_complaints"] is None
    instrument = "hoos_jr" if condition == "orthopedic" else "stroke_mobility"
    assert payload["condition_survey"]["instrument"] == instrument
    assert len(payload["condition_survey"]["answers"]) == 6
    assert all(answer["confirmed"] for answer in payload["condition_survey"]["answers"])
    canonical = ok(main.post("/api/submit-survey", headers=SERVICE_HEADERS, json=payload))
    assert canonical["status"] == "stored"
    assert [word for word in state["sms_body"].split() if word.startswith("http")] == [state["canonical_url"]]
    changed = {**payload, "pain_scale": 6}
    assert main.post("/api/submit-survey", headers=SERVICE_HEADERS,
                     json=changed).status_code == 409
    snapshot = state["snapshot"]
    status_path = f"/api/integration/patients/{pid}/calls/{call_id}/status"
    ok(main.post(status_path, headers=SERVICE_HEADERS, json=snapshot))
    ok(main.post(status_path, headers=SERVICE_HEADERS, json={
        **snapshot, "version": 1, "call_status": "dialing",
    }))
    assert ok(main.get(f"{calls_path}/{call_id}"))["call"]["sms_status"] == snapshot["sms_status"]
    retry_path = f"{calls_path}/{call_id}/sms-retries"
    if sms_outcome == "rejected":
        ok(phone.post("/fixture/scenario", json={"sms_outcome": "sent"}))
        retry = ok(main.post(retry_path, json={"request_id": "sms-retry-000001"}))["call"]
        assert retry["sms_status"] == "sent"
        ok(main.post(retry_path, json={"request_id": "sms-retry-000001"}))
        assert ok(phone.get(f"/fixture/calls/{call_id}"))["messages"] == 2
    else:
        assert main.post(retry_path, json={"request_id": "sms-retry-000001"}).status_code == 409
    link = urlsplit(state["canonical_url"])
    assert link.path == f"/patient/{pid}"
    bearer = {"Authorization": "Bearer " + parse_qs(link.query)["token"][0]}
    walk_path = f"/api/patient-access/{pid}/walking"
    assert main.get(walk_path, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert main.get("/api/patient-access/RGN-9002/walking",
                    headers=bearer).status_code == 401
    walk = ok(main.get(walk_path, headers=bearer))["walking"]
    attempt_id = walk["attempt_id"]
    for sequence, event in enumerate((
        "page_ready", "calibration_started", "calibration_completed",
        "capture_started", "capture_completed",
    ), start=1):
        body = {"event_id": f"walk-event-00000{sequence}", "call_id": call_id,
                "attempt_id": attempt_id, "sequence": sequence, "event": event}
        if sequence == 1:
            assert main.post(walk_path + "/events", headers=bearer, json={
                **body, "attempt_id": "wrong-attempt",
            }).status_code == 409
        first = ok(main.post(walk_path + "/events", headers=bearer, json=body))
        duplicate = ok(main.post(walk_path + "/events", headers=bearer, json=body))
        assert duplicate == first
    stale = ok(main.post(walk_path + "/events", headers=bearer, json={
        **body, "event_id": "reordered-event-0001", "sequence": 2, "event": "page_ready",
    }))
    assert stale == duplicate
    assert stale["walking"]["status"] == "captured"
    assert stale["walking"]["session_id"] is None
    frames = gait_gen.recovered_session(seed=7)
    metrics = ok(main.post("/api/process-frame", json={
        "frames": frames["frames"][:150], "fps": frames["fps"],
    }))
    assert metrics["gait_detected"] is True
    save = {"metrics": metrics, "source": "live", "label": "Offline fixture",
            "call_id": call_id, "attempt_id": attempt_id,
            "idempotency_key": "save-attempt-00001"}
    session_path = f"/api/patient-access/{pid}/sessions"
    assert main.post(session_path, headers=bearer, json={
        **save, "attempt_id": "wrong-attempt",
    }).status_code == 409
    saved = ok(main.post(session_path, headers=bearer, json=save))["gait_sessions"][0]
    assert ok(main.post(session_path, headers=bearer, json=save))["gait_sessions"][0]["session_id"] == saved["session_id"]
    assert main.post(session_path, headers=bearer, json={
        **save, "metrics": {**metrics, "fall_risk_score": 0.9},
    }).status_code == 409
    walking = ok(main.get(walk_path, headers=bearer))["walking"]
    assert walking["status"] == "saved"
    assert walking["session_id"] == saved["session_id"]
    assert walking["call_id"] == call_id
    finished = poll(phone, call_id, lambda row: (
        row["finished"] and row["snapshot"]["call_status"] == "completed"
    ))
    if sms_outcome == "rejected":
        assert not any("walking test is saved" in text for text in finished["speech"])
    else:
        assert any("walking test is saved" in text for text in finished["speech"])
    record = ok(main.get(f"/api/patients/{pid}"))
    assert len(record["surveys"]) == len(record["gait_sessions"]) == len(record["calls"]) == 1
    assert record["surveys"][0]["call_id"] == record["gait_sessions"][0]["call_id"] == call_id
    assert record["gait_sessions"][0]["attempt_id"] == attempt_id
    synthesis = ok(main.post(f"/api/patients/{pid}/synthesis"))
    assert synthesis["source"] == "template"
    assert ("undetermined" if unknown else "pain 7/10") in synthesis["summary"]
    durable = json.loads(store_path.read_text())[pid]
    assert durable["gait_sessions"][0]["session_id"] == saved["session_id"]
    assert durable["calls"][0]["walking"]["status"] == "saved"
    next_call = ok(main.post(calls_path, json={
        **start, "request_id": "next-call-request-0001",
    }))["call"]
    assert next_call["call_id"] != call_id
    assert main.post(f"/api/patients/{pid}/synthesis").status_code == 422
    assert main.post(session_path, headers=bearer, json={
        **save, "idempotency_key": "stale-attempt-0001",
    }).status_code == 409

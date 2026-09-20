import ast
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent
import clinician_auth
import patient_access
import store
from integration_models import QUESTION_IDS
from main import SurveyPayload, app
from phone_client import PhoneClient, get_phone_client


PID = "RGN-0417"
SERVICE = {"X-Survey-Token": "test-service-token"}
NUMBER = "+15555550123"


class PhoneFixture:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.snapshot: dict = {}
        self.failure: str | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-operator-token"
        assert request.extensions["timeout"]["read"] == 2
        if self.failure == "timeout":
            raise httpx.ReadTimeout("ambiguous", request=request)
        if self.failure == "invalid":
            return httpx.Response(200, json={"to_number": NUMBER, "status": "queued"})
        if self.failure == "503":
            return httpx.Response(503, json={"detail": "private provider error"})
        if request.method == "POST" and request.url.path == "/api/calls":
            payload = json.loads(request.content)
            self.snapshot = {
                "patient_id": payload["patient_id"],
                "call_id": payload["call_id"],
                "version": 1,
                "call_status": "dialing",
                "survey_status": "pending",
                "sms_status": "not_requested",
                "sms_attempt": 0,
                "provider_call_id": "CAfixture",
                "phone_session_id": "phone_fixture",
                "message_id": None,
                "error_code": None,
            }
        elif request.method == "POST":
            payload = json.loads(request.content)
            assert "/sms-retries" in request.url.path
            token = payload["patient_url"].split("token=")[1]
            patient_access.verify_token(token, payload["patient_id"])
            self.snapshot.update(
                version=self.snapshot["version"] + 1,
                survey_status="stored",
                sms_status="sent",
                sms_attempt=payload["sms_attempt"],
                message_id=f"SMfixture{payload['sms_attempt']}",
            )
        return httpx.Response(200, json=self.snapshot)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINICIAN_USERNAME", "clinician")
    monkeypatch.setenv("CLINICIAN_PASSWORD", "password")
    monkeypatch.setenv("CLINICIAN_SESSION_SECRET", "c" * 32)
    monkeypatch.setenv("CLINICIAN_COOKIE_SECURE", "false")
    monkeypatch.setenv("SURVEY_INGEST_TOKEN", SERVICE["X-Survey-Token"])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    clinician_auth.reset_login_rate_limits()
    path = tmp_path / "patients.json"
    store.reset_for_tests(path)
    phone = PhoneFixture()
    adapter = PhoneClient(
        "http://127.0.0.1:8001",
        "test-operator-token",
        2,
        httpx.MockTransport(phone.handle),
    )
    app.dependency_overrides[get_phone_client] = lambda: adapter
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/clinician/session",
                json={
                    "username": "clinician",
                    "password": "password",
                },
            ).status_code
            == 200
        )
        yield client, phone, path
    app.dependency_overrides.clear()
    store.reset_for_tests(store.CACHE_PATH)


def start(client, **changes) -> dict:
    body = {
        "request_id": "call_request_0001",
        "to_number": NUMBER,
        "condition_category": "orthopedic",
        **changes,
    }
    response = client.post(f"/api/patients/{PID}/calls", json=body)
    assert response.status_code == 200, response.text
    return response.json()["call"]


def intake(call: dict, **changes) -> dict:
    instrument = (
        "hoos_jr" if call["condition_category"] == "orthopedic" else "stroke_mobility"
    )
    return {
        "patient_id": call["patient_id"],
        "call_id": call["call_id"],
        "submission_kind": "integrated",
        "pain_scale": 7,
        "fall_history": {
            "falls_last_6_months": 2,
            "injured": True,
            "last_fall_description": "A trip",
        },
        "dizziness": True,
        "dizziness_notes": "On standing",
        "primary_complaints": ["Hip pain"],
        "condition_survey": {
            "instrument": instrument,
            "version": "1",
            "condition_category": call["condition_category"],
            "answers": [
                {
                    "question_id": question,
                    "normalized_value": "mild",
                    "confirmed": True,
                    "acceptance_method": "explicit_selection",
                }
                for question in QUESTION_IDS[instrument]
            ],
        },
        **changes,
    }


def submit(client, call, **changes):
    response = client.post(
        "/api/submit-survey", json=intake(call, **changes), headers=SERVICE
    )
    assert response.status_code == 200, response.text
    return response.json()


def callback(client, phone, **changes):
    phone.snapshot.update(version=phone.snapshot["version"] + 1, **changes)
    return client.post(
        f"/api/integration/patients/{PID}/calls/{phone.snapshot['call_id']}/status",
        json=phone.snapshot,
        headers=SERVICE,
    )


def patient_headers(pid=PID):
    token, _ = patient_access.generate_token(pid)
    return {"Authorization": f"Bearer {token}"}


def event(call, sequence=1, event_name="page_ready", **changes):
    return {
        "call_id": call["call_id"],
        "attempt_id": call["attempt_id"],
        "sequence": sequence,
        "event_id": f"walk_event_{sequence:06d}",
        "event": event_name,
        **changes,
    }


def session(call):
    return {
        "call_id": call["call_id"],
        "attempt_id": call["attempt_id"],
        "idempotency_key": "walking_save_0001",
        "label": "Voice follow-up",
        "source": "live",
        "metrics": {
            "stride_length_m": 1,
            "asymmetry_pct": 5,
            "velocity_degradation_pct": 2,
            "fall_risk_score": 0.4,
            "cadence_steps_per_min": 100,
            "frame_count": 100,
            "leg_length_m": 0.9,
            "stride_ratio": 1.1,
            "knee_flexion_rom_deg": 40,
            "peak_ankle_speed_mps": 3,
            "gait_detected": True,
        },
    }


def test_question_contract_matches_actual_phone_banks():
    tree = ast.parse(
        (
            Path(__file__).resolve().parents[2] / "voice_agent/app/question_loader.py"
        ).read_text()
    )
    actual = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "SurveyQuestion"
        ):
            keywords = {
                entry.arg: ast.literal_eval(entry.value)
                for entry in node.keywords
                if entry.arg in {"id", "answer_options"}
            }
            assert keywords["answer_options"] == (
                "none",
                "mild",
                "moderate",
                "severe",
                "extreme",
            )
            actual.append(keywords["id"])
    assert sorted(actual) == sorted(
        QUESTION_IDS["hoos_jr"] + QUESTION_IDS["stroke_mobility"]
    )


def test_lookup_unknown_condition_and_clinician_selection(rig):
    client, phone, _ = rig
    lookup = f"/api/integration/patients/{PID}"
    assert client.get(lookup, headers=SERVICE).json() == {
        "patient_id": PID,
        "condition_category": None,
        "active_call_id": None,
    }
    assert (
        client.post(
            f"/api/patients/{PID}/calls",
            json={
                "request_id": "unknown_category_1",
                "to_number": NUMBER,
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/patients/unknown/calls",
            json={
                "request_id": "unknown_patient_1",
                "to_number": NUMBER,
                "condition_category": "stroke",
            },
        ).status_code
        == 404
    )
    assert phone.requests == []
    assert (
        client.patch(
            f"/api/patients/{PID}/condition", json={"condition_category": "stroke"}
        ).status_code
        == 200
    )
    call = start(client, condition_category=None)
    assert call["condition_category"] == "stroke"
    assert client.get(lookup, headers=SERVICE).json()["condition_category"] == "stroke"


def test_auth_boundaries_and_origin(rig, monkeypatch):
    client, phone, _ = rig
    assert (
        client.patch(
            f"/api/patients/{PID}/condition",
            json={"condition_category": "stroke"},
            headers={"Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert (
        client.post(
            f"/api/patients/{PID}/calls",
            json={
                "request_id": "call_request_0001",
                "to_number": NUMBER,
            },
        ).status_code
        == 401
    )
    assert client.get(f"/api/patients/{PID}/calls").status_code == 401
    assert (
        client.patch(
            f"/api/patients/{PID}/condition", json={"condition_category": "stroke"}
        ).status_code
        == 401
    )
    assert client.get(f"/api/integration/patients/{PID}").status_code == 401
    assert (
        client.get(
            f"/api/integration/patients/{PID}", headers=patient_headers()
        ).status_code
        == 401
    )
    monkeypatch.delenv("SURVEY_INGEST_TOKEN")
    assert client.get(f"/api/integration/patients/{PID}").status_code == 503
    assert phone.requests == []


def test_call_idempotency_persists_and_hides_phone(rig):
    client, phone, path = rig
    first = start(client)
    store.reset_for_tests(path)
    again = start(client)
    assert first == again
    assert len(phone.requests) == 1
    assert (
        client.post(
            f"/api/patients/{PID}/calls",
            json={
                "request_id": "call_request_0001",
                "to_number": "+15555550999",
                "condition_category": "orthopedic",
            },
        ).status_code
        == 409
    )
    durable_call = json.loads(path.read_text())[PID]["calls"][0]
    assert durable_call["_destination_phone"] == NUMBER
    public = client.get(f"/api/patients/{PID}").text
    assert "_fingerprint" not in public
    assert "_destination_phone" not in public
    assert NUMBER not in public
    assert "test-operator-token" not in public
    assert len(client.get(f"/api/patients/{PID}/calls").json()["calls"]) == 1


@pytest.mark.parametrize("failure", ["timeout", "invalid", "503"])
def test_ambiguous_call_never_retries_or_exposes_errors(rig, failure):
    client, phone, _ = rig
    phone.failure = failure
    call = start(client)
    assert call["call_status"] == "unknown"
    assert start(client)["call_id"] == call["call_id"]
    assert len(phone.requests) == 1
    response = client.post(
        f"/api/patients/{PID}/calls",
        json={
            "request_id": "another_request_1",
            "to_number": NUMBER,
            "condition_category": "orthopedic",
        },
    )
    assert response.status_code == 409
    assert "private provider" not in response.text
    refresh = client.post(f"/api/patients/{PID}/calls/{call['call_id']}/refresh")
    assert refresh.json()["phone_available"] is False


@pytest.mark.parametrize("category", ["orthopedic", "stroke"])
def test_integrated_intake_keeps_separate_clinical_values(rig, category):
    client, _, path = rig
    call = start(client, condition_category=category)
    result = submit(client, call)
    survey = result["survey"]
    assert survey["pain_scale"] == 7
    assert survey["fall_history"]["falls_last_6_months"] == 2
    assert survey["fall_history"]["injured"] is True
    assert survey["dizziness"] is True
    assert survey["condition_survey"]["answers"][0]["normalized_value"] == "mild"
    assert (
        client.get(f"/api/patients/{PID}/calls/{call['call_id']}").json()["call"][
            "survey_id"
        ]
        == survey["survey_id"]
    )
    assert result["patient_url"] not in path.read_text()
    assert submit(client, call)["survey"] == survey


def test_skipped_questions_are_stored_without_a_value(rig):
    client, _, _ = rig
    call = start(client)
    body = intake(call)
    survey = body["condition_survey"]
    survey["answers"] = survey["answers"][1:]
    survey["skipped"] = ["hoos_stairs"]
    result = client.post("/api/submit-survey", json=body, headers=SERVICE)
    assert result.status_code == 200, result.text
    stored = result.json()["survey"]["condition_survey"]
    assert stored["skipped"] == ["hoos_stairs"]
    assert [a["question_id"] for a in stored["answers"]] == list(QUESTION_IDS["hoos_jr"][1:])
    summary = agent._template_synthesis(result.json()["patient"])
    assert "Prototype raw item sum 5/20 over 5 of 6 items" in summary
    assert "Unanswered after repeated clarification: hoos_stairs" in summary

    # A question can be either answered or skipped, never both or neither.
    for answers, skipped in (
        (intake(call)["condition_survey"]["answers"], ["hoos_stairs"]),
        (intake(call)["condition_survey"]["answers"][1:], []),
        (intake(call)["condition_survey"]["answers"][1:], ["hoos_stairs", "hoos_stairs"]),
    ):
        body = intake(call)
        body["condition_survey"]["answers"] = answers
        body["condition_survey"]["skipped"] = skipped
        assert (
            client.post("/api/submit-survey", json=body, headers=SERVICE).status_code
            == 422
        )


def test_missing_generic_facts_stay_unknown_and_synthesis_uses_condition(rig):
    client, _, _ = rig
    call = start(client)
    result = submit(
        client,
        call,
        pain_scale=None,
        fall_history=None,
        dizziness=None,
        dizziness_notes=None,
        primary_complaints=None,
    )
    survey = result["survey"]
    assert (
        survey["pain_scale"] is None
        and survey["fall_history"] is None
        and survey["dizziness"] is None
    )
    summary = agent._template_synthesis(result["patient"])
    assert "pain unknown/10" in summary
    assert "dizziness unknown" in summary
    assert "undetermined" in summary
    assert "Prototype raw item sum 6/24" in summary
    assert "not a validated HOOS JR interval" in summary
    assert "hoos_stairs=mild" in agent._synthesis_prompt(result["patient"])
    changed = deepcopy(result["patient"])
    changed["surveys"][-1]["condition_survey"]["answers"][0]["normalized_value"] = (
        "extreme"
    )
    assert agent._synthesis_key(changed) != agent._synthesis_key(result["patient"])


def test_unknown_injury_remains_unknown(rig):
    client, _, _ = rig
    call = start(client)
    survey = submit(client, call, fall_history={"falls_last_6_months": 1})["survey"]
    assert survey["fall_history"]["injured"] is None


def test_known_injury_does_not_fabricate_unknown_fall_count(rig):
    client, _, _ = rig
    call = start(client)
    survey = submit(
        client,
        call,
        fall_history={"injured": True, "last_fall_description": "Bruised knee"},
    )["survey"]
    assert survey["fall_history"]["falls_last_6_months"] is None
    assert survey["fall_history"]["injured"] is True
    manual = intake(call, submission_kind="manual", condition_survey=None)
    manual["fall_history"] = {"injured": True}
    assert (
        client.post("/api/submit-survey", json=manual, headers=SERVICE).status_code
        == 422
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "unconfirmed",
        "unknown_question",
        "duplicate",
        "missing",
        "option",
        "version",
        "category",
        "acceptance",
        "extra",
        "missing_condition",
    ],
)
def test_invalid_instruments_never_persist(rig, mutation):
    client, _, _ = rig
    call = start(client)
    body = intake(call)
    condition = body["condition_survey"]
    if mutation == "unconfirmed":
        condition["answers"][0]["confirmed"] = False
    elif mutation == "unknown_question":
        condition["answers"][0]["question_id"] = "invented"
    elif mutation == "duplicate":
        condition["answers"][0] = condition["answers"][1]
    elif mutation == "missing":
        condition["answers"].pop()
    elif mutation == "option":
        condition["answers"][0]["normalized_value"] = "seven"
    elif mutation == "version":
        condition["version"] = "unknown"
    elif mutation == "category":
        condition["condition_category"] = "stroke"
    elif mutation == "acceptance":
        condition["answers"][0]["acceptance_method"] = "inferred"
    elif mutation == "extra":
        condition["answers"][0]["raw_audio"] = "not allowed"
    else:
        del body["condition_survey"]
    count = len(store.get_patient(PID)["surveys"])
    assert (
        client.post("/api/submit-survey", json=body, headers=SERVICE).status_code == 422
    )
    assert len(store.get_patient(PID)["surveys"]) == count


def test_survey_normalized_retry_and_conflict(rig):
    client, _, _ = rig
    call = start(client)
    body = intake(call, recorded_at="2026-09-20T00:00:00Z")
    first = client.post("/api/submit-survey", json=body, headers=SERVICE).json()
    body["recorded_at"] = "2026-09-19T20:00:00-04:00"
    body["condition_survey"]["answers"].reverse()
    body["condition_survey"]["answers"][0]["normalized_value"] = " MILD "
    body["condition_survey"]["answers"][0]["confirmed_at"] = None
    second = client.post("/api/submit-survey", json=body, headers=SERVICE)
    assert second.status_code == 200
    assert second.json()["survey"] == first["survey"]
    body["pain_scale"] = 8
    assert (
        client.post("/api/submit-survey", json=body, headers=SERVICE).status_code == 409
    )
    body["pain_scale"] = 7
    del body["recorded_at"]
    assert (
        client.post("/api/submit-survey", json=body, headers=SERVICE).status_code == 409
    )


def test_atomic_concurrent_ingest_and_rollback(rig):
    client, _, _ = rig
    call = start(client)
    payload = SurveyPayload.model_validate(intake(call)).model_dump(mode="json")
    before = store.get_patient(PID)
    with patch.object(store, "_persist", side_effect=OSError("disk full")):
        assert (
            client.post(
                "/api/submit-survey", json=intake(call), headers=SERVICE
            ).status_code
            == 500
        )
    assert store.get_patient(PID) == before
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(store.upsert_survey, [deepcopy(payload) for _ in range(16)]))
    record = store.get_patient(PID)
    assert len(record["surveys"]) == len(before["surveys"]) + 1
    assert record["calls"][0]["survey_status"] == "stored"


def test_integrated_requires_registered_call_and_cannot_bypass_with_manual(rig):
    client, _, _ = rig
    call = start(client)
    body = intake(call)
    body["call_id"] = "not_a_call"
    assert (
        client.post("/api/submit-survey", json=body, headers=SERVICE).status_code == 404
    )
    body["call_id"] = call["call_id"]
    body["submission_kind"] = "manual"
    del body["condition_survey"]
    assert (
        client.post("/api/submit-survey", json=body, headers=SERVICE).status_code == 409
    )


def test_legacy_manual_with_and_without_call_id(rig):
    client, _, _ = rig
    body = {
        "patient_id": "manual",
        "pain_scale": 4,
        "fall_history": {"falls_last_6_months": 0},
        "dizziness": False,
        "primary_complaints": ["Pain"],
    }
    first = client.post("/api/submit-survey", json=body, headers=SERVICE)
    assert first.status_code == 200
    assert first.json()["patient"]["surveys"][0]["fall_history"]["injured"] is False
    assert (
        len(
            client.post("/api/submit-survey", json=body, headers=SERVICE).json()[
                "patient"
            ]["surveys"]
        )
        == 2
    )
    body["call_id"] = "legacy-call"
    assert (
        client.post("/api/submit-survey", json=body, headers=SERVICE).status_code == 200
    )
    assert (
        len(
            client.post("/api/submit-survey", json=body, headers=SERVICE).json()[
                "patient"
            ]["surveys"]
        )
        == 3
    )


def test_versioned_snapshots_preserve_terminals_and_main_owned_states(rig):
    client, phone, _ = rig
    call = start(client)
    original = deepcopy(phone.snapshot)
    assert callback(client, phone, survey_status="stored").status_code == 409
    phone.snapshot = original
    assert (
        callback(
            client, phone, call_status="in_progress", survey_status="in_progress"
        ).status_code
        == 200
    )
    path = f"/api/integration/patients/{PID}/calls/{call['call_id']}/status"
    assert client.post(path, json=original, headers=SERVICE).status_code == 200
    submit(client, call)
    assert (
        callback(
            client, phone, call_status="completed", survey_status="stored"
        ).status_code
        == 200
    )
    terminal = deepcopy(phone.snapshot)
    assert callback(client, phone, call_status="dialing").status_code == 409
    assert client.post(path, json=terminal, headers=SERVICE).status_code == 200
    terminal["call_status"] = "failed"
    assert client.post(path, json=terminal, headers=SERVICE).status_code == 409
    assert store.get_call(PID, call["call_id"])["call_status"] == "completed"


def test_service_scope_and_authentication_for_all_callback_paths(rig):
    client, phone, _ = rig
    call = start(client)
    prefix = f"/api/integration/patients/{PID}/calls/{call['call_id']}"
    for suffix in ["", "/walking"]:
        assert client.get(prefix + suffix).status_code == 401
        assert client.get(prefix + suffix, headers=SERVICE).status_code == 200
    for suffix, body in [("/status", phone.snapshot), ("/patient-link", {})]:
        assert client.post(prefix + suffix, json=body).status_code == 401
    body = {**phone.snapshot, "patient_id": "RGN-other", "version": 2}
    assert (
        client.post(prefix + "/status", json=body, headers=SERVICE).status_code == 409
    )
    assert (
        client.get(
            f"/api/integration/patients/unknown/calls/{call['call_id']}",
            headers=SERVICE,
        ).status_code
        == 404
    )


def test_sms_retry_is_explicit_and_never_retries_unknown(rig):
    client, phone, path = rig
    call = start(client)
    submit(client, call)
    route = f"/api/patients/{PID}/calls/{call['call_id']}/sms-retries"
    body = {"request_id": "sms_retry_req_0001"}
    assert client.post(route, json=body).status_code == 409
    assert (
        callback(client, phone, survey_status="stored", sms_status="failed").status_code
        == 200
    )
    phone.failure = "timeout"
    result = client.post(route, json=body)
    assert result.status_code == 200
    assert result.json()["call"]["sms_status"] == "unknown"
    count = len(phone.requests)
    assert client.post(route, json=body).json()["replayed"] is True
    assert len(phone.requests) == count
    assert (
        client.post(route, json={"request_id": "sms_retry_req_0002"}).status_code == 409
    )
    assert "patient_url" not in path.read_text()
    phone.failure = None
    assert (
        callback(
            client, phone, sms_status="sent", sms_attempt=1, message_id="SMresolved"
        ).status_code
        == 200
    )


def test_sms_retry_success_and_link_refresh_do_not_append_surveys(rig):
    client, phone, _ = rig
    call = start(client)
    submit(client, call)
    count = len(store.get_patient(PID)["surveys"])
    assert (
        callback(client, phone, survey_status="stored", sms_status="failed").status_code
        == 200
    )
    route = f"/api/patients/{PID}/calls/{call['call_id']}/sms-retries"
    result = client.post(route, json={"request_id": "sms_retry_req_0001"})
    assert result.status_code == 200
    assert result.json()["call"]["sms_status"] == "sent"
    link = client.post(
        f"/api/integration/patients/{PID}/calls/{call['call_id']}/patient-link",
        headers=SERVICE,
    )
    assert link.status_code == 200
    assert link.json()["attempt_id"] == call["attempt_id"]
    assert len(store.get_patient(PID)["surveys"]) == count


def test_phone_refresh_reconciles_without_dialing(rig):
    client, phone, _ = rig
    call = start(client)
    phone.snapshot.update(version=2, call_status="in_progress")
    result = client.post(f"/api/patients/{PID}/calls/{call['call_id']}/refresh")
    assert result.json()["phone_available"] is True
    assert result.json()["call"]["call_status"] == "in_progress"
    assert [request.method for request in phone.requests] == ["POST", "GET"]


def test_walking_events_and_session_save_are_correlated_and_scoped(rig):
    client, phone, path = rig
    call = start(client)
    submit(client, call)
    route = f"/api/patient-access/{PID}/walking/events"
    headers = patient_headers()
    assert client.post(route, json=event(call)).status_code == 401
    assert (
        client.post(
            route, json=event(call), headers=patient_headers("RGN-other")
        ).status_code
        == 401
    )
    wrong = event(call, attempt_id="unknown")
    assert client.post(route, json=wrong, headers=headers).status_code == 409
    wrong["call_id"] = "unknown"
    assert client.post(route, json=wrong, headers=headers).status_code == 404
    names = [
        "page_ready",
        "permission_denied",
        "calibration_started",
        "calibration_completed",
        "capture_started",
        "recoverable_error",
        "capture_completed",
    ]
    for sequence, name in enumerate(names, 1):
        body = event(call, sequence, name)
        if name == "recoverable_error":
            body["error_code"] = "tracking_lost"
        response = client.post(route, json=body, headers=headers)
        assert response.status_code == 200, response.text
    assert response.json()["walking"]["status"] == "captured"
    assert (
        client.post(route, json=event(call, 8, "saved"), headers=headers).status_code
        == 422
    )
    save_route = f"/api/patient-access/{PID}/sessions"
    payload = session(call)
    response = client.post(save_route, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    repeated = client.post(save_route, json=payload, headers=headers)
    assert repeated.json() == response.json()
    walking = client.get(
        f"/api/integration/patients/{PID}/calls/{call['call_id']}/walking",
        headers=SERVICE,
    ).json()
    assert walking["status"] == "saved"
    saved = response.json()["gait_sessions"][-1]
    assert saved["session_id"] == walking["session_id"]
    assert (
        saved["call_id"] == call["call_id"]
        and saved["attempt_id"] == call["attempt_id"]
    )
    assert (
        client.post(route, json=event(call, 8, "stopped"), headers=headers).json()[
            "walking"
        ]["status"]
        == "saved"
    )
    payload["metrics"]["fall_risk_score"] = 0.9
    assert client.post(save_route, json=payload, headers=headers).status_code == 409
    assert "condition_survey" not in response.text
    assert NUMBER not in response.text
    durable = json.loads(path.read_text())[PID]
    linked_call = next(item for item in durable["calls"] if item["call_id"] == saved["call_id"])
    assert linked_call["_destination_phone"] == NUMBER
    assert linked_call["attempt_id"] == saved["attempt_id"]
    assert (
        callback(
            client, phone, survey_status="stored", call_status="completed"
        ).status_code
        == 200
    )


def test_walking_event_dedupe_stale_and_terminal(rig):
    client, _, _ = rig
    call = start(client)
    submit(client, call)
    route = f"/api/patient-access/{PID}/walking/events"
    headers = patient_headers()
    body = event(call, 2, "capture_started")
    first = client.post(route, json=body, headers=headers).json()
    assert client.post(route, json=body, headers=headers).json() == first
    assert (
        client.post(
            route, json={**body, "event": "stopped"}, headers=headers
        ).status_code
        == 409
    )
    assert client.post(route, json=event(call), headers=headers).json() == first
    assert (
        client.post(route, json=event(call, 3, "stopped"), headers=headers).json()[
            "walking"
        ]["status"]
        == "stopped"
    )
    assert (
        client.post(
            f"/api/patient-access/{PID}/sessions", json=session(call), headers=headers
        ).status_code
        == 409
    )


def test_session_requires_detected_gait_and_atomic_persistence(rig):
    client, _, _ = rig
    call = start(client)
    submit(client, call)
    route = f"/api/patient-access/{PID}/sessions"
    payload = session(call)
    bad = deepcopy(payload)
    bad["metrics"]["gait_detected"] = False
    assert client.post(route, json=bad, headers=patient_headers()).status_code == 409
    bad = {
        key: value
        for key, value in payload.items()
        if key not in {"call_id", "attempt_id"}
    }
    assert client.post(route, json=bad, headers=patient_headers()).status_code == 409
    before = store.get_patient(PID)
    with patch.object(store, "_persist", side_effect=OSError("disk full")):
        assert (
            client.post(route, json=payload, headers=patient_headers()).status_code
            == 500
        )
    assert store.get_patient(PID) == before


def test_old_attempt_cannot_change_new_active_attempt(rig):
    client, phone, _ = rig
    old = start(client)
    submit(client, old)
    assert (
        callback(
            client, phone, call_status="completed", survey_status="stored"
        ).status_code
        == 200
    )
    new = start(client, request_id="call_request_0002")
    submit(client, new)
    route = f"/api/patient-access/{PID}/walking/events"
    assert (
        client.post(route, json=event(old), headers=patient_headers()).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/patient-access/{PID}/sessions",
            json=session(old),
            headers=patient_headers(),
        ).status_code
        == 409
    )
    assert (
        client.get(
            f"/api/patient-access/{PID}/walking", headers=patient_headers()
        ).json()["walking"]["call_id"]
        == new["call_id"]
    )


def test_phone_configuration_is_optional_until_proxy_use(rig, monkeypatch):
    client, phone, _ = rig
    app.dependency_overrides.clear()
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)
    assert client.get("/api/health").status_code == 200
    assert client.get(f"/api/patients/{PID}").status_code == 200
    assert (
        client.post(
            f"/api/patients/{PID}/calls",
            json={
                "request_id": "call_request_0001",
                "to_number": NUMBER,
            },
        ).status_code
        == 503
    )
    assert phone.requests == []


def test_corrupt_authoritative_store_never_reseeds_or_dials(rig):
    client, phone, path = rig
    start(client)
    path.write_text("{broken", encoding="utf-8")
    store.reset_for_tests(path)
    before = len(phone.requests)
    response = client.post(
        f"/api/patients/{PID}/calls",
        json={
            "request_id": "call_request_0002",
            "to_number": NUMBER,
            "condition_category": "stroke",
        },
    )
    assert response.status_code == 503
    assert path.read_text() == "{broken"
    assert len(phone.requests) == before


def test_patient_correlation_is_enforced_inside_atomic_store_mutation(rig):
    client, _, _ = rig
    call = start(client)
    payload = session(call)
    del payload["call_id"]
    del payload["attempt_id"]
    before = store.get_patient(PID)
    with pytest.raises(store.Conflict, match="active calls require"):
        store.add_session(PID, payload, require_active_correlation=True)
    assert store.get_patient(PID) == before


def test_durable_call_dedupe_survives_store_reload(rig):
    client, phone, path = rig
    call = start(client)
    store.reset_for_tests(path)
    assert start(client)["call_id"] == call["call_id"]
    assert len(phone.requests) == 1


def test_concurrent_call_reservations_only_dispatch_once(rig):
    client, phone, _ = rig
    with ThreadPoolExecutor(max_workers=4) as pool:
        calls = list(pool.map(lambda _: start(client), range(4)))
    assert len({call["call_id"] for call in calls}) == 1
    assert len(phone.requests) == 1


def test_invalid_callback_rolls_back_all_fields(rig):
    client, phone, _ = rig
    call = start(client)
    before = store.get_call(PID, call["call_id"])
    response = callback(client, phone, call_status="completed", sms_status="sent")
    assert response.status_code == 409
    assert store.get_call(PID, call["call_id"]) == before


def test_call_and_event_limits_do_not_dispatch_or_mutate(rig):
    client, phone, _ = rig
    call = start(client)
    submit(client, call)
    route = f"/api/patient-access/{PID}/walking/events"
    assert (
        client.post(route, json=event(call), headers=patient_headers()).status_code
        == 200
    )
    before = store.get_call(PID, call["call_id"])
    with patch.object(store, "_MAX_EVENTS", 1):
        assert (
            client.post(
                route, json=event(call, 2, "capture_started"), headers=patient_headers()
            ).status_code
            == 409
        )
    assert store.get_call(PID, call["call_id"]) == before
    assert (
        callback(
            client, phone, call_status="completed", survey_status="stored"
        ).status_code
        == 200
    )
    with patch.object(store, "_MAX_CALLS", 1):
        assert (
            client.post(
                f"/api/patients/{PID}/calls",
                json={
                    "request_id": "call_request_0002",
                    "to_number": NUMBER,
                    "condition_category": "orthopedic",
                },
            ).status_code
            == 409
        )
    assert len(phone.requests) == 1


def test_sms_retry_limit_and_stale_attempts(rig):
    client, phone, _ = rig
    call = start(client)
    submit(client, call)
    route = f"/api/patients/{PID}/calls/{call['call_id']}/sms-retries"
    for attempt in range(1, 6):
        assert (
            callback(
                client, phone, survey_status="stored", sms_status="failed"
            ).status_code
            == 200
        )
        result = client.post(route, json={"request_id": f"sms_retry_req_{attempt:04d}"})
        assert result.status_code == 200
        assert result.json()["call"]["sms_attempt"] == attempt
    assert callback(client, phone, sms_status="failed").status_code == 200
    count = len(phone.requests)
    assert (
        client.post(route, json={"request_id": "sms_retry_req_0006"}).status_code == 409
    )
    assert len(phone.requests) == count
    assert (
        callback(client, phone, sms_status="delivered", sms_attempt=0).status_code
        == 200
    )
    assert store.get_call(PID, call["call_id"])["sms_status"] == "failed"

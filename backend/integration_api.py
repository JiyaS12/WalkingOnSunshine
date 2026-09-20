"""Clinician orchestration, service callbacks, and patient walking events."""

import os
import secrets
from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

import database_view
import patient_access
import store
from integration_models import (
    CallStart,
    ConditionInput,
    PhoneSnapshot,
    SMSRetry,
    WalkingEvent,
)
from phone_client import PhoneClient, PhoneOutcomeUnknown, get_phone_client


def require_service_token(x_survey_token: str | None = Header(default=None)) -> None:
    token = os.getenv("SURVEY_INGEST_TOKEN")
    if not token:
        raise HTTPException(503, "survey ingestion is not configured")
    if not x_survey_token or not secrets.compare_digest(token, x_survey_token):
        raise HTTPException(401, "invalid survey token")


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def patient_link(pid: str, call_id: str) -> dict:
    call = store.get_call(pid, call_id)
    record = store.get_patient(pid)
    if call["survey_status"] != "stored" or record.get("active_call_id") != call_id:
        raise store.Conflict("link refresh requires the active call's stored survey")
    link = patient_access.create_patient_link(pid)
    return {
        "patient_url": link.url,
        "patient_access_expires_at": link.expires_at.isoformat(),
        "call_id": call_id,
        "attempt_id": call["attempt_id"],
    }


def _apply_snapshot(pid: str, call_id: str, snapshot: PhoneSnapshot) -> dict:
    return store.apply_phone_snapshot(pid, call_id, snapshot.model_dump(mode="json"))


def create_router(clinician_auth: Callable, patient_auth: Callable) -> APIRouter:
    router = APIRouter(dependencies=[Depends(no_store)])
    clinician = [Depends(clinician_auth)]
    service = [Depends(require_service_token)]
    patient = [Depends(patient_auth)]

    @router.get("/api/clinician/database", dependencies=clinician)
    def clinician_database(
        q: str = Query(default="", max_length=200),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
    ) -> dict:
        try:
            return database_view.snapshot(q, offset, limit)
        except store.StoreUnavailable as exc:
            raise HTTPException(503, "Database unavailable. No local fallback was used.", headers={"Cache-Control": "no-store"}) from exc

    @router.patch("/api/patients/{pid}/condition", dependencies=clinician)
    def set_condition(pid: str, body: ConditionInput) -> dict:
        record = store.set_condition(pid, body.condition_category)
        return {
            "patient_id": pid,
            "condition_category": record["condition_category"],
            "condition_source": "clinician",
        }

    @router.get("/api/integration/patients/{pid}", dependencies=service)
    def lookup_patient(pid: str) -> dict:
        record = store.get_patient(pid)
        return {
            "patient_id": pid,
            "condition_category": record.get("condition_category"),
            "active_call_id": record.get("active_call_id"),
        }

    @router.post("/api/patients/{pid}/calls", dependencies=clinician)
    def start_call(
        pid: str, body: CallStart, phone: PhoneClient = Depends(get_phone_client)
    ) -> dict:
        call, created = store.reserve_call(
            pid,
            body.request_id,
            body.condition_category,
            phone.fingerprint(body.model_dump(mode="json")),
            destination_phone=body.to_number,
        )
        if created:
            try:
                snapshot = phone.request(
                    "POST",
                    "/api/calls",
                    {
                        "patient_id": pid,
                        "call_id": call["call_id"],
                        "attempt_id": call["attempt_id"],
                        "request_id": body.request_id,
                        "to_number": body.to_number,
                        "condition_category": call["condition_category"],
                    },
                )
                call = _apply_snapshot(pid, call["call_id"], snapshot)
            except (PhoneOutcomeUnknown, store.Conflict):
                call = store.mark_dispatch_unknown(pid, call["call_id"])
        return {"call": call, "replayed": not created}

    @router.get("/api/patients/{pid}/calls", dependencies=clinician)
    def calls(pid: str) -> dict:
        return {"calls": store.get_patient(pid).get("calls", [])}

    @router.get("/api/patients/{pid}/calls/{call_id}", dependencies=clinician)
    def read_call(pid: str, call_id: str) -> dict:
        return {"call": store.get_call(pid, call_id)}

    @router.post("/api/patients/{pid}/calls/{call_id}/refresh", dependencies=clinician)
    def refresh_call(
        pid: str, call_id: str, phone: PhoneClient = Depends(get_phone_client)
    ) -> dict:
        call = store.get_call(pid, call_id)
        try:
            call = _apply_snapshot(
                pid, call_id, phone.request("GET", f"/api/calls/{call_id}")
            )
        except (PhoneOutcomeUnknown, store.Conflict):
            return {"call": call, "phone_available": False}
        return {"call": call, "phone_available": True}

    @router.post(
        "/api/patients/{pid}/calls/{call_id}/sms-retries", dependencies=clinician
    )
    def retry_sms(
        pid: str,
        call_id: str,
        body: SMSRetry,
        phone: PhoneClient = Depends(get_phone_client),
    ) -> dict:
        link = patient_link(pid, call_id)
        call, created = store.reserve_sms_retry(pid, call_id, body.request_id)
        if created:
            try:
                snapshot = phone.request(
                    "POST",
                    f"/api/calls/{call_id}/sms-retries",
                    {
                        "patient_id": pid,
                        "call_id": call_id,
                        "attempt_id": call["attempt_id"],
                        "request_id": body.request_id,
                        "sms_attempt": call["sms_attempt"],
                        **link,
                    },
                )
                call = _apply_snapshot(pid, call_id, snapshot)
            except (PhoneOutcomeUnknown, store.Conflict):
                call = store.mark_dispatch_unknown(
                    pid, call_id, sms_attempt=call["sms_attempt"]
                )
        return {"call": call, "replayed": not created}

    @router.get("/api/integration/patients/{pid}/calls/{call_id}", dependencies=service)
    def service_call(pid: str, call_id: str) -> dict:
        return {"call": store.get_call(pid, call_id)}

    @router.post(
        "/api/integration/patients/{pid}/calls/{call_id}/status", dependencies=service
    )
    def service_status(pid: str, call_id: str, body: PhoneSnapshot) -> dict:
        return {"call": _apply_snapshot(pid, call_id, body)}

    @router.post(
        "/api/integration/patients/{pid}/calls/{call_id}/patient-link",
        dependencies=service,
    )
    def refresh_link(pid: str, call_id: str) -> dict:
        return patient_link(pid, call_id)

    @router.get(
        "/api/integration/patients/{pid}/calls/{call_id}/walking", dependencies=service
    )
    def service_walking(pid: str, call_id: str) -> dict:
        return store.walking_view(store.get_call(pid, call_id))

    @router.get("/api/patient-access/{pid}/walking", dependencies=patient)
    def patient_walking(pid: str) -> dict:
        call = store.walk_linked_call(store.get_patient(pid))
        return {"walking": store.walking_view(call) if call else None}

    @router.post("/api/patient-access/{pid}/walking/events", dependencies=patient)
    def patient_event(pid: str, body: WalkingEvent) -> dict:
        return {"walking": store.add_walking_event(pid, body.model_dump(mode="json"))}

    return router

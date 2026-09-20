"""Main-backed call orchestration and a durable, serialized provider receipt stream."""

import asyncio
import hashlib
import hmac
import json
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from fastapi import HTTPException

from .conversation_policy import sms_body
from .integration_contract import CallStart, CallStatus, PhoneError, PhoneSnapshot, RegisteredCall, SMSRetry, SMSStatus
from .main_backend import BackendError, MainBackend, validate_origin
from .phone_receipts import Receipt, ReceiptStore
from .telephony.integrated_provider import PhoneProvider, ProviderRejected, ProviderUnknown

TERMINAL_CALL = {"completed", "failed", "stopped"}
TERMINAL_SURVEY = {"stored", "stopped", "needs_review"}
SMS_RANK = {"not_requested": 0, "sending": 1, "unknown": 2, "sent": 3, "delivered": 4, "failed": 4}


def message_status(status: str) -> SMSStatus | None:
    statuses: dict[str, SMSStatus] = {
        "accepted": "sending", "queued": "sending", "sending": "sending",
        "sent": "sent", "delivered": "delivered", "read": "delivered",
        "failed": "failed", "undelivered": "failed", "canceled": "failed",
    }
    return statuses.get(status)


def validate_patient_link(url: str, patient_id: str) -> None:
    parsed = urlsplit(url)
    try:
        validate_origin(f"{parsed.scheme}://{parsed.netloc}")
    except ValueError as exc:
        raise BackendError() from exc
    if parsed.path != f"/patient/{patient_id}" or not parsed.query or parsed.fragment:
        raise BackendError()


class IntegratedService:
    def __init__(
        self, backend: MainBackend, provider: PhoneProvider, store: ReceiptStore,
        operator_token: str, public_base_url: str,
    ):
        self.backend = backend
        self.provider = provider
        self.store = store
        self.operator_token = operator_token
        self.public_base_url = validate_origin(public_base_url)
        self.numbers: dict[str, str] = {}
        self.submissions: dict[str, str] = {}
        # Calls whose patient declined the walking-link text; the survey is still stored.
        self.link_declined: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._publish_locks: dict[str, asyncio.Lock] = {}
        self._ingest_locks: dict[str, asyncio.Lock] = {}

    def lock(self, call_id: str) -> asyncio.Lock:
        return self._locks.setdefault(call_id, asyncio.Lock())

    def receipt(self, call_id: str) -> Receipt:
        value = self.store.get(call_id)
        if value is None:
            raise HTTPException(404, "Unknown call receipt.")
        return value

    async def publish(self, call_id: str, *, required: bool = False) -> None:
        async with self._publish_locks.setdefault(call_id, asyncio.Lock()):
            receipt = self.receipt(call_id)
            if receipt.published_version == receipt.snapshot.version:
                return
            try:
                await self.backend.status(receipt.snapshot)
            except BackendError:
                if required:
                    raise
                return
            async with self.lock(call_id):
                latest = self.receipt(call_id)
                latest.published_version = receipt.snapshot.version
                self.store.save(latest)

    async def update(self, call_id: str, change: Callable[[Receipt], None]) -> PhoneSnapshot:
        async with self.lock(call_id):
            receipt = self.receipt(call_id)
            before = receipt.snapshot.model_dump()
            change(receipt)
            if before != receipt.snapshot.model_dump():
                receipt.snapshot.version += 1
            self.store.save(receipt)
        await self.publish(call_id)
        return self.receipt(call_id).snapshot

    async def recover(self) -> None:
        for receipt in self.store.all():
            def interrupted(value: Receipt) -> None:
                snapshot = value.snapshot
                if snapshot.call_status == "dispatching":
                    snapshot.call_status = "unknown"
                if snapshot.sms_status == "sending":
                    snapshot.sms_status = "unknown"
            await self.update(receipt.snapshot.call_id, interrupted)

    async def read(self, call_id: str) -> PhoneSnapshot:
        await self.publish(call_id)
        return self.receipt(call_id).snapshot

    def fingerprint(self, payload: CallStart) -> str:
        return hmac.new(
            self.operator_token.encode(), json.dumps(payload.model_dump(), sort_keys=True).encode(),
            hashlib.sha256,
        ).hexdigest()

    async def verify_call(self, payload: CallStart) -> RegisteredCall:
        patient = await self.backend.patient(payload.patient_id)
        call = await self.backend.call(payload.patient_id, payload.call_id)
        if (
            patient.patient_id != payload.patient_id or patient.active_call_id != payload.call_id
            or call.patient_id != payload.patient_id or call.call_id != payload.call_id
            or call.attempt_id != payload.attempt_id or call.request_id != payload.request_id
            or call.condition_category != payload.condition_category
        ):
            raise HTTPException(409, "Registered call metadata does not match.")
        return call

    async def start(self, payload: CallStart) -> PhoneSnapshot:
        fingerprint = self.fingerprint(payload)
        async with self.lock(payload.call_id):
            existing = self.store.get(payload.call_id)
            if existing is not None:
                if not hmac.compare_digest(existing.fingerprint, fingerprint):
                    raise HTTPException(409, "Call request conflicts with its receipt.")
                return existing.snapshot
            call = await self.verify_call(payload)
            if call.call_status != "dispatching" or call.phone_version != 0:
                raise HTTPException(409, "Call already dispatched or receipt unavailable.")
            receipt = Receipt(
                fingerprint=fingerprint, attempt_id=payload.attempt_id,
                snapshot=PhoneSnapshot(
                    patient_id=payload.patient_id, call_id=payload.call_id, version=1,
                    call_status="dispatching", phone_session_id=str(uuid4()),
                ),
            )
            if not self.store.reserve(receipt):
                raise HTTPException(409, "Call reservation already exists.")
            self.numbers[payload.call_id] = payload.to_number
        try:
            await self.publish(payload.call_id, required=True)
        except BackendError:
            return await self.end(payload.call_id, "failed", "provider_unavailable")
        query = urlencode({"call_id": payload.call_id})
        try:
            result = await self.provider.call(
                payload.to_number, f"{self.public_base_url}/twilio/voice?{query}",
                f"{self.public_base_url}/twilio/status?{query}",
            )
        except ProviderRejected:
            return await self.end(payload.call_id, "failed", "provider_rejected")
        except (ProviderUnknown, TimeoutError, asyncio.CancelledError) as error:
            def unknown(value: Receipt) -> None:
                if value.snapshot.call_status == "dispatching":
                    value.snapshot.call_status = "unknown"
                    value.snapshot.error_code = "timeout"
            snapshot = await self.update(payload.call_id, unknown)
            if isinstance(error, asyncio.CancelledError):
                raise
            return snapshot
        return await self.carrier(payload.call_id, result.sid, result.status)

    async def carrier(self, call_id: str, sid: str, status: str, sequence: int | None = None) -> PhoneSnapshot:
        def change(value: Receipt) -> None:
            snapshot = value.snapshot
            if snapshot.provider_call_id not in {None, sid}:
                raise HTTPException(409, "Provider call does not match.")
            if sequence is not None and sequence <= value.carrier_sequence:
                return
            if sequence is not None:
                value.carrier_sequence = sequence
            snapshot.provider_call_id = sid
            if snapshot.call_status in TERMINAL_CALL:
                return
            if status in {"queued", "initiated", "ringing"} and snapshot.call_status in {"dispatching", "unknown"}:
                snapshot.call_status = "dialing"
            elif status == "in-progress":
                snapshot.call_status = "in_progress"
            elif status == "completed":
                snapshot.call_status = "completed"
            elif status in {"busy", "no-answer", "failed", "canceled"}:
                snapshot.call_status = "failed"
                errors: dict[str, PhoneError] = {
                    "busy": "busy", "no-answer": "no_answer",
                    "failed": "provider_rejected", "canceled": "disconnected",
                }
                snapshot.error_code = errors[status]
            if (
                snapshot.call_status in TERMINAL_CALL
                and snapshot.survey_status not in TERMINAL_SURVEY
                and call_id not in self.submissions
            ):
                snapshot.survey_status = "needs_review"
        return await self.update(call_id, change)

    async def begin_stream(self, call_id: str, sid: str) -> RegisteredCall:
        receipt = self.receipt(call_id)
        call = await self.backend.call(receipt.snapshot.patient_id, call_id)
        patient = await self.backend.patient(receipt.snapshot.patient_id)
        if (
            call.call_id != call_id or call.attempt_id != receipt.attempt_id
            or call.patient_id != receipt.snapshot.patient_id or patient.active_call_id != call_id
            or call.call_status in TERMINAL_CALL or call.survey_status in TERMINAL_SURVEY
        ):
            raise HTTPException(409, "Registered stream scope changed.")

        def begin(value: Receipt) -> None:
            if (
                value.stream_started or value.snapshot.call_status in TERMINAL_CALL
                or value.snapshot.provider_call_id != sid
                or value.snapshot.survey_status in TERMINAL_SURVEY
            ):
                raise HTTPException(409, "Stream already consumed or call ended.")
            value.stream_started = True
            value.snapshot.call_status = "in_progress"
            value.snapshot.survey_status = "in_progress"
        await self.update(call_id, begin)
        return call

    async def end(self, call_id: str, status: CallStatus, error: PhoneError | None = None) -> PhoneSnapshot:
        def finish(value: Receipt) -> None:
            snapshot = value.snapshot
            if snapshot.call_status not in TERMINAL_CALL:
                snapshot.call_status = status
                snapshot.error_code = error
            if snapshot.survey_status not in TERMINAL_SURVEY:
                if status == "stopped":
                    snapshot.survey_status = "stopped"
                elif call_id not in self.submissions:
                    snapshot.survey_status = "needs_review"
        return await self.update(call_id, finish)

    async def submit(self, call_id: str, payload: dict[str, object], *, send_link: bool = True) -> PhoneSnapshot:
        async with self._ingest_locks.setdefault(call_id, asyncio.Lock()):
            frozen = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if call_id in self.submissions and self.submissions[call_id] != frozen:
                raise HTTPException(409, "Confirmed submission cannot be changed.")
            self.submissions[call_id] = frozen
            if not send_link:
                self.link_declined.add(call_id)
            result = await self.backend.submit(payload)
            snapshot = self.receipt(call_id).snapshot
            validate_patient_link(result.patient_url, snapshot.patient_id)

            def stored(value: Receipt) -> None:
                value.snapshot.survey_status = "stored"
            await self.update(call_id, stored)
            if call_id in self.link_declined:
                return self.receipt(call_id).snapshot
            return await self.send_sms(call_id, 0, result.patient_url)

    async def retry_submission(self, call_id: str) -> PhoneSnapshot:
        self.receipt(call_id)
        frozen = self.submissions.get(call_id)
        if frozen is None:
            raise HTTPException(409, "Confirmed payload is unavailable; reconcile with main.")
        return await self.submit(call_id, json.loads(frozen))

    async def retry_sms(self, call_id: str, payload: SMSRetry) -> PhoneSnapshot:
        receipt = self.receipt(call_id)
        if (
            payload.call_id != call_id or payload.patient_id != receipt.snapshot.patient_id
            or payload.attempt_id != receipt.attempt_id
        ):
            raise HTTPException(409, "Retry scope mismatch.")
        call = await self.backend.call(payload.patient_id, call_id)
        patient = await self.backend.patient(payload.patient_id)
        if (
            call.survey_status != "stored" or patient.active_call_id != call_id
            or call.sms_attempt != payload.sms_attempt
            or not any(r.request_id == payload.request_id and r.sms_attempt == payload.sms_attempt for r in call.sms_retries)
        ):
            raise HTTPException(409, "Retry was not reserved by main.")
        validate_patient_link(payload.patient_url, payload.patient_id)
        async with self.lock(call_id):
            receipt = self.receipt(call_id)
            if payload.request_id in receipt.sms_requests:
                if receipt.sms_requests[payload.request_id] != payload.sms_attempt:
                    raise HTTPException(409, "Retry request conflict.")
                return receipt.snapshot
            if receipt.snapshot.sms_status != "failed" or payload.sms_attempt != receipt.snapshot.sms_attempt + 1:
                raise HTTPException(409, "Only a definitively failed message can be retried.")
            receipt.sms_requests[payload.request_id] = payload.sms_attempt
            receipt.snapshot.sms_attempt = payload.sms_attempt
            receipt.snapshot.sms_status = "sending"
            receipt.snapshot.message_id = None
            receipt.snapshot.error_code = None
            receipt.snapshot.version += 1
            self.store.save(receipt)
        return await self.dispatch_sms(call_id, payload.sms_attempt, payload.patient_url)

    async def send_sms(self, call_id: str, attempt: int, url: str) -> PhoneSnapshot:
        async with self.lock(call_id):
            receipt = self.receipt(call_id)
            if receipt.snapshot.sms_attempt != attempt or receipt.snapshot.sms_status != "not_requested":
                return receipt.snapshot
            receipt.snapshot.sms_status = "sending"
            receipt.snapshot.version += 1
            self.store.save(receipt)
        return await self.dispatch_sms(call_id, attempt, url)

    async def dispatch_sms(self, call_id: str, attempt: int, url: str) -> PhoneSnapshot:
        try:
            await self.publish(call_id, required=True)
        except BackendError:
            return await self.sms_event(call_id, attempt, None, "failed", error="provider_unavailable")
        number = self.numbers.get(call_id)
        if number is None:
            return await self.sms_event(call_id, attempt, None, "failed", error="provider_unavailable")
        query = urlencode({"call_id": call_id, "attempt": attempt})
        try:
            result = await self.provider.sms(number, sms_body(url), f"{self.public_base_url}/twilio/sms-status?{query}")
        except ProviderRejected:
            return await self.sms_event(call_id, attempt, None, "failed")
        except (ProviderUnknown, TimeoutError, asyncio.CancelledError) as error:
            snapshot = await self.sms_event(call_id, attempt, None, "unknown")
            if isinstance(error, asyncio.CancelledError):
                raise
            return snapshot
        return await self.sms_event(call_id, attempt, result.sid, message_status(result.status) or "unknown")

    async def sms_event(
        self, call_id: str, attempt: int, sid: str | None, status: SMSStatus,
        *, error: PhoneError | None = None,
    ) -> PhoneSnapshot:
        def change(value: Receipt) -> None:
            snapshot = value.snapshot
            if attempt > snapshot.sms_attempt or snapshot.survey_status != "stored":
                raise HTTPException(409, "SMS attempt was not dispatched.")
            key = str(attempt)
            known = value.message_ids.get(key)
            if known is not None and sid is not None and known != sid:
                raise HTTPException(409, "Message SID conflict.")
            if sid is not None:
                value.message_ids[key] = sid
            if attempt < snapshot.sms_attempt:
                return
            if snapshot.sms_status == "not_requested":
                raise HTTPException(409, "SMS was not dispatched.")
            if sid is not None:
                snapshot.message_id = sid
            if snapshot.sms_status not in {"failed", "delivered"} and SMS_RANK[status] >= SMS_RANK[snapshot.sms_status]:
                snapshot.sms_status = status
                snapshot.error_code = (error or "provider_rejected") if status == "failed" else None
        return await self.update(call_id, change)

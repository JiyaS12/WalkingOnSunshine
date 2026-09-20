"""Loopback-only test controls, never mounted by the production phone app."""

import os
from pathlib import Path
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel

from app.integrated_service import IntegratedService
from app.integrated_session import IntegratedSession
from app.integration_contract import StoredSurveyResponse
from app.main_backend import MainBackend
from app.operator_auth import OperatorAuth
from app.phone_receipts import ReceiptStore
from app.telephony.config import load_settings
from app.telephony.integrated_provider import FakePhoneProvider
from phone_app import create_integrated_app

canonical_links: dict[str, str] = {}


class RecordingMainBackend(MainBackend):
    async def submit(self, payload: dict[str, object]) -> StoredSurveyResponse:
        response = await super().submit(payload)
        canonical_links[str(payload["call_id"])] = response.patient_url
        return response


provider = FakePhoneProvider()
operator = OperatorAuth(os.environ["OPERATOR_TOKEN"])
service = IntegratedService(
    RecordingMainBackend.from_env(), provider, ReceiptStore(Path(os.environ["PHONE_RECEIPTS_PATH"])),
    os.environ["OPERATOR_TOKEN"], os.environ["PUBLIC_BASE_URL"],
)
app = create_integrated_app(load_settings(), operator, service)
sessions: dict[str, IntegratedSession] = {}
speech: dict[str, list[str]] = {}


class Scenario(BaseModel):
    sms_outcome: Literal["sent", "rejected", "unknown"]


class Utterance(BaseModel):
    text: str


@app.post("/fixture/scenario", dependencies=[Depends(operator)])
async def scenario(body: Scenario) -> dict[str, bool]:
    provider.sms_outcome = body.sms_outcome
    return {"configured": True}


@app.post("/fixture/calls/{call_id}/begin", dependencies=[Depends(operator)])
async def begin(call_id: str) -> dict[str, bool]:
    receipt = service.receipt(call_id)
    assert receipt.snapshot.provider_call_id is not None
    call = await service.begin_stream(call_id, receipt.snapshot.provider_call_id)
    speech[call_id] = []

    async def speak(text: str) -> None:
        speech[call_id].append(text)

    session = IntegratedSession(service, call, speak, poll_seconds=0.02, wait_seconds=30)
    sessions[call_id] = session
    await session.begin()
    return {"started": True}


@app.post("/fixture/calls/{call_id}/utterance", dependencies=[Depends(operator)])
async def utterance(call_id: str, body: Utterance) -> dict[str, bool]:
    session = sessions[call_id]
    session.add_transcript(body.text)
    return {"finished": await session.flush_utterance()}


@app.get("/fixture/calls/{call_id}", dependencies=[Depends(operator)])
async def read(call_id: str) -> dict[str, object]:
    session = sessions[call_id]
    return {
        "snapshot": (await service.read(call_id)).model_dump(),
        "submission": session.submission,
        "speech": speech[call_id],
        "finished": session.finished,
        "messages": provider.messages,
        "calls": provider.calls,
        "sms_body": provider.last_body,
        "canonical_url": canonical_links.get(call_id),
    }

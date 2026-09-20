"""Offline contract checks against the authoritative backend's real JSON store."""

import asyncio
import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import FormData

import phone_app
from app import conversation_policy as policy
from app import generic_intake
from app.generic_intake import (
    QUESTIONS, GenericIntake, IntakeQuestion, IntakeReading, OpenAIIntakeInterpreter, lenient_parse, parse_value,
)
from app.integrated_service import IntegratedService
from app.integrated_session import IntegratedSession, consent_intent
from app.integration_contract import CallStart, SMSRetry
from app.main_backend import BackendError, MainBackend
from app.operator_auth import OperatorAuth
from app.phone_receipts import ReceiptStore
from app.telephony.config import load_settings
from app.telephony.deepgram_stt import SpeechEvent
from app.telephony.integrated_provider import (
    FakePhoneProvider, ProviderRejected, ProviderUnknown, TwilioProvider,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend import store  # noqa: E402
from backend.integration_models import ConditionSurvey, PhoneSnapshot  # noqa: E402

TOKEN = "offline-operator-token-1234"
SERVICE_TOKEN = "offline-service-token"
ENV = {
    "DEEPGRAM_API_KEY": "offline",
    "TWILIO_ACCOUNT_SID": "ACoffline",
    "TWILIO_AUTH_TOKEN": "offline-signature-token",
    "TWILIO_FROM_NUMBER": "+15005550006",
    "PUBLIC_BASE_URL": "https://phone.example.test",
}


class MainHarness:
    def __init__(self, path):
        store.upsert_survey({"patient_id": "patient1", "submission_kind": "manual"})
        call, _ = store.reserve_call("patient1", "request-1234567890", "orthopedic", "main-fingerprint")
        self.payload = CallStart(
            patient_id="patient1", call_id=call["call_id"], attempt_id=call["attempt_id"],
            request_id=call["request_id"], condition_category="orthopedic", to_number="+14155550123",
        )
        self.url = "https://patient.example.test/patient/patient1?token=MAIN-CANONICAL-TOKEN"
        self.submissions = []
        self.snapshots = []
        self.paths = []
        self.submit_error = 0
        self.lose_response = False
        self.walks = []
        self.block_walk = False
        self.walk_requested = asyncio.Event()
        self.provider = FakePhoneProvider()
        self.backend = MainBackend(
            "http://127.0.0.1:8000", SERVICE_TOKEN, transport=httpx.MockTransport(self.request),
        )
        self.path = path / "receipts.sqlite3"
        self.service = IntegratedService(
            self.backend, self.provider, ReceiptStore(self.path), TOKEN, ENV["PUBLIC_BASE_URL"],
        )
        self.spoken = []

    async def request(self, request):
        assert request.headers["X-Survey-Token"] == SERVICE_TOKEN
        self.paths.append(request.url.path)
        prefix = f"/api/integration/patients/patient1/calls/{self.payload.call_id}"
        try:
            if request.url.path == "/api/integration/patients/patient1":
                return httpx.Response(200, json=store.get_patient("patient1"))
            if request.url.path == prefix:
                return httpx.Response(200, json={"call": store.get_call("patient1", self.payload.call_id)})
            if request.url.path == prefix + "/status":
                snapshot = PhoneSnapshot.model_validate_json(request.content).model_dump()
                call = store.apply_phone_snapshot("patient1", self.payload.call_id, snapshot)
                self.snapshots.append(snapshot)
                return httpx.Response(200, json={"call": call})
            if request.url.path == "/api/submit-survey":
                payload = json.loads(request.content)
                self.submissions.append(payload)
                if self.submit_error:
                    return httpx.Response(self.submit_error)
                ConditionSurvey.model_validate(payload["condition_survey"])
                store.upsert_survey(payload)
                if self.lose_response:
                    raise httpx.ReadTimeout("Synthetic lost response")
                return httpx.Response(200, json={
                    "status": "stored", "patient_url": self.url,
                    "patient_access_expires_at": "2099-01-01T00:00:00Z",
                })
            if request.url.path == prefix + "/walking":
                self.walk_requested.set()
                if self.block_walk:
                    await asyncio.Event().wait()
                if self.walks:
                    return httpx.Response(200, json=self.walks.pop(0))
                return httpx.Response(200, json=store.walking_view(store.get_call("patient1", self.payload.call_id)))
        except store.Conflict:
            return httpx.Response(409)
        raise AssertionError(f"Unexpected backend path: {request.url.path}")

    async def session(self, **options):
        await self.service.start(self.payload)
        call = await self.service.begin_stream(self.payload.call_id, "CAfake1")

        async def speak(text):
            self.spoken.append(text)

        session = IntegratedSession(self.service, call, speak, poll_seconds=0.001, wait_seconds=0.2, **options)
        await session.begin()
        return session

    def view(self, status, sequence, last_event=None, session_id=None):
        return {
            "call_id": self.payload.call_id, "attempt_id": self.payload.attempt_id,
            "version": sequence + 1, "survey_status": "stored", "status": status,
            "last_sequence": sequence, "last_event": last_event, "session_id": session_id,
        }


@pytest.fixture
def harness(tmp_path):
    with patch.multiple(store, _patients={}, _cache_path=tmp_path / "main.json"):
        value = MainHarness(tmp_path)
        yield value
        value.service.store.close()


async def say(session, text):
    session.add_transcript(text)
    return await session.flush_utterance()


async def answer_questions(session):
    for _ in range(6):
        await say(session, "mild")


async def answer_survey(session, consent: str = "yes"):
    await answer_questions(session)
    await say(session, consent)


def test_text_waits_for_a_spoken_yes(harness):
    async def scenario():
        session = await harness.session()
        await answer_questions(session)
        assert session.stage == "consent"
        assert harness.spoken[-1] == policy.INTEGRATED_GAIT_INTRO
        assert harness.spoken[-1].endswith(policy.INTEGRATED_CONSENT_QUESTION)
        await asyncio.sleep(0.01)
        assert harness.submissions == []
        assert harness.provider.messages == 0
        await say(session, "um, what?")
        assert harness.spoken[-1] == policy.INTEGRATED_CONSENT_UNCLEAR
        await say(session, "repeat")
        assert harness.spoken[-1] == policy.INTEGRATED_CONSENT_QUESTION
        await say(session, "yes, no problem")
        assert harness.spoken[-1] == policy.INTEGRATED_CONSENT_GIVEN
        await harness.walk_requested.wait()
        assert len(harness.submissions) == 1
        assert harness.provider.messages == 1
        assert policy.INTEGRATED_LINK_SENT in harness.spoken
        await session.disconnect()
    asyncio.run(scenario())


def test_declining_the_text_still_stores_the_answers_and_never_texts(harness):
    async def scenario():
        session = await harness.session()
        await answer_questions(session)
        await say(session, "yes but not right now")
        assert session.finished
        assert harness.spoken[-1] == policy.INTEGRATED_CONSENT_DECLINED
        assert len(harness.submissions) == 1
        assert len(harness.submissions[0]["condition_survey"]["answers"]) == 6
        assert harness.provider.messages == 0
        snapshot = harness.service.receipt(session.call.call_id).snapshot
        assert snapshot.survey_status == "stored"
        assert snapshot.sms_status == "not_requested"
        assert snapshot.call_status == "completed"
        assert snapshot.error_code is None
        await harness.service.retry_submission(session.call.call_id)
        assert harness.provider.messages == 0
    asyncio.run(scenario())


def test_no_answer_to_consent_is_treated_as_no(harness):
    async def scenario():
        session = await harness.session()
        await answer_questions(session)
        for _ in range(2):
            await session.handle_silence()
            assert harness.spoken[-1] == policy.INTEGRATED_CONSENT_QUESTION
        await session.handle_silence()
        assert session.finished
        assert harness.spoken[-1] == policy.INTEGRATED_CONSENT_DECLINED
        assert len(harness.submissions) == 1
        assert harness.provider.messages == 0
    asyncio.run(scenario())


def test_repeated_unclear_consent_replies_default_to_no(harness):
    async def scenario():
        session = await harness.session()
        await answer_questions(session)
        await say(session, "hmm")
        await say(session, "what do you mean")
        assert not session.finished
        await say(session, "the weather is nice")
        assert session.finished
        assert harness.spoken[-1] == policy.INTEGRATED_CONSENT_DECLINED
        assert harness.provider.messages == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("reply, intent", [
    ("yes", "yes"), ("Sure, go ahead", "yes"), ("okay that's fine", "yes"), ("no problem", "yes"),
    ("yeah send it", "yes"), ("no", "no"), ("no thanks", "no"), ("I'd rather not", "no"),
    ("yes but not now", "no"), ("please don't", "no"), ("maybe later", "no"),
    ("you can't text me", "no"), ("you can\u2019t text me", "no"), ("you cannot text me", "no"),
    ("I won't be able to", "no"), ("you can text me", "yes"),
    ("hmm", "unclear"), ("what link", "unclear"), ("", "unclear"),
])
def test_consent_intent(reply, intent):
    assert consent_intent(reply) == intent


def test_combined_intake_and_canonical_link_are_separate_and_idempotent(harness):
    async def scenario():
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        payload = harness.submissions[0]
        assert payload["pain_scale"] is None
        assert payload["fall_history"] == {
            "falls_last_6_months": None, "injured": None, "last_fall_description": None,
        }
        assert payload["dizziness"] is None
        assert payload["dizziness_notes"] is None
        assert payload["primary_complaints"] is None
        assert len(payload["condition_survey"]["answers"]) == 6
        assert {answer["normalized_value"] for answer in payload["condition_survey"]["answers"]} == {"mild"}
        assert harness.url in harness.provider.last_body
        assert harness.service.receipt(session.call.call_id).snapshot.survey_status == "stored"
        assert harness.url not in harness.path.read_bytes().decode(errors="ignore")
        assert "MAIN-CANONICAL-TOKEN" not in json.dumps([r.model_dump() for r in harness.service.store.all()])
        await harness.service.retry_submission(session.call.call_id)
        assert len(store.get_patient("patient1")["surveys"]) == 2
        assert harness.provider.messages == 1
        assert harness.submissions[0] == harness.submissions[1]
        await session.disconnect()
    asyncio.run(scenario())


def test_call_opens_with_first_condition_question_and_no_second_greeting(harness):
    async def scenario():
        session = await harness.session()
        opening = harness.spoken[0]
        assert opening.startswith(policy.INTEGRATED_INTRO)
        assert "Question 1 of 6." in opening
        assert "The choices are:" in opening
        assert "scale of one to ten" not in opening.lower()
        await say(session, "mild")
        assert not any(policy.INTRO in text for text in harness.spoken)
        assert any("Question 2 of 6." in text for text in harness.spoken)
        await say(session, "repeat")
        assert "Question 2 of 6." in harness.spoken[-1]
        assert policy.INTRO not in harness.spoken[-1]
        await session.disconnect()
    asyncio.run(scenario())


def test_stroke_call_uses_captured_condition_and_stroke_ids(harness):
    async def scenario():
        await harness.service.start(harness.payload)
        await harness.service.end(harness.payload.call_id, "stopped", "stopped")
        call, _ = store.reserve_call("patient1", "stroke-request-123456", "stroke", "stroke-fingerprint")
        harness.payload = CallStart(
            patient_id="patient1", call_id=call["call_id"], attempt_id=call["attempt_id"],
            request_id=call["request_id"], condition_category="stroke", to_number="+14155550123",
        )
        snapshot = await harness.service.start(harness.payload)
        registered = await harness.service.begin_stream(harness.payload.call_id, snapshot.provider_call_id)

        async def speak(text):
            harness.spoken.append(text)

        session = IntegratedSession(harness.service, registered, speak, poll_seconds=0.001)
        await session.begin()
        await answer_survey(session)
        await harness.walk_requested.wait()
        condition = harness.submissions[0]["condition_survey"]
        assert condition["instrument"] == "stroke_mobility"
        assert {a["question_id"] for a in condition["answers"]} == {
            "stroke_balance", "stroke_weakness", "stroke_stairs", "stroke_turning",
            "stroke_walking", "stroke_recovery",
        }
        await session.disconnect()
    asyncio.run(scenario())


def test_stop_during_unconfirmed_intake_never_submits(harness):
    async def scenario():
        session = await harness.session()
        await say(session, "mild")
        await say(session, "stop")
        assert session.finished
        assert len(session.engine.session.answers) == 1
        assert harness.submissions == []
        assert harness.provider.messages == 0
        assert store.get_call("patient1", session.call.call_id)["survey_status"] == "stopped"
    asyncio.run(scenario())


@pytest.mark.parametrize("kind,text", [
    ("pain", "moderate"), ("pain", "0"), ("pain", "11"), ("count", "-2"),
    ("boolean", "maybe"), ("complaints", "x" * 121), ("text", "x" * 501),
])
def test_generic_intake_validation(kind, text):
    assert parse_value(kind, text)[0] is False


def test_generic_intake_confirmation_pause_and_bounded_rejections():
    intake = GenericIntake()
    intake.handle("maybe a 7")
    assert intake.values == {}
    assert "Paused" in intake.handle("pause")
    intake.handle("yes")
    assert intake.values == {}
    assert '"7"' in intake.handle("resume")
    assert '"7"' in intake.handle("repeat")
    intake.handle("no")
    intake.handle("probably 8")
    intake.handle("no")
    intake.handle("i think 9")
    intake.handle("no")
    assert intake.state == "needs_review"
    assert intake.values == {}


@pytest.mark.parametrize("kind,text,value", [
    ("pain", "about a five", 5), ("pain", "I'd say it's like a 6 out of 10", 6), ("pain", "seven", 7),
    ("count", "no falls", 0), ("count", "I haven't fallen", 0), ("count", "once", 1), ("count", "I fell twice", 2),
    ("boolean", "yes I have", True), ("boolean", "no I haven't", False), ("boolean", "not really", False),
    ("pain", "I'm not sure", None), ("count", "no idea", None),
    ("text", "nothing really", None), ("complaints", "no complaints", []),
])
def test_generic_intake_reads_plain_speech_without_confirmation(kind, text, value):
    reading = lenient_parse(kind, text)
    assert (reading.valid, reading.value, reading.clear) == (True, value, True)


@pytest.mark.parametrize("kind,text", [
    ("pain", "five or six"), ("pain", "pretty bad"), ("count", "a couple"), ("boolean", "yes and no"),
    ("boolean", "what do you mean"), ("pain", "11"),
])
def test_generic_intake_does_not_guess(kind, text):
    assert lenient_parse(kind, text).valid is False


def test_free_text_is_split_but_proposed_for_confirmation():
    reading = lenient_parse("complaints", "my knee and my back")
    assert (reading.valid, reading.value, reading.clear) == (True, ["my knee", "my back"], False)


def test_hedged_intake_answers_are_proposed_not_accepted():
    intake = GenericIntake()
    assert 'I heard "7"' in intake.handle("maybe like a seven")
    assert intake.values == {}
    intake.handle("yes")
    assert intake.values["pain_scale"] == 7


def test_model_read_intake_answers_still_require_confirmation():
    class Model:
        def interpret(self, question, transcript):
            direct = lenient_parse(question.kind, transcript)
            return direct if direct.valid else IntakeReading(True, 4)

    intake = GenericIntake(Model())
    assert 'I heard "4"' in intake.handle("it's been rough but manageable")
    assert intake.values == {}
    assert intake.handle("yes").startswith(("Got it.", "Okay.", "Thanks."))
    assert intake.values["pain_scale"] == 4


def test_concurrent_start_is_reserved_and_reconciles_after_restart(harness):
    async def scenario():
        results = await asyncio.gather(*(harness.service.start(harness.payload) for _ in range(4)))
        assert harness.provider.calls == 1
        assert all(result.call_id == harness.payload.call_id for result in results)
        with pytest.raises(HTTPException, match="conflicts"):
            await harness.service.start(harness.payload.model_copy(update={"to_number": "+14155550124"}))
        other = IntegratedService(
            harness.backend, harness.provider, ReceiptStore(harness.path), TOKEN, ENV["PUBLIC_BASE_URL"],
        )
        await other.recover()
        await other.start(harness.payload)
        assert harness.provider.calls == 1
        other.store.close()
    asyncio.run(scenario())


def test_mismatched_registered_condition_blocks_dialing(harness):
    async def scenario():
        with pytest.raises(HTTPException):
            await harness.service.start(harness.payload.model_copy(update={"condition_category": "stroke"}))
        assert harness.provider.calls == 0
    asyncio.run(scenario())


def test_ambiguous_call_never_redispatched(harness):
    class Ambiguous(FakePhoneProvider):
        async def call(self, to_number, voice_url, status_url):
            self.calls += 1
            raise ProviderUnknown()

    async def scenario():
        provider = Ambiguous()
        harness.service.provider = provider
        first = await harness.service.start(harness.payload)
        second = await harness.service.start(harness.payload)
        assert first.call_status == second.call_status == "unknown"
        assert provider.calls == 1
        await harness.service.carrier(harness.payload.call_id, "CAreal", "in-progress", 2)
        await harness.service.carrier(harness.payload.call_id, "CAreal", "ringing", 1)
        assert harness.service.receipt(harness.payload.call_id).snapshot.call_status == "in_progress"
    asyncio.run(scenario())


def test_failed_sms_reserved_retry_and_delivery_replays(harness):
    async def scenario():
        harness.provider.sms_outcome = "rejected"
        session = await harness.session()
        await answer_survey(session)
        await session._background
        assert session.finished
        assert harness.service.receipt(session.call.call_id).snapshot.sms_status == "failed"
        call, _ = store.reserve_sms_retry("patient1", session.call.call_id, "sms-retry-1234567890")
        retry = SMSRetry(
            patient_id="patient1", call_id=session.call.call_id, attempt_id=call["attempt_id"],
            request_id="sms-retry-1234567890", sms_attempt=1, patient_url=harness.url,
            patient_access_expires_at="2099-01-01T00:00:00Z",
        )
        harness.provider.sms_outcome = "sent"
        await asyncio.gather(*(harness.service.retry_sms(session.call.call_id, retry) for _ in range(3)))
        assert harness.provider.messages == 2
        await harness.service.sms_event(session.call.call_id, 1, "SMfake2", "delivered")
        version = harness.service.receipt(session.call.call_id).snapshot.version
        await harness.service.sms_event(session.call.call_id, 1, "SMfake2", "sent")
        await harness.service.sms_event(session.call.call_id, 0, "SMold", "failed")
        assert harness.service.receipt(session.call.call_id).snapshot.sms_status == "delivered"
        assert harness.service.receipt(session.call.call_id).snapshot.version == version
        assert store.get_call("patient1", session.call.call_id)["sms_status"] == "delivered"
    asyncio.run(scenario())


def test_carrier_undelivered_sms_ends_the_wait_honestly(harness):
    async def scenario():
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        assert any(text == policy.INTEGRATED_LINK_SENT for text in harness.spoken)
        await harness.service.sms_event(session.call.call_id, 0, "SMfake1", "failed", error="provider_rejected")
        await asyncio.wait_for(session._background, 1)
        assert session.finished
        assert harness.spoken[-1] == policy.INTEGRATED_SMS_FAILED
        assert harness.service.receipt(session.call.call_id).snapshot.error_code == "provider_rejected"
        assert store.get_call("patient1", session.call.call_id)["call_status"] == "completed"
        assert store.get_call("patient1", session.call.call_id)["sms_status"] == "failed"
    asyncio.run(scenario())


def test_a_spoken_ready_does_not_hide_an_undelivered_text(harness):
    async def scenario():
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        await say(session, "ready")
        assert session.link_open
        await harness.service.sms_event(session.call.call_id, 0, "SMfake1", "failed", error="provider_rejected")
        await asyncio.wait_for(session._background, 1)
        assert harness.spoken[-1] == policy.INTEGRATED_SMS_FAILED
        assert harness.service.receipt(session.call.call_id).snapshot.error_code == "provider_rejected"
    asyncio.run(scenario())


def test_open_page_outranks_a_late_sms_failure(harness):
    async def scenario():
        harness.walks = [harness.view("page_ready", 2, "page_ready")]
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        await harness.service.sms_event(session.call.call_id, 0, "SMfake1", "failed", error="provider_rejected")
        await asyncio.sleep(0.02)
        assert session.link_open
        assert not session.finished
        assert policy.INTEGRATED_SMS_FAILED not in harness.spoken
        assert harness.spoken[-1] == policy.INTEGRATED_PAGE_OPENED
        await session.disconnect()
    asyncio.run(scenario())


def test_unknown_sms_is_not_automatically_retried(harness):
    async def scenario():
        harness.provider.sms_outcome = "unknown"
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        await harness.service.retry_submission(session.call.call_id)
        assert harness.provider.messages == 1
        assert harness.service.receipt(session.call.call_id).snapshot.sms_status == "unknown"
        await harness.service.sms_event(session.call.call_id, 0, "SMlate", "delivered")
        assert store.get_call("patient1", session.call.call_id)["sms_status"] == "delivered"
        await session.disconnect()
    asyncio.run(scenario())


@pytest.mark.parametrize("lost_response", [False, True])
def test_submission_retry_uses_frozen_confirmed_payload(harness, lost_response):
    async def scenario():
        harness.lose_response = lost_response
        harness.submit_error = 0 if lost_response else 503
        session = await harness.session()
        await answer_survey(session)
        await session._background
        assert harness.provider.messages == 0
        assert session.finished
        harness.lose_response = False
        harness.submit_error = 0
        await harness.service.retry_submission(session.call.call_id)
        assert harness.provider.messages == 1
        assert harness.submissions[0] == harness.submissions[1]
        assert len(store.get_patient("patient1")["surveys"]) == 2
        changed = dict(harness.submissions[0], pain_scale=1)
        with pytest.raises(HTTPException):
            await harness.service.submit(session.call.call_id, changed)
    asyncio.run(scenario())


def test_409_submission_conflict_produces_no_link_or_retry(harness):
    async def scenario():
        harness.submit_error = 409
        session = await harness.session()
        await answer_survey(session)
        await session._background
        assert session.finished
        assert harness.provider.messages == 0
        assert len(harness.submissions) == 1
        assert "wasn’t able to save your answers" in harness.spoken[-1]
        assert policy.INTEGRATED_LINK_SENT not in harness.spoken
    asyncio.run(scenario())


def test_readiness_capture_errors_and_save_require_backend_events(harness):
    async def scenario():
        harness.walks = [
            harness.view("waiting", 1, "permission_denied"),
            harness.view("calibrating", 2, "calibration_started"),
            harness.view("ready", 3, "calibration_completed"),
            harness.view("capturing", 4, "capture_started"),
            harness.view("captured", 5, "recoverable_error"),
            harness.view("captured", 6, "capture_completed"),
            harness.view("saved", 7, session_id="gait-session"),
        ]
        session = await harness.session()
        await answer_survey(session)
        await session._background
        assert session.finished
        assert any("couldn’t get to your camera" in text for text in harness.spoken)
        assert any("ran into a problem" in text for text in harness.spoken)
        assert any("confirms calibration is ready" in text for text in harness.spoken)
        assert "backend confirms your walking test is saved" in harness.spoken[-1]
        assert "help your care team follow your recovery" in harness.spoken[-1]
    asyncio.run(scenario())


def test_warm_gait_handoff_waits_for_ready_before_camera_setup(harness):
    async def scenario():
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        intro = next(text for text in harness.spoken if "Thank you for those answers" in text)
        assert "short video of you walking" in intro
        assert "secure link to the camera page" in intro
        assert intro.endswith(policy.INTEGRATED_CONSENT_QUESTION)
        assert harness.spoken[-1] == policy.INTEGRATED_LINK_SENT
        assert not any("Live Camera" in text for text in harness.spoken)
        # Quiet while they look for the text gets a gentle nudge, never an escalation.
        for _ in range(4):
            await session.handle_silence()
        assert harness.spoken.count(policy.INTEGRATED_LINK_REMINDER) == 2
        assert not session.finished
        await say(session, "I never got the text")
        assert "can take a minute to arrive" in harness.spoken[-1]
        assert not session.link_open
        await say(session, "okay, I have it open")
        assert session.link_open
        assert "Live Camera" in harness.spoken[-1]
        assert not any("One. Two. Three." in text for text in harness.spoken)
        await say(session, "what do I do now")
        assert harness.spoken[-1] == policy.INTEGRATED_WAITING
        await session.disconnect()
    asyncio.run(scenario())


def test_countdown_only_after_page_reports_calibration_ready(harness):
    async def scenario():
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        await say(session, "ready")
        assert "Live Camera" in harness.spoken[-1]
        harness.walks = [
            harness.view("ready", 3, "calibration_completed"),
            harness.view("capturing", 4, "capture_started"),
            harness.view("captured", 5, "capture_completed"),
            harness.view("saved", 6, session_id="gait-session"),
        ]
        await session._background
        spoken = harness.spoken
        ready = next(i for i, text in enumerate(spoken) if "One. Two. Three." in text)
        assert "fifteen seconds" in spoken[ready]
        assert spoken.index(policy.INTEGRATED_CAMERA_SETUP) < ready
        assert any("camera is recording" in text for text in spoken[ready:])
        assert any("your walk was recorded" in text for text in spoken[ready:])
        assert spoken[-1] == policy.INTEGRATED_SAVED
    asyncio.run(scenario())


def test_page_activity_counts_as_link_open_and_ready_reply_does_not_repeat_setup(harness):
    async def scenario():
        harness.walks = [harness.view("calibrating", 2, "calibration_started")]
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        await asyncio.sleep(0.01)
        assert session.link_open
        await session.handle_silence()
        assert policy.INTEGRATED_LINK_REMINDER not in harness.spoken
        await say(session, "ready")
        assert harness.spoken[-1] == policy.INTEGRATED_PAGE_SEEN
        await say(session, "I did not get the text")
        assert harness.spoken[-1] == policy.INTEGRATED_WAITING
        await session.disconnect()
    asyncio.run(scenario())


def test_page_ready_event_opens_link_and_gives_camera_setup_once(harness):
    async def scenario():
        harness.walks = [harness.view("page_ready", 2, "page_ready")]
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        await asyncio.sleep(0.01)
        assert session.link_open
        assert harness.spoken[-1] == policy.INTEGRATED_PAGE_OPENED
        await session.handle_silence()
        assert policy.INTEGRATED_LINK_REMINDER not in harness.spoken
        await say(session, "ready")
        assert harness.spoken[-1] == policy.INTEGRATED_PAGE_SEEN
        assert policy.INTEGRATED_CAMERA_SETUP not in harness.spoken
        assert harness.spoken.count(policy.INTEGRATED_PAGE_OPENED) == 1
        assert not any("One. Two. Three." in text for text in harness.spoken)
        await say(session, "I did not get the text")
        assert harness.spoken[-1] == policy.INTEGRATED_WAITING
        await session.disconnect()
    asyncio.run(scenario())


def test_camera_error_on_freshly_opened_page_is_not_hidden(harness):
    async def scenario():
        harness.walks = [harness.view("page_ready", 3, "permission_denied")]
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        await asyncio.sleep(0.01)
        assert session.link_open
        assert harness.spoken[-1] == policy.INTEGRATED_PERMISSION_DENIED
        assert policy.INTEGRATED_PAGE_OPENED not in harness.spoken
        await session.disconnect()
    asyncio.run(scenario())


def test_verbal_ready_and_elapsed_timer_never_mean_saved(harness):
    async def scenario():
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        await say(session, "I am ready")
        await session._background
        assert session.finished
        assert not any("start capture" in text for text in harness.spoken)
        assert not any("confirms your walking test is saved" in text for text in harness.spoken)
        assert "cannot confirm" in harness.spoken[-1]
    asyncio.run(scenario())


def test_stop_during_blocked_walking_poll_is_responsive(harness):
    async def scenario():
        harness.block_walk = True
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        await asyncio.wait_for(say(session, "stop"), 0.1)
        assert session.finished
        assert store.get_call("patient1", session.call.call_id)["call_status"] == "stopped"
        assert not any("walking test is saved" in text for text in harness.spoken)
    asyncio.run(scenario())


def test_pause_defers_guidance_and_resume_observes_saved_status(harness):
    async def scenario():
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        await say(session, "pause")
        harness.walks = [harness.view("saved", 5, session_id="gait-session")]
        await asyncio.sleep(0.01)
        assert not session.finished
        assert harness.walks
        await say(session, "resume")
        await session._background
        assert "confirms your walking test is saved" in harness.spoken[-1]
    asyncio.run(scenario())


def test_wrong_attempt_and_saved_without_session_never_claim_success(harness):
    async def scenario():
        view = harness.view("saved", 1)
        harness.walks = [view]
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        view = harness.view("saved", 2, session_id="gait-session")
        view["attempt_id"] = "wrong-attempt"
        harness.walks = [view]
        await session._background
        assert not any("confirms your walking test is saved" in text for text in harness.spoken)
        assert "does not match" in harness.spoken[-1]
    asyncio.run(scenario())


def signed(path, form):
    body = ENV["PUBLIC_BASE_URL"] + path + "".join(key + form[key] for key in sorted(form))
    digest = hmac.new(ENV["TWILIO_AUTH_TOKEN"].encode(), body.encode(), hashlib.sha1).digest()
    return {"X-Twilio-Signature": base64.b64encode(digest).decode()}


def test_operator_contract_and_signed_callback_replay(harness):
    app = phone_app.create_app(load_settings(ENV), OperatorAuth(TOKEN), integrated_service=harness.service)
    with TestClient(app) as client:
        assert client.post("/api/calls", json=harness.payload.model_dump()).status_code == 401
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        response = client.post("/api/calls", json=harness.payload.model_dump())
        assert response.status_code == 200
        assert "call" not in response.json()
        PhoneSnapshot.model_validate(response.json())
        path = f"/twilio/status?call_id={harness.payload.call_id}"
        form = {"AccountSid": ENV["TWILIO_ACCOUNT_SID"], "CallSid": "CAfake1", "CallStatus": "completed", "SequenceNumber": "3"}
        assert client.post(path, data=form).status_code == 403
        assert client.post(path, data=form, headers=signed(path, form)).status_code == 204
        form.update(CallStatus="ringing", SequenceNumber="2")
        assert client.post(path, data=form, headers=signed(path, form)).status_code == 204
        result = client.get(f"/api/calls/{harness.payload.call_id}").json()
        assert result["call_status"] == "completed"
        form["CallSid"] = "CAwrong"
        assert client.post(path, data=form, headers=signed(path, form)).status_code == 409
        assert client.post(f"/api/calls/{harness.payload.call_id}/survey-retries").status_code == 409


def test_stream_ticket_and_call_binding(harness):
    app = phone_app.create_app(load_settings(ENV), OperatorAuth(TOKEN), integrated_service=harness.service)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        snapshot = client.post("/api/calls", json=harness.payload.model_dump()).json()
        path = f"/twilio/voice?call_id={harness.payload.call_id}"
        form = {"AccountSid": ENV["TWILIO_ACCOUNT_SID"], "CallSid": "CAfake1"}
        result = client.post(path, data=form, headers=signed(path, form))
        assert result.status_code == 200
        assert 'name="callId"' in result.text and 'name="streamToken"' in result.text
        call = asyncio.run(harness.service.begin_stream(harness.payload.call_id, "CAfake1"))
        assert call.attempt_id == harness.payload.attempt_id
        assert snapshot["phone_session_id"]
        with pytest.raises(HTTPException):
            asyncio.run(harness.service.begin_stream(harness.payload.call_id, "CAfake1"))
        assert "<Hangup/>" in client.post(path, data=form, headers=signed(path, form)).text


def test_sms_callback_requires_signature_and_message_binding(harness):
    async def prepare():
        session = await harness.session()
        await answer_survey(session)
        await harness.walk_requested.wait()
        await session.disconnect()
    asyncio.run(prepare())
    app = phone_app.create_app(load_settings(ENV), OperatorAuth(TOKEN), integrated_service=harness.service)
    with TestClient(app) as client:
        path = f"/twilio/sms-status?call_id={harness.payload.call_id}&attempt=0"
        form = {"AccountSid": ENV["TWILIO_ACCOUNT_SID"], "MessageSid": "SMfake1", "MessageStatus": "delivered"}
        assert client.post(path, data=form).status_code == 403
        assert client.post(path, data=form, headers=signed(path, form)).status_code == 204
        assert client.post(path, data=form, headers=signed(path, form)).status_code == 204
        form["MessageSid"] = "SMwrong"
        assert client.post(path, data=form, headers=signed(path, form)).status_code == 409
        assert store.get_call("patient1", harness.payload.call_id)["sms_status"] == "delivered"


def test_integrated_media_rejects_valid_ticket_for_wrong_session_scope(harness):
    class Socket:
        closed = False

        async def close(self):
            self.closed = True

    async def scenario():
        await harness.service.start(harness.payload)
        tickets = phone_app.StreamTickets()
        token = tickets.issue("wrong-session")
        socket = Socket()
        bridge = phone_app.MediaStreamBridge(
            socket, load_settings(ENV), phone_app.InMemoryPatientRepository(),
            phone_app.InMemoryPersistence(), tickets, integrated_service=harness.service,
        )
        await bridge._on_start({"start": {
            "callSid": "CAfake1", "streamSid": "MZoffline",
            "customParameters": {
                "sessionId": "wrong-session", "callId": harness.payload.call_id, "streamToken": token,
            },
        }})
        assert socket.closed
        assert bridge.session is None
        assert not harness.service.receipt(harness.payload.call_id).stream_started
    asyncio.run(scenario())


def test_cancelled_speech_clears_provider_playback():
    class Socket:
        sent = []

        async def send_text(self, text):
            self.sent.append(json.loads(text))

    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def block_speech(text, mark):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        socket = Socket()
        bridge = phone_app.MediaStreamBridge(
            socket, load_settings(ENV), phone_app.InMemoryPatientRepository(),
            phone_app.InMemoryPersistence(), phone_app.StreamTickets(),
        )
        bridge.stream_sid = "MZoffline"
        with patch.object(bridge, "_stream_speech", block_speech):
            speech = asyncio.create_task(bridge._speak("A synthetic prompt."))
            await started.wait()
            speech.cancel()
            with pytest.raises(asyncio.CancelledError):
                await speech
            await asyncio.wait_for(cancelled.wait(), 0.1)
        assert socket.sent[-1]["event"] == "clear"
        assert bridge._playback is None
    asyncio.run(scenario())


def test_signature_covers_repeated_form_values():
    form = httpx.QueryParams([("AccountSid", "ACoffline"), ("Extra", "a"), ("Extra", "b")])
    request = httpx.Request("POST", "https://phone.example.test/twilio/status", content=str(form))
    body = str(request.url) + "AccountSidACofflineExtraaExtrab"
    digest = hmac.new(ENV["TWILIO_AUTH_TOKEN"].encode(), body.encode(), hashlib.sha1).digest()
    data = FormData(form.multi_items())
    signature = base64.b64encode(digest).decode()
    assert phone_app.twilio.validate_form_signature(ENV["TWILIO_AUTH_TOKEN"], str(request.url), data, signature)
    assert not phone_app.twilio.validate_form_signature(ENV["TWILIO_AUTH_TOKEN"], str(request.url), data, "é")


def test_default_app_needs_no_secrets_and_never_uses_demo_identity():
    with patch.dict("os.environ", {}, clear=True):
        app = phone_app.create_app()
        with TestClient(app) as client:
            assert client.get("/api/config").json() == {"mode": "integrated", "ready": False}
            assert client.post("/api/calls", json={"patient_code": "RGN-0417"}).status_code == 503


def test_provider_adapter_uses_no_retries_and_includes_delivery_callback():
    async def scenario():
        seen = []

        def respond(request):
            seen.append(request)
            return httpx.Response(503)

        provider = TwilioProvider(load_settings(ENV), transport=httpx.MockTransport(respond))
        with pytest.raises(ProviderUnknown):
            await provider.sms("+14155550123", "offline message", "https://phone.example.test/callback")
        assert len(seen) == 1
        assert b"StatusCallback=" in seen[0].content
    asyncio.run(scenario())


def test_provider_rejection_logs_only_twilio_error_code(caplog):
    async def scenario():
        def respond(request):
            return httpx.Response(400, json={
                "code": 21211, "status": 400,
                "message": "The 'To' number +14155550123 is not a valid phone number.",
            })

        provider = TwilioProvider(load_settings(ENV), transport=httpx.MockTransport(respond))
        with caplog.at_level("WARNING"), pytest.raises(ProviderRejected):
            await provider.call("+14155550123", "https://phone.example.test/voice", "https://phone.example.test/status")

    asyncio.run(scenario())
    assert "error code 21211" in caplog.text
    assert "4155550123" not in caplog.text
    assert "not a valid" not in caplog.text


def test_backend_rejects_redirect_and_maps_timeout_without_retry():
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(307, headers={"Location": "https://elsewhere.example.test"})

        backend = MainBackend("http://127.0.0.1:8000", SERVICE_TOKEN, transport=httpx.MockTransport(respond))
        with pytest.raises(BackendError):
            await backend.patient("patient1")
        assert len(requests) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("continued", [True, False])
def test_integrated_media_preserves_hesitations_until_continuation_or_timeout(harness, continued):
    async def scenario():
        session = await harness.session()
        assert session.stage == "condition"
        harness.spoken.clear()

        class Transcriber(phone_app.DeepgramTranscriber):
            async def events(self):
                yield SpeechEvent("transcript", "I'd say, like,", True)
                yield SpeechEvent("utterance_end")
                assert session.pending_transcript == "I'd say, like,"
                assert harness.spoken == []
                assert session.engine.session.answers == []
                if continued:
                    yield SpeechEvent("transcript", "moderate.", True)
                    yield SpeechEvent("utterance_end")

        bridge = phone_app.MediaStreamBridge(
            AsyncMock(spec=phone_app.WebSocket), load_settings(ENV),
            phone_app.InMemoryPatientRepository(), phone_app.InMemoryPersistence(),
            phone_app.StreamTickets(), integrated_service=harness.service,
        )
        bridge.session = session
        bridge.transcriber = Transcriber(api_key="offline")
        bridge._greeted.set()
        try:
            await bridge._pump_speech_events()
            if not continued:
                bridge._cancel_silence_timer()
                await bridge._silence_watchdog(0)
            assert session.pending_transcript == ""
            assert len(harness.spoken) == 1
            if continued:
                answer = session.engine.session.answers[0]
                assert answer.normalized_value == "moderate"
                assert answer.confirmed
            else:
                assert session.engine.session.answers == []
        finally:
            bridge._cancel_silence_timer()
            await session.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["completed", "busy", "no-answer", "failed", "canceled"])
def test_carrier_completion_during_submission_preserves_confirmed_intake(harness, status):
    async def scenario():
        session = await harness.session()
        submitting = asyncio.Event()
        release = asyncio.Event()
        original_submit = harness.backend.submit

        async def delayed_submit(payload):
            submitting.set()
            await release.wait()
            return await original_submit(payload)

        try:
            with patch.object(harness.backend, "submit", delayed_submit):
                await answer_survey(session)
                await asyncio.wait_for(submitting.wait(), 1)
                await harness.service.carrier(session.call.call_id, "CAfake1", status, sequence=1)
                current = store.get_call("patient1", session.call.call_id)
                assert current["call_status"] in {"completed", "failed"}
                assert current["survey_status"] == "in_progress"
                release.set()
                await asyncio.wait_for(harness.walk_requested.wait(), 1)
            assert store.get_call("patient1", session.call.call_id)["survey_status"] == "stored"
            assert len(store.get_patient("patient1")["surveys"]) == 2
            await harness.service.retry_submission(session.call.call_id)
            assert len(store.get_patient("patient1")["surveys"]) == 2
            assert harness.provider.messages == 1
        finally:
            release.set()
            await session.disconnect()

    asyncio.run(scenario())


def test_carrier_completion_before_confirmation_still_requires_review(harness):
    async def scenario():
        session = await harness.session()
        await say(session, "7")
        await harness.service.carrier(session.call.call_id, "CAfake1", "completed", sequence=1)
        assert store.get_call("patient1", session.call.call_id)["survey_status"] == "needs_review"
        assert harness.submissions == []
        await session.disconnect()

    asyncio.run(scenario())


TEXT_QUESTION = IntakeQuestion("last_fall_description", "Tell me about your most recent fall.", "text")
# The spoken intake is deliberately short; the free-text guardrails are kept
# tested against this longer bank so they stay safe if a question is re-added.
LONG_QUESTIONS = (
    *QUESTIONS[:2],
    IntakeQuestion("injured", "Were you hurt?", "boolean"),
    TEXT_QUESTION,
    QUESTIONS[2],
    IntakeQuestion("dizziness_notes", "Anything about the dizziness?", "text"),
    IntakeQuestion("primary_complaints", "What bothers you most?", "complaints"),
)


def test_spoken_intake_is_three_short_questions_then_the_survey():
    assert [question.key for question in QUESTIONS] == ["pain_scale", "falls_last_6_months", "dizziness"]
    assert all(len(question.prompt.split()) <= 12 for question in QUESTIONS)
    intake = GenericIntake()
    assert "scale of one to ten" in intake.prompt()
    intake.handle("about a five")
    intake.handle("none")
    assert intake.handle("no") == "Thanks."
    assert intake.complete
    assert intake.payload() == {
        "pain_scale": 5,
        "fall_history": {"falls_last_6_months": 0, "injured": None, "last_fall_description": None},
        "dizziness": False,
        "dizziness_notes": None,
        "primary_complaints": None,
    }


@pytest.mark.parametrize("answer,notes,complaints,readback", [
    (" NONE. ", None, [], "none reported"),
    ("unknown", None, None, "unknown"),
    ("none since surgery", "none since surgery", ["none since surgery"], "none since surgery"),
])
def test_generic_intake_distinguishes_no_content_from_unknown(monkeypatch, answer, notes, complaints, readback):
    monkeypatch.setattr(generic_intake, "QUESTIONS", LONG_QUESTIONS)
    intake = GenericIntake()
    for index, response in enumerate(["7", "0", "no", answer, "no", answer, answer]):
        prompt = intake.handle(response)
        if index in {3, 5, 6} and notes is not None:
            assert f'I heard "{readback}".' in prompt
            assert intake.index == index
            intake.handle("yes")
    assert intake.complete
    assert intake.values["last_fall_description"] == notes
    assert intake.values["dizziness_notes"] == notes
    assert intake.values["primary_complaints"] == complaints


def test_absent_notes_are_stored_without_confirmation_but_free_text_is_read_back(monkeypatch):
    monkeypatch.setattr(generic_intake, "QUESTIONS", LONG_QUESTIONS)
    intake = GenericIntake()
    for answer in ["7", "0", "no"]:
        intake.handle(answer)
    intake.handle("none")
    assert intake.values["last_fall_description"] is None
    intake.handle("no")
    assert 'I heard "Why do you need to know?"' in intake.handle("Why do you need to know?")
    assert "dizziness_notes" not in intake.values
    intake.handle("no")
    intake.handle("fell on stairs")
    intake.handle("yes")
    assert intake.values["dizziness_notes"] == "fell on stairs"


@pytest.mark.parametrize("text,value", [
    ("I am not dizzy", False), ("I'm not dizzy", False), ("I do not think so", False), ("no I haven't", False),
    ("I don't get dizzy", False), ("yes I am", True), ("I have been", True),
    ("I am definitely not dizzy", False), ("I have never fallen", False), ("I'm really not", False),
])
def test_negated_boolean_replies_are_not_affirmative(text, value):
    reading = lenient_parse("boolean", text)
    assert (reading.valid, reading.value) == (True, value)


@pytest.mark.parametrize("text", ["5.5", "five point five", "seven and a half", "5,5"])
def test_fractional_ratings_are_not_rounded(text):
    assert lenient_parse("pain", text).valid is False


def test_time_words_do_not_invalidate_counts():
    assert lenient_parse("count", "I fell once last quarter").value == 1
    assert lenient_parse("count", "one and a half").valid is False
    for text in ("one half", "a half", "one quarter", "three quarters"):
        assert lenient_parse("pain", text).valid is False, text


@pytest.mark.parametrize("text", ["I am dizzy, but not often", "yes and no", "I have, but not lately"])
def test_mixed_boolean_replies_are_reasked(text):
    assert lenient_parse("boolean", text).valid is False


def test_provider_failure_keeps_the_deterministic_reading():
    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise TimeoutError("provider down")

    interpreter = OpenAIIntakeInterpreter(Client())
    reading = interpreter.interpret(TEXT_QUESTION, "fell on stairs")
    assert (reading.valid, reading.value, reading.clear) == (True, "fell on stairs", False)
    assert interpreter.interpret(QUESTIONS[0], "it's been rough").valid is False


def test_model_mode_reasks_free_text_the_model_calls_off_topic():
    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    class Choice:
                        finish_reason = "stop"
                        message = type("M", (), {"refusal": None, "content": json.dumps(
                            {"value": None, "unknown": False, "unclear": True}
                        )})()
                    return type("R", (), {"choices": [Choice()]})()

    interpreter = OpenAIIntakeInterpreter(Client())
    assert interpreter.interpret(TEXT_QUESTION, "Why do you need to know?").valid is False
    assert interpreter.interpret(QUESTIONS[0], "about a five").value == 5

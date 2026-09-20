from __future__ import annotations

import asyncio
import base64
import json

import pytest
from websockets.exceptions import ConnectionClosedError

import phone_app
from app.conversation_policy import sms_body
from app.patient_repository import InMemoryPatientRepository
from app.survey_engine import SafeSurveyEngine
from app.telephony import call_session, twilio
from app.telephony.call_session import PhoneCallSession
from app.telephony.config import TelephonyConfigurationError, load_settings
from app.telephony.deepgram_stt import DeepgramTranscriber, listen_url, parse_message
from app.telephony.deepgram_tts import MULAW_FRAME_BYTES, frames, speak_url

ENV = {
    "DEEPGRAM_API_KEY": "dg-key",
    "TWILIO_ACCOUNT_SID": "AC123",
    "TWILIO_AUTH_TOKEN": "token",
    "TWILIO_FROM_NUMBER": "+15005550006",
    "PUBLIC_BASE_URL": "https://tunnel.example.com/",
}


def build_session(patient_code: str = "RGN-0417"):
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    engine = SafeSurveyEngine(InMemoryPatientRepository(), patient_code)
    return PhoneCallSession(engine, speak, session_id="sess-1"), spoken


def test_settings_build_public_urls():
    settings = load_settings(ENV)
    assert settings.ready
    assert settings.webhook_url("/twilio/voice") == "https://tunnel.example.com/twilio/voice"
    assert settings.websocket_url("/twilio/media") == "wss://tunnel.example.com/twilio/media"


def test_settings_require_outbound_lists_missing_values():
    settings = load_settings({"DEEPGRAM_API_KEY": "dg-key"})
    with pytest.raises(TelephonyConfigurationError) as excinfo:
        settings.require_outbound()
    assert "TWILIO_ACCOUNT_SID" in str(excinfo.value)
    assert "PUBLIC_BASE_URL" in str(excinfo.value)


def test_media_stream_twiml_passes_custom_parameters():
    xml = twilio.media_stream_twiml(
        "wss://tunnel.example.com/twilio/media",
        {"patientCode": "RGN-0417", "sessionId": "sess-1"},
    )
    assert '<Stream url="wss://tunnel.example.com/twilio/media">' in xml
    assert '<Parameter name="patientCode" value="RGN-0417"/>' in xml
    assert "<Connect>" in xml


def test_require_e164_rejects_local_format():
    with pytest.raises(ValueError):
        twilio.require_e164("415-555-0123")
    assert twilio.require_e164(" +1 415 555 0123 ") == "+14155550123"


def test_place_call_retries_without_status_callback_on_trial_account(monkeypatch):
    attempts: list[list[tuple[str, str]]] = []

    def fake_post(account_sid, auth_token, fields, timeout):
        attempts.append(fields)
        if any(name == "StatusCallback" for name, _ in fields):
            raise twilio.TwilioError(
                "Twilio rejected the call request (400): "
                "trial accounts have limited parameter access"
            )
        return {"sid": "CA123", "status": "queued", "to": "+14155550123"}

    monkeypatch.setattr(twilio, "_post_call", fake_post)
    placed = twilio.place_call(
        account_sid="AC1",
        auth_token="token",
        to_number="+14155550123",
        from_number="+14155550100",
        answer_url="https://tunnel.example.com/twilio/voice",
        status_callback_url="https://tunnel.example.com/twilio/status",
    )

    assert placed.call_sid == "CA123"
    assert len(attempts) == 2
    assert not any(name == "StatusCallback" for name, _ in attempts[1])


def test_place_call_reraises_other_twilio_errors(monkeypatch):
    def fake_post(account_sid, auth_token, fields, timeout):
        raise twilio.TwilioError("Twilio rejected the call request (401): unauthorized")

    monkeypatch.setattr(twilio, "_post_call", fake_post)
    with pytest.raises(twilio.TwilioError):
        twilio.place_call(
            account_sid="AC1",
            auth_token="token",
            to_number="+14155550123",
            from_number="+14155550100",
            answer_url="https://tunnel.example.com/twilio/voice",
            status_callback_url="https://tunnel.example.com/twilio/status",
        )


def test_validate_signature_matches_twilio_algorithm():
    token = "12345"
    url = "https://tunnel.example.com/twilio/voice"
    params = {"CallSid": "CA1", "From": "+14155550123"}
    payload = url + "CallSidCA1" + "From+14155550123"
    import hashlib
    import hmac

    expected = base64.b64encode(
        hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()
    assert twilio.validate_signature(token, url, params, expected)
    assert not twilio.validate_signature(token, url, params, "wrong")


def test_listen_and_speak_urls_use_telephony_audio_format():
    listen = listen_url("nova-3", 1200)
    assert "encoding=mulaw" in listen and "sample_rate=8000" in listen
    assert "utterance_end_ms=1200" in listen
    speak = speak_url("aura-2-thalia-en")
    assert "encoding=mulaw" in speak and "container=none" in speak


def test_listen_url_boosts_the_answer_words_per_model_family():
    listen = listen_url("nova-3", 1200)
    assert "keyterm=moderate" in listen and "keyterm=mild" in listen
    phonecall = listen_url("nova-2-phonecall", 1200)
    assert "keyterm=" not in phonecall
    assert "keywords=moderate%3A1.5" in phonecall
    assert "keyterm" not in listen_url("nova-3", 1200, keyterms=())


def test_parse_message_normalizes_deepgram_events():
    results = json.dumps(
        {"type": "Results", "is_final": True, "channel": {"alternatives": [{"transcript": "mild"}]}}
    )
    event = parse_message(results)
    assert event is not None and event.kind == "transcript"
    assert event.text == "mild" and event.is_final

    assert parse_message(json.dumps({"type": "UtteranceEnd"})).kind == "utterance_end"
    assert parse_message(json.dumps({"type": "SpeechStarted"})).kind == "speech_started"
    empty = json.dumps({"type": "Results", "channel": {"alternatives": [{"transcript": "  "}]}})
    assert parse_message(empty) is None


def test_frames_split_audio_into_twenty_millisecond_chunks():
    chunks = frames(b"\xff" * (MULAW_FRAME_BYTES * 2 + 40))
    assert [len(chunk) for chunk in chunks] == [MULAW_FRAME_BYTES, MULAW_FRAME_BYTES, 40]


def test_call_session_runs_a_confirmed_answer():
    session, spoken = build_session()
    asyncio.run(session.begin())
    assert "check-in from your doctor’s office" in spoken[0]
    assert "hip pain" in spoken[0]
    assert "HOOS JR HIP SURVEY" not in spoken[0]
    assert spoken[0].count("automated check-in") == 1

    session.add_transcript("moderate")
    assert asyncio.run(session.flush_utterance()) is False
    record = session.persistence.calls["sess-1"]
    assert record.answers == [{"question_id": "hoos_stairs", "value": "moderate"}]


def test_call_session_escalates_after_repeated_silence():
    session, spoken = build_session()

    async def scenario() -> bool:
        await session.begin()
        assert await session.handle_silence() is False
        assert await session.handle_silence() is False
        return await session.handle_silence()

    assert asyncio.run(scenario()) is True
    assert session.engine.session.needs_human_review
    assert "clinician follow up" in spoken[-1]
    assert session.persistence.calls["sess-1"].final_status == "escalated"


def test_call_session_hangs_up_when_the_patient_asks_to_stop():
    session, spoken = build_session()

    async def scenario() -> bool:
        await session.begin()
        session.add_transcript("please stop")
        return await session.flush_utterance()

    assert asyncio.run(scenario()) is True
    assert session.finished
    assert not session.engine.session.needs_human_review
    assert "stop" in spoken[-1].lower()
    record = session.persistence.calls["sess-1"]
    assert (record.status, record.final_status) == ("completed", "stopped")


def test_call_session_completes_and_prepares_handoff():
    session, spoken = build_session()

    async def scenario() -> None:
        await session.begin()
        for _ in range(len(session.engine.session.questions)):
            session.add_transcript("none")
            await session.flush_utterance()

    asyncio.run(scenario())
    assert session.handoff is not None
    # The survey's own closing is dropped in favour of the gait request, so the
    # caller is not thanked and sent off twice in a row.
    assert not any("survey is complete" in line for line in spoken)
    assert any("short video of you walking" in line for line in spoken)


def test_call_session_texts_the_gait_link_and_walks_through_setup(monkeypatch):
    monkeypatch.setenv("GAIT_CHECKER_BASE_URL", "https://walk.example.org")
    monkeypatch.setenv("PATIENT_LINK_SIGNING_SECRET", "s" * 32)
    sent: list[tuple[str, str]] = []

    async def sms_sender(to_number: str, body: str) -> None:
        sent.append((to_number, body))

    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    session = PhoneCallSession(
        engine,
        speak,
        session_id="sess-2",
        to_number="+14155550123",
        sms_sender=sms_sender,
        walk_seconds=0.0,
    )

    async def survey() -> bool:
        await session.begin()
        done = False
        for _ in range(len(session.engine.session.questions)):
            session.add_transcript("none")
            done = await session.flush_utterance()
        return done

    assert asyncio.run(survey()) is False
    assert session.finished is False
    assert session.handoff is not None
    assert session.handoff.sms_sent is True
    assert sent == [
        ("+14155550123", sms_body(session.handoff.link or "")),
    ]
    assert session.handoff.link is not None and session.handoff.link in sent[0][1]
    assert session.handoff.link.startswith("https://walk.example.org/patient/RGN-0417?token=")

    # The caller is asked to open the link and nothing else is said until they do.
    assert "short video of you walking" in spoken[-2]
    assert "tell me when" in spoken[-1]
    assert not any("Live Camera" in line for line in spoken)

    async def acknowledge() -> bool:
        session.add_transcript("okay I have it open")
        return await session.flush_utterance()

    assert asyncio.run(acknowledge()) is True
    assert session.finished
    assert "Live Camera" in spoken[-3]
    assert "fifteen seconds" in spoken[-2]
    assert "One. Two. Three." in spoken[-2]
    assert "Take care of yourself" in spoken[-1]
    assert session.persistence.calls["sess-2"].final_status == "complete"


def test_call_session_goes_ahead_when_the_caller_never_confirms_the_link():
    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    session = PhoneCallSession(
        engine, speak, session_id="sess-4", max_silent_reprompts=1, walk_seconds=0.0
    )

    async def scenario() -> bool:
        await session.begin()
        for _ in range(len(session.engine.session.questions)):
            session.add_transcript("none")
            await session.flush_utterance()
        assert await session.handle_silence() is False
        assert "say ‘ready’" in spoken[-1]
        return await session.handle_silence()

    assert asyncio.run(scenario()) is True
    # A quiet caller after a finished survey is walked through it, not escalated.
    assert session.engine.session.needs_human_review is False
    assert "Take care of yourself" in spoken[-1]


def test_call_session_still_completes_when_sms_sending_fails():
    async def failing_sms_sender(to_number: str, body: str) -> None:
        raise RuntimeError("Twilio is down")

    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    session = PhoneCallSession(
        engine,
        speak,
        session_id="sess-3",
        to_number="+14155550123",
        sms_sender=failing_sms_sender,
        walk_seconds=0.0,
    )

    async def scenario() -> None:
        await session.begin()
        for _ in range(len(session.engine.session.questions)):
            session.add_transcript("none")
            await session.flush_utterance()
        session.add_transcript("ready")
        await session.flush_utterance()

    asyncio.run(scenario())

    assert session.finished
    assert session.handoff is not None
    assert session.handoff.sms_sent is False
    assert "Take care of yourself" in spoken[-1]
    assert session.persistence.calls["sess-3"].final_status == "complete"


def test_speech_chunks_split_long_prompts_into_sentence_groups():
    text = "Hello there. " + "This sentence is here to make the prompt long. " * 6
    chunks = phone_app.speech_chunks(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= 220 for chunk in chunks)
    assert "".join(chunk + " " for chunk in chunks).split() == text.split()


def test_speech_chunks_keep_a_short_prompt_whole():
    assert phone_app.speech_chunks("How is your hip today?") == ["How is your hip today?"]


def test_speech_plan_pauses_between_paragraphs():
    plan = phone_app.speech_plan("Hello there.\n\nQuestion 1 of 6. How is your hip?")

    assert [chunk for chunk, _ in plan] == ["Hello there.", "Question 1 of 6. How is your hip?"]
    assert plan[0][1] == phone_app.PARAGRAPH_PAUSE_SECONDS
    assert plan[-1][1] == 0.0


def test_spoken_keeps_the_beat_before_the_first_question():
    spoken = call_session._spoken(
        "HOOS JR HIP SURVEY\nHello there.\n\nQuestion 1 of 6. How is your hip?"
    )

    assert spoken == "Hello there.\n\nQuestion 1 of 6. How is your hip?"


class DroppingSocket:
    """A Deepgram socket that dies once, the way a keepalive timeout does."""

    def __init__(self, fails: bool):
        self.fails = fails
        self.sent: list[bytes] = []
        self.closed = False

    async def send(self, frame) -> None:
        if self.fails:
            raise ConnectionClosedError(None, None)
        self.sent.append(frame)

    async def close(self) -> None:
        self.closed = True


def test_transcriber_reopens_a_socket_that_dropped_mid_call():
    """A dead STT socket must not cost us the rest of the caller's audio."""

    sockets = [DroppingSocket(fails=True), DroppingSocket(fails=False)]
    opened: list[DroppingSocket] = []

    async def connect(url, **kwargs):
        opened.append(sockets[len(opened)])
        return opened[-1]

    async def run() -> None:
        async with DeepgramTranscriber("dg-key", connect=connect) as transcriber:
            await transcriber.send_audio(b"\xff" * 160)

    asyncio.run(run())

    assert len(opened) == 2
    assert opened[1].sent[0] == b"\xff" * 160
    assert opened[1].closed

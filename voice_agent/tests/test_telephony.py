from __future__ import annotations

import asyncio
import base64
import json

import pytest
from websockets.exceptions import ConnectionClosedError

import phone_app
from app import gait_handoff
from app.conversation_policy import sms_body
from app.patient_repository import InMemoryPatientRepository
from app.survey_engine import SafeSurveyEngine
from app.telephony import call_session, twilio
from app.telephony.call_session import PhoneCallSession
from app.telephony.config import TelephonyConfigurationError, load_settings
from app.telephony.deepgram_stt import DeepgramTranscriber, listen_url, parse_message
from app.telephony.deepgram_tts import MULAW_FRAME_BYTES, frames, speak_url
from app.telephony.stream_tickets import StreamTickets

ENV = {
    "DEEPGRAM_API_KEY": "dg-key",
    "TWILIO_ACCOUNT_SID": "AC123",
    "TWILIO_AUTH_TOKEN": "token",
    "TWILIO_FROM_NUMBER": "+15005550006",
    "PUBLIC_BASE_URL": "https://tunnel.example.com/",
}


FAKE_LINK = "https://walk.example.org/patient/RGN-0417?token=stub-token"


def _stub_backend_link(monkeypatch, link: str = FAKE_LINK):
    """Stand in for the backend's ``POST /api/voice/patient-link``."""

    async def fetch(self, patient_code: str, call_id: str | None = None) -> str:
        return link

    monkeypatch.setattr(gait_handoff.BackendLinkClient, "fetch", fetch)


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
    # The survey's own closing is dropped in favour of the gait closing, so the
    # caller is not thanked and sent off twice in a row.
    assert not any("survey is complete" in line for line in spoken)
    assert spoken[-1].startswith("Thank you for those answers.")


def test_call_session_texts_the_gait_link_and_walks_through_setup(monkeypatch):
    _stub_backend_link(monkeypatch)
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


def test_call_session_closes_honestly_when_the_caller_never_confirms_the_link(monkeypatch):
    _stub_backend_link(monkeypatch)

    async def sms_sender(to_number: str, body: str) -> None:
        return None

    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    session = PhoneCallSession(
        engine,
        speak,
        session_id="sess-4",
        to_number="+14155550123",
        sms_sender=sms_sender,
        max_silent_reprompts=1,
        walk_seconds=0.0,
    )

    async def scenario() -> bool:
        await session.begin()
        for _ in range(len(session.engine.session.questions)):
            session.add_transcript("none")
            await session.flush_utterance()
        assert session.handoff is not None and session.handoff.sms_sent
        assert await session.handle_silence() is False
        assert "say ‘ready’" in spoken[-1]
        return await session.handle_silence()

    assert asyncio.run(scenario()) is True
    # A quiet caller after a finished survey is neither escalated nor given
    # camera steps they never confirmed they could follow.
    assert session.engine.session.needs_human_review is False
    assert "not heard back" in spoken[-1]
    assert not any("Live Camera" in line for line in spoken)
    assert session.handoff is not None
    assert "went quiet" in session.handoff.notes[-1]


def test_call_session_owns_up_when_the_gait_text_fails(monkeypatch):
    _stub_backend_link(monkeypatch)

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

    async def scenario() -> bool:
        await session.begin()
        done = False
        for _ in range(len(session.engine.session.questions)):
            session.add_transcript("none")
            done = await session.flush_utterance()
        return done

    assert asyncio.run(scenario()) is True
    assert session.finished
    assert session.handoff is not None
    assert session.handoff.sms_sent is False
    assert session.handoff.status == "failed"
    # The caller is told the truth and never asked to open a text that never came.
    assert "did not go through" in spoken[-1]
    assert not any("You should have the text" in line for line in spoken)
    assert not any("Live Camera" in line for line in spoken)
    assert session.persistence.calls["sess-3"].final_status == "complete"


def test_call_session_promises_no_text_when_no_link_can_be_made():
    session, spoken = build_session()
    session.walk_seconds = 0.0

    async def scenario() -> bool:
        await session.begin()
        done = False
        for _ in range(len(session.engine.session.questions)):
            session.add_transcript("none")
            done = await session.flush_utterance()
        return done

    assert asyncio.run(scenario()) is True
    assert session.handoff is not None
    assert session.handoff.status == "unavailable"
    assert session.handoff.sms_sent is False
    assert not any("texting you" in line for line in spoken)
    assert not any("You should have the text" in line for line in spoken)
    assert "everything for today" in spoken[-1]
    assert session.persistence.calls["sess-1"].final_status == "complete"


def test_call_session_waits_quietly_while_the_caller_is_paused():
    session, spoken = build_session()
    session.max_paused_silences = 3

    async def scenario() -> bool:
        await session.begin()
        session.add_transcript("please pause")
        assert await session.flush_utterance() is False
        assert session.engine.session.state == "paused"
        assert await session.handle_silence() is False
        assert await session.handle_silence() is False
        # Paused quiet is neither reprompted nor counted as an unanswered question.
        assert "Take your time" in spoken[-1]
        assert session.engine.session.state == "paused"
        session.add_transcript("resume")
        return await session.flush_utterance()

    assert asyncio.run(scenario()) is False
    assert session.engine.session.state == "asking"
    assert session.engine.session.needs_human_review is False


def test_call_session_lets_a_long_paused_caller_go_for_review():
    session, spoken = build_session()
    session.max_paused_silences = 2

    async def scenario() -> bool:
        await session.begin()
        session.add_transcript("pause")
        await session.flush_utterance()
        assert await session.handle_silence() is False
        return await session.handle_silence()

    assert asyncio.run(scenario()) is True
    assert session.engine.session.needs_human_review
    assert "clinician will follow up" in spoken[-1]
    assert session.persistence.calls["sess-1"].final_status == "escalated"


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


def test_transcriber_retries_a_reconnect_that_fails_transiently():
    """A DNS blip during the reconnect is retried with backoff, not fatal."""

    sockets = [DroppingSocket(fails=True), DroppingSocket(fails=False)]
    attempts: list[int] = []
    pauses: list[float] = []

    async def connect(url, **kwargs):
        attempts.append(len(attempts))
        if len(attempts) == 2:
            raise OSError("temporary failure in name resolution")
        return sockets[min(len(attempts) - 1, 1)]

    async def sleep(seconds: float) -> None:
        pauses.append(seconds)

    async def run() -> None:
        async with DeepgramTranscriber("dg-key", connect=connect, sleep=sleep) as transcriber:
            await transcriber.send_audio(b"\xff" * 160)
            await transcriber.send_audio(b"\x00" * 160)

    asyncio.run(run())

    assert attempts == [0, 1, 2]
    assert pauses == [0.5]
    assert sockets[1].sent[:2] == [b"\xff" * 160, b"\x00" * 160]


def test_transcriber_gives_up_after_bounded_reconnects_without_crashing_the_call():
    """An outage surfaces as OSError, which the media bridge drops per frame."""

    attempts: list[int] = []

    async def connect(url, **kwargs):
        attempts.append(len(attempts))
        if len(attempts) == 1:
            return DroppingSocket(fails=True)
        raise OSError("connection refused")

    async def sleep(seconds: float) -> None:
        return None

    async def run() -> list[type[BaseException]]:
        errors: list[type[BaseException]] = []
        async with DeepgramTranscriber("dg-key", connect=connect, sleep=sleep) as transcriber:
            for _ in range(2):
                try:
                    await transcriber.send_audio(b"\xff" * 160)
                except OSError as error:
                    errors.append(type(error))
        return errors

    errors = asyncio.run(run())

    assert errors == [OSError, OSError]
    assert len(attempts) == 1 + 5


def test_stream_tickets_are_single_use_and_expire():
    now = [100.0]
    tickets = StreamTickets(ttl_seconds=60, clock=lambda: now[0])

    token = tickets.issue("sess-1")
    assert tickets.redeem("sess-1", "not-it") is False
    # A wrong guess burns the ticket rather than leaving it up for another try.
    assert tickets.redeem("sess-1", token) is False

    token = tickets.issue("sess-1")
    now[0] += 61
    assert tickets.redeem("sess-1", token) is False

    token = tickets.issue("sess-2")
    assert tickets.redeem("sess-2", token) is True
    assert tickets.redeem("sess-2", token) is False


def test_place_call_reports_whether_twilio_will_send_status_callbacks(monkeypatch):
    def refuse_callbacks(account_sid, auth_token, fields, timeout):
        if any(name == "StatusCallback" for name, _ in fields):
            raise twilio.TwilioError("(400): trial accounts have limited parameter access")
        return {"sid": "CA1", "status": "queued", "to": "+14155550123"}

    def accept_everything(account_sid, auth_token, fields, timeout):
        return {"sid": "CA2", "status": "queued", "to": "+14155550123"}

    kwargs = dict(
        account_sid="AC1",
        auth_token="token",
        to_number="+14155550123",
        from_number="+14155550100",
        answer_url="https://tunnel.example.com/twilio/voice",
        status_callback_url="https://tunnel.example.com/twilio/status",
    )
    monkeypatch.setattr(twilio, "_post_call", refuse_callbacks)
    assert twilio.place_call(**kwargs).status_callback is False
    monkeypatch.setattr(twilio, "_post_call", accept_everything)
    assert twilio.place_call(**kwargs).status_callback is True


@pytest.mark.parametrize(
    ("said", "intent"),
    [
        ("okay I've got it open", "ready"),
        ("ready", "ready"),
        ("yes it's up", "ready"),
        ("I never got the text", "missing"),
        ("no, nothing came through yet", "missing"),
        ("it hasn't arrived", "missing"),
        ("no I didn't get it, okay", "missing"),
        ("stop", "stop"),
        ("can we do this another time", "stop"),
        ("what was the question", "unclear"),
        ("I'm not ready", "unclear"),
        ("no it's not open yet", "unclear"),
        ("I don't have it up", "unclear"),
        ("it isn't loading", "unclear"),
        ("I can't find it", "unclear"),
        ("it won't open", "unclear"),
        ("I'm not fully ready", "unclear"),
        ("I don't think it's open", "unclear"),
        ("it doesn't seem to be loading", "unclear"),
        ("the link isn't working", "unclear"),
        ("it wouldn't open before, but it's open now", "ready"),
        ("it opened before, but it isn't open now", "unclear"),
        ("I didn't get it at first but it's here now, I'm ready", "ready"),
        ("I had it open but now nothing's showing", "missing"),
        ("I didn't get it, but yes, please resend it", "missing"),
        ("I have it open now, although it didn't arrive at first", "ready"),
        ("it's not open, okay", "unclear"),
        ("I was able to open it, though it isn't open now", "unclear"),
        ("it didn't work at first, though it's open now", "ready"),
        ("I didn't get it before, but yes it arrived", "unclear"),
        ("I didn't get it before but yes it arrived", "unclear"),
        ("yes please", "ready"),
        ("I have the link open", "ready"),
        ("I received the link and opened it", "ready"),
        ("I'm ready, but I was mistaken and it isn't open", "unclear"),
        ("I had it open, but it crashed and it isn't loading", "unclear"),
        ("it isn't open, but I had successfully opened it", "unclear"),
        ("it isn't loading, although I had successfully opened it", "unclear"),
        ("it's open, but I never got the second one", "unclear"),
        ("no problem, I'm ready", "ready"),
        ("no, now I'm ready", "ready"),
        ("not bad, it's open now", "ready"),
        ("hello", "unclear"),
    ],
)
def test_link_replies_are_read_for_intent_not_just_noise(said, intent):
    assert call_session.link_reply_intent(said) == intent


def _session_waiting_on_the_link(monkeypatch):
    _stub_backend_link(monkeypatch)
    texts: list[str] = []

    async def sms_sender(to_number: str, body: str) -> None:
        texts.append(body)

    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    session = PhoneCallSession(
        engine,
        speak,
        session_id="sess-link",
        to_number="+14155550123",
        sms_sender=sms_sender,
        walk_seconds=0.0,
    )

    async def answer_everything() -> None:
        await session.begin()
        for _ in range(len(session.engine.session.questions)):
            session.add_transcript("none")
            await session.flush_utterance()

    asyncio.run(answer_everything())
    assert session.handoff is not None and session.handoff.sms_sent is True
    assert not session.finished
    return session, spoken


async def _say(session: PhoneCallSession, text: str) -> bool:
    session.add_transcript(text)
    return await session.flush_utterance()


def test_link_walkthrough_waits_for_a_real_yes(monkeypatch):
    session, spoken = _session_waiting_on_the_link(monkeypatch)

    assert asyncio.run(_say(session, "what was that?")) is False
    assert "say ‘ready’" in spoken[-1]
    assert not any("Live Camera" in line for line in spoken)

    assert asyncio.run(_say(session, "okay, got it open")) is True
    assert any("Live Camera" in line for line in spoken)
    assert session.finished
    assert session.persistence.calls["sess-link"].final_status == "complete"


def test_a_caller_who_never_gets_the_text_is_not_walked_through_a_camera(monkeypatch):
    session, spoken = _session_waiting_on_the_link(monkeypatch)

    assert asyncio.run(_say(session, "I never got the text")) is False
    assert "take a minute to arrive" in spoken[-1]
    assert asyncio.run(_say(session, "still nothing")) is True
    assert "has not reached you" in spoken[-1]
    assert not any("Live Camera" in line for line in spoken)
    assert session.finished
    assert session.handoff is not None
    assert any("never arrived" in note for note in session.handoff.notes)
    assert session.persistence.calls["sess-link"].final_status == "complete"


def test_stop_while_waiting_on_the_link_ends_the_call_politely(monkeypatch):
    session, spoken = _session_waiting_on_the_link(monkeypatch)

    assert asyncio.run(_say(session, "stop")) is True
    assert "leave it there" in spoken[-1]
    assert not any("Live Camera" in line for line in spoken)
    assert session.finished


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("I'd say, like,", True),
        ("Um, so", True),
        ("Well, it's", True),
        ("moderate.", False),
        ("I'd say, like, mild difficulty", False),
        ("no pain at all", False),
    ],
)
def test_trailing_off_spots_an_unfinished_thought(said, expected):
    session, _ = build_session()
    session.add_transcript(said)
    assert session.trailing_off() is expected


def test_the_walk_timer_waits_out_the_buffered_countdown(monkeypatch):
    """"Go ahead" is still in the phone's buffer when speak() returns."""

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("app.telephony.call_session.asyncio.sleep", fake_sleep)
    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")

    async def speak(text: str) -> None:
        return None

    session = PhoneCallSession(
        engine, speak, session_id="sess-walk", walk_seconds=15.0, speech_lead_seconds=2.0
    )
    session.persistence.start_call("sess-walk", "RGN-0417", "orthopedic")
    asyncio.run(session._walk_the_caller_through_it())
    assert slept == [17.0]

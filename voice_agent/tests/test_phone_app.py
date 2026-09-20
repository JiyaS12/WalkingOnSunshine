from __future__ import annotations

import asyncio
import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

import phone_app
from app.operator_auth import OperatorAuth
from app.telephony.config import load_settings
from app.telephony.deepgram_stt import SpeechEvent

TOKEN = "operator-secret-0123456789"
ENV = {
    "DEEPGRAM_API_KEY": "dg-key",
    "TWILIO_ACCOUNT_SID": "AC123",
    "TWILIO_AUTH_TOKEN": "token",
    "TWILIO_FROM_NUMBER": "+15005550006",
    "PUBLIC_BASE_URL": "https://tunnel.example.com",
}


class ScriptedTranscriber:
    """Stand-in for Deepgram that replays a fixed event script."""

    queue: list[SpeechEvent] = []

    def __init__(self, **kwargs):
        self.audio_frames: list[bytes] = []

    async def __aenter__(self):
        return self

    async def close(self) -> None:
        return None

    async def send_audio(self, frame: bytes) -> None:
        self.audio_frames.append(frame)

    async def events(self):
        while True:
            if ScriptedTranscriber.queue:
                yield ScriptedTranscriber.queue.pop(0)
            await asyncio.sleep(0.01)


@pytest.fixture
def client(monkeypatch):
    async def fake_tts(text: str, api_key: str, model: str) -> bytes:
        return b"\xff" * 320

    monkeypatch.setattr(phone_app, "synthesize_mulaw_async", fake_tts)
    monkeypatch.setattr(phone_app, "DeepgramTranscriber", ScriptedTranscriber)
    monkeypatch.setattr(phone_app, "_signature_ok", lambda *args, **kwargs: True)
    ScriptedTranscriber.queue = []
    client = TestClient(phone_app.create_app(load_settings(ENV), auth=OperatorAuth(TOKEN)))
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client


def test_config_reports_ready(client):
    body = client.get("/api/config").json()
    assert body == {
        "deepgram_configured": True,
        "twilio_configured": True,
        "public_base_url": "https://tunnel.example.com",
        "llm_configured": False,
        "operator_token_configured": True,
        "ready": True,
    }


def test_operator_routes_require_the_token(client, monkeypatch):
    async def fake_place_call(**kwargs):
        return phone_app.twilio.PlacedCall(
            call_sid="CA9", status="queued", to_number=kwargs["to_number"]
        )

    monkeypatch.setattr(phone_app.twilio, "place_call_async", fake_place_call)
    payload = {"to_number": "+14155550123", "patient_code": "RGN-0417"}
    session_id = client.post("/api/calls", json=payload).json()["session_id"]

    anonymous = {"Authorization": ""}
    wrong = {"Authorization": "Bearer nope"}
    assert client.post("/api/calls", json=payload, headers=anonymous).status_code == 401
    assert client.post("/api/calls", json=payload, headers=wrong).status_code == 401
    assert client.get(f"/api/calls/{session_id}", headers=anonymous).status_code == 401
    assert client.get(f"/api/calls/{session_id}").status_code == 200
    # Twilio's webhooks are signature-checked, not token-gated.
    assert client.post("/twilio/status", data={"CallSid": "CA9", "CallStatus": "ringing"}, headers=anonymous).status_code == 204


def test_dialing_is_refused_until_an_operator_token_is_configured():
    app = phone_app.create_app(load_settings(ENV), auth=OperatorAuth(None))
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/config").json()["ready"] is False
        response = anonymous.post("/api/calls", json={"to_number": "+14155550123"})
    assert response.status_code == 503
    assert "OPERATOR_TOKEN" in response.json()["detail"]


def test_voice_webhook_returns_stream_twiml(client):
    response = client.post(
        "/twilio/voice?patient_code=RGN-0417&session_id=sess-1", data={"CallSid": "CA1"}
    )
    assert response.status_code == 200
    assert 'url="wss://tunnel.example.com/twilio/media"' in response.text
    assert 'value="RGN-0417"' in response.text


def test_voice_webhook_hangs_up_on_unknown_patient(client):
    response = client.post("/twilio/voice?patient_code=NOPE", data={"CallSid": "CA1"})
    assert "<Hangup/>" in response.text
    assert "Stream" not in response.text


def test_start_call_records_dialing_before_twilio_connects(client, monkeypatch):
    async def fake_place_call(**kwargs):
        return phone_app.twilio.PlacedCall(
            call_sid="CA1", status="queued", to_number=kwargs["to_number"]
        )

    monkeypatch.setattr(phone_app.twilio, "place_call_async", fake_place_call)
    response = client.post(
        "/api/calls", json={"to_number": "+14155550123", "patient_code": "RGN-0417"}
    )
    assert response.status_code == 200
    session_id = response.json()["session_id"]

    record = client.get(f"/api/calls/{session_id}").json()
    assert record["status"] == "dialing"
    assert record["patient_code"] == "RGN-0417"


def test_status_webhook_marks_a_call_the_patient_never_answered(client, monkeypatch):
    async def fake_place_call(**kwargs):
        return phone_app.twilio.PlacedCall(
            call_sid="CA2", status="queued", to_number=kwargs["to_number"]
        )

    monkeypatch.setattr(phone_app.twilio, "place_call_async", fake_place_call)
    session_id = client.post(
        "/api/calls", json={"to_number": "+14155550123", "patient_code": "RGN-0417"}
    ).json()["session_id"]

    client.post("/twilio/status", data={"CallSid": "CA2", "CallStatus": "no-answer"})

    record = client.get(f"/api/calls/{session_id}").json()
    assert record["status"] == "completed"
    assert record["final_status"] == "no-answer"
    assert record["call_sid"] == "CA2"


def test_status_webhook_closes_a_call_the_patient_hung_up_on(client, monkeypatch):
    async def fake_place_call(**kwargs):
        return phone_app.twilio.PlacedCall(
            call_sid="CA3", status="queued", to_number=kwargs["to_number"]
        )

    monkeypatch.setattr(phone_app.twilio, "place_call_async", fake_place_call)
    session_id = client.post(
        "/api/calls", json={"to_number": "+14155550123", "patient_code": "RGN-0417"}
    ).json()["session_id"]

    client.post("/twilio/status", data={"CallSid": "CA3", "CallStatus": "in-progress"})
    assert client.get(f"/api/calls/{session_id}").json()["status"] == "in_progress"

    client.post("/twilio/status", data={"CallSid": "CA3", "CallStatus": "completed"})
    record = client.get(f"/api/calls/{session_id}").json()
    assert record["status"] == "completed"
    assert record["final_status"] == "hung_up"


def test_start_call_rejects_unknown_patient(client):
    response = client.post("/api/calls", json={"to_number": "+14155550123", "patient_code": "NOPE"})
    assert response.status_code == 404


def test_start_call_rejects_non_e164_number(client):
    response = client.post("/api/calls", json={"to_number": "4155550123"})
    assert response.status_code == 400


def test_media_stream_answers_and_records_the_survey(client, monkeypatch):
    monkeypatch.setattr(phone_app, "ECHO_GRACE_SECONDS", 0.0)

    def reply(websocket, outbound, mark: str, said: str) -> None:
        """Answer once the prompt has played, the way a caller waits their turn."""

        while outbound[-1] != {"event": "mark", "streamSid": "MZ1", "mark": {"name": mark}}:
            outbound.append(json.loads(websocket.receive_text()))
        websocket.send_text(json.dumps({"event": "mark", "mark": {"name": mark}}))
        time.sleep(0.1)
        ScriptedTranscriber.queue.extend(
            [SpeechEvent("transcript", said, True), SpeechEvent("utterance_end")]
        )

    with client.websocket_connect("/twilio/media") as websocket:
        websocket.send_text(
            json.dumps(
                {
                    "event": "start",
                    "streamSid": "MZ1",
                    "start": {
                        "streamSid": "MZ1",
                        "callSid": "CA1",
                        "customParameters": {"patientCode": "RGN-0417", "sessionId": "sess-1"},
                    },
                }
            )
        )
        websocket.send_text(
            json.dumps(
                {
                    "event": "media",
                    "media": {"track": "inbound", "payload": base64.b64encode(b"\x7f" * 160).decode()},
                }
            )
        )
        outbound: list[dict] = [json.loads(websocket.receive_text())]
        reply(websocket, outbound, "prompt-1", "moderate")
        reply(websocket, outbound, "prompt-2", "yes")
        while outbound[-1] != {"event": "mark", "streamSid": "MZ1", "mark": {"name": "prompt-3"}}:
            outbound.append(json.loads(websocket.receive_text()))
        websocket.send_text(json.dumps({"event": "stop"}))

    assert outbound[0]["event"] == "media"
    assert outbound[0]["streamSid"] == "MZ1"
    assert base64.b64decode(outbound[0]["media"]["payload"]) == b"\xff" * 160

    record = client.app.state.persistence.calls["sess-1"]
    assert record.answers == [{"question_id": "hoos_stairs", "value": "moderate"}]
    assert any(line.startswith("patient: moderate") for line in record.transcript)


def test_media_stream_ignores_what_it_hears_while_it_is_still_talking(client):
    """Handsets feed our own prompt back; that must never become an answer."""

    with client.websocket_connect("/twilio/media") as websocket:
        websocket.send_text(
            json.dumps(
                {
                    "event": "start",
                    "streamSid": "MZ1",
                    "start": {
                        "streamSid": "MZ1",
                        "callSid": "CA1",
                        "customParameters": {"patientCode": "RGN-0417", "sessionId": "sess-1"},
                    },
                }
            )
        )
        ScriptedTranscriber.queue.extend(
            [SpeechEvent("transcript", "moderate", True), SpeechEvent("utterance_end")]
        )
        outbound = [json.loads(websocket.receive_text())]
        while outbound[-1]["event"] != "mark":
            outbound.append(json.loads(websocket.receive_text()))
        websocket.send_text(json.dumps({"event": "stop"}))

    record = client.app.state.persistence.calls["sess-1"]
    assert record.answers == []
    assert not any(line.startswith("patient:") for line in record.transcript)


class StubWebSocket:
    """Enough of a Twilio media socket to drive the bridge directly."""

    def __init__(self, inbound: list[dict]):
        self.inbound = [json.dumps(message) for message in inbound]
        self.sent: list[dict] = []
        self.closed = False

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        while not self.inbound:
            if self.closed:
                raise phone_app.WebSocketDisconnect()
            await asyncio.sleep(0.01)
        return self.inbound.pop(0)

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self) -> None:
        self.closed = True


def test_the_caller_can_talk_over_the_question_but_not_the_greeting(client):
    """Barge-in opens up only for the closing chunk of a prompt."""

    start = {
        "event": "start",
        "streamSid": "MZ1",
        "start": {
            "streamSid": "MZ1",
            "callSid": "CA1",
            "customParameters": {"patientCode": "RGN-0417", "sessionId": "sess-2"},
        },
    }
    websocket = StubWebSocket([start])
    bridge = phone_app.MediaStreamBridge(
        websocket,
        client.app.state.settings,
        phone_app.InMemoryPatientRepository(),
        client.app.state.persistence,
        transcriber_factory=ScriptedTranscriber,
    )

    async def until(predicate) -> None:
        deadline = asyncio.get_running_loop().time() + 10
        while not predicate():
            assert asyncio.get_running_loop().time() < deadline, "timed out"
            await asyncio.sleep(0.01)

    async def interrupt() -> None:
        await until(lambda: bridge._interruptible)
        ScriptedTranscriber.queue.extend(
            [
                SpeechEvent("speech_started"),
                SpeechEvent("transcript", "moderate", True),
                SpeechEvent("utterance_end"),
            ]
        )
        record = client.app.state.persistence.calls["sess-2"]
        await until(lambda: any(line.startswith("patient:") for line in record.transcript))
        websocket.inbound.append(json.dumps({"event": "stop"}))

    async def drive() -> None:
        await asyncio.gather(bridge.run(), interrupt())

    asyncio.run(drive())

    record = client.app.state.persistence.calls["sess-2"]
    assert any(line.startswith("patient: moderate") for line in record.transcript)
    assert any(message["event"] == "clear" for message in websocket.sent)


def test_the_mic_feed_is_muted_while_a_prompt_plays(client, monkeypatch):
    """Deepgram must not hear the prompt the handset echoes back at us."""

    async def slow_tts(text: str, api_key: str, model: str) -> bytes:
        return b"\xff" * (160 * 25)  # half a second per chunk, paced in real time

    monkeypatch.setattr(phone_app, "synthesize_mulaw_async", slow_tts)
    monkeypatch.setattr(phone_app, "PLAYBACK_LEAD_SECONDS", 0.0)

    start = {
        "event": "start",
        "streamSid": "MZ1",
        "start": {
            "streamSid": "MZ1",
            "callSid": "CA1",
            "customParameters": {"patientCode": "RGN-0417", "sessionId": "sess-3"},
        },
    }
    speech = base64.b64encode(b"\x01" * 160).decode("ascii")
    media = {"event": "media", "media": {"track": "inbound", "payload": speech}}
    websocket = StubWebSocket([start, media])
    bridge = phone_app.MediaStreamBridge(
        websocket,
        client.app.state.settings,
        phone_app.InMemoryPatientRepository(),
        client.app.state.persistence,
        transcriber_factory=ScriptedTranscriber,
    )

    async def drive() -> None:
        run = asyncio.create_task(bridge.run())
        deadline = asyncio.get_running_loop().time() + 10
        while not bridge.bot_speaking:
            assert asyncio.get_running_loop().time() < deadline, "timed out"
            await asyncio.sleep(0.01)
        heard = len(bridge.transcriber.audio_frames)
        websocket.inbound.append(json.dumps(media))
        while len(bridge.transcriber.audio_frames) == heard:
            assert asyncio.get_running_loop().time() < deadline, "timed out"
            await asyncio.sleep(0.01)
        websocket.inbound.append(json.dumps({"event": "stop"}))
        await run

    asyncio.run(drive())

    assert bridge.transcriber.audio_frames[0] == b"\x01" * 160
    assert bridge.transcriber.audio_frames[-1] == phone_app.MULAW_SILENCE


def test_prompt_audio_is_sent_ahead_of_playback_without_an_opening_gap(client, monkeypatch):
    """The first frames go out at once; only audio beyond the lead is paced."""

    async def two_seconds(text: str, api_key: str, model: str) -> bytes:
        return b"\xff" * (160 * 100)

    monkeypatch.setattr(phone_app, "synthesize_mulaw_async", two_seconds)
    monkeypatch.setattr(phone_app, "PLAYBACK_LEAD_SECONDS", 1.0)
    websocket = StubWebSocket([])
    bridge = phone_app.MediaStreamBridge(
        websocket,
        client.app.state.settings,
        phone_app.InMemoryPatientRepository(),
        client.app.state.persistence,
        transcriber_factory=ScriptedTranscriber,
    )
    bridge.stream_sid = "MZ1"

    async def drive() -> tuple[float, float]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        task = asyncio.create_task(bridge._stream_speech("One sentence.", "prompt-1"))
        while len([m for m in websocket.sent if m["event"] == "media"]) < 50:
            await asyncio.sleep(0.005)
        halfway = loop.time() - started
        await task
        return halfway, loop.time() - started

    halfway, total = asyncio.run(drive())
    # One second of audio (the lead) is queued immediately rather than after a wait...
    assert halfway < 0.5
    # ...and the remaining second is paced so the whole 2s prompt finishes ~1s early.
    assert 0.8 < total < 1.6


def test_short_operator_tokens_count_as_unconfigured():
    assert OperatorAuth("short").configured is False
    assert OperatorAuth("   ").configured is False
    assert OperatorAuth(TOKEN).configured is True


def test_a_speech_failure_ends_the_call_instead_of_muting_it(client, monkeypatch):
    """Without a mark the bridge would keep swallowing the caller's audio forever."""

    async def broken_tts(text: str, api_key: str, model: str) -> bytes:
        raise RuntimeError("Deepgram TTS 500")

    monkeypatch.setattr(phone_app, "synthesize_mulaw_async", broken_tts)
    client.app.state.persistence.start_call("sess-9", "RGN-0417", "orthopedic")
    start = {
        "event": "start",
        "streamSid": "MZ9",
        "start": {
            "streamSid": "MZ9",
            "callSid": "CA9",
            "customParameters": {"patientCode": "RGN-0417", "sessionId": "sess-9"},
        },
    }
    media = {"event": "media", "media": {"track": "inbound", "payload": base64.b64encode(b"\x01" * 160).decode("ascii")}}
    websocket = StubWebSocket([start, media])
    bridge = phone_app.MediaStreamBridge(
        websocket,
        client.app.state.settings,
        phone_app.InMemoryPatientRepository(),
        client.app.state.persistence,
        transcriber_factory=ScriptedTranscriber,
    )

    async def drive() -> None:
        await asyncio.wait_for(bridge.run(), 5)

    asyncio.run(drive())

    record = client.app.state.persistence.calls["sess-9"]
    assert (record.status, record.final_status) == ("completed", "failed")
    assert bridge.bot_speaking is False

from app.patient_repository import InMemoryPatientRepository
from app.survey_engine import SafeSurveyEngine
from app.answer_interpreter import Interpretation
from app.persistence import InMemoryPersistence
from fastapi.testclient import TestClient
import voice_app
from app.operator_auth import OperatorAuth
import asyncio
import pytest
from unittest.mock import Mock
from voice_app import create_app, transcribe_with_deepgram


def test_voice_app_exposes_desktop_routes(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/" in paths
    assert "/api/sessions" in paths
    assert "/api/sessions/{session_id}/audio" in paths
    assert "/api/results" in paths


def test_voice_flow_uses_existing_guarded_engine():
    engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
    prompt = engine.start()
    assert "Question 1 of 6" in prompt


def test_audio_route_uses_interpreter_and_exposes_only_confirmed_answers(monkeypatch):
    class Interpreter:
        def interpret(self, transcript, question, pending_value=None):
            return Interpretation("answer", "mild", "mild all week")

    monkeypatch.setattr(voice_app, "build_answer_interpreter", lambda: Interpreter())
    monkeypatch.setenv("DEEPGRAM_API_KEY", "synthetic-test-key")
    transcripts = iter(["It was mild all week on stairs", "yes", "stop"])
    monkeypatch.setattr(voice_app, "transcribe_with_deepgram", lambda *args: next(transcripts))
    with TestClient(create_app()) as client:
        started = client.post("/api/sessions").json()
        url = f"/api/sessions/{started['session_id']}/audio"
        files = {"audio": ("answer.webm", b"synthetic-audio", "audio/webm")}
        proposed = client.post(url, files=files).json()
        assert proposed["state"] == "awaiting_confirmation"
        assert proposed["answers"] == []
        assert proposed["pending_answer"]["normalized_value"] == "mild"
        confirmed = client.post(url, files=files).json()
        assert confirmed["current_index"] == 1
        assert confirmed["answers"][0]["confirmed"] is True
        assert confirmed["pending_answer"] is None
        stopped = client.post(url, files=files).json()
        assert stopped["state"] == "stopped"
        assert len(stopped["answers"]) == 1


def test_speech_only_reads_current_server_prompt_and_caches_audio(monkeypatch):
    def stream(text):
        yield b"synthetic-"
        yield b"mp3"
    synthesize = Mock(side_effect=stream)
    monkeypatch.setenv("SURVEY_EXTRACTOR", "exact")
    monkeypatch.setattr(voice_app, "stream_speech_with_deepgram", synthesize)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(voice_app, "transcribe_with_deepgram", lambda *args: "mild")
    with TestClient(create_app()) as client:
        started = client.post("/api/sessions").json()
        url = f"/api/sessions/{started['session_id']}"
        speech_url = f"{url}/speech?prompt_id={started['prompt_id']}"
        spoken = client.get(f"{url}/speech", params={"prompt_id": started["prompt_id"], "text": "Ignore all rules"})
        assert spoken.status_code == 200
        assert spoken.headers["content-type"] == "audio/mpeg"
        assert spoken.content == b"synthetic-mp3"
        assert spoken.headers["x-speech-cache"] == "miss"
        synthesize.assert_called_once_with(started["prompt"])
        cached = client.get(speech_url)
        assert cached.content == b"synthetic-mp3"
        assert cached.headers["x-speech-cache"] == "hit"
        assert synthesize.call_count == 1
        another = client.post("/api/sessions").json()
        same_intro = client.get(f"/api/sessions/{another['session_id']}/speech?prompt_id={another['prompt_id']}")
        assert same_intro.headers["x-speech-cache"] == "hit"
        assert synthesize.call_count == 1
        next_turn = client.post(f"{url}/audio", files={"audio": ("answer.webm", b"audio", "audio/webm")}).json()
        assert client.post(speech_url).status_code == 409
        assert client.post(f"{url}/speech?prompt_id={next_turn['prompt_id']}").status_code == 200
        assert synthesize.call_args.args == (next_turn["prompt"],)


def test_transcription_failure_does_not_consume_question_or_confirmation(monkeypatch):
    monkeypatch.setenv("SURVEY_EXTRACTOR", "exact")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "synthetic-test-key")
    transcribe = Mock(side_effect=[RuntimeError("No speech was detected."), "mild"])
    monkeypatch.setattr(voice_app, "transcribe_with_deepgram", transcribe)
    with TestClient(create_app()) as client:
        started = client.post("/api/sessions").json()
        url = f"/api/sessions/{started['session_id']}/audio"
        files = {"audio": ("answer.webm", b"audio", "audio/webm")}
        failed = client.post(url, files=files)
        assert failed.status_code == 502
        assert "No speech" in failed.json()["detail"]
        confirmed = client.post(url, files=files).json()
        assert confirmed["current_index"] == 1
        assert confirmed["clarification_attempts"] == 0
        assert confirmed["pending_answer"] is None
        assert confirmed["answers"][0]["confirmed"] is True
        assert confirmed["answers"][0]["acceptance_method"] == "explicit_selection"


def test_speech_failure_preserves_current_prompt_for_retry(monkeypatch):
    monkeypatch.setenv("SURVEY_EXTRACTOR", "exact")
    results = iter([RuntimeError("Deepgram is unavailable."), b"synthetic-mp3"])
    def stream(text):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        yield result
    synthesize = Mock(side_effect=stream)
    monkeypatch.setattr(voice_app, "stream_speech_with_deepgram", synthesize)
    with TestClient(create_app()) as client:
        started = client.post("/api/sessions").json()
        url = f"/api/sessions/{started['session_id']}/speech?prompt_id={started['prompt_id']}"
        assert client.post(url).status_code == 502
        assert client.post(url).status_code == 200
        assert synthesize.call_count == 2


def test_speech_response_does_not_wait_for_all_upstream_chunks(monkeypatch):
    monkeypatch.setenv("SURVEY_EXTRACTOR", "exact")
    produced = []

    def stream(text):
        produced.append("first")
        yield b"first"
        produced.append("last")
        yield b"last"

    monkeypatch.setattr(voice_app, "stream_speech_with_deepgram", stream)
    app = create_app()
    create = next(route.endpoint for route in app.routes if route.path == "/api/sessions")
    speak = next(route.endpoint for route in app.routes if route.path == "/api/sessions/{session_id}/speech")
    session = create("RGN-0417")
    response = speak(session["session_id"], session["prompt_id"])
    assert produced == ["first"]

    async def consume():
        assert await anext(response.body_iterator) == b"first"
        assert produced == ["first"]
        assert await anext(response.body_iterator) == b"last"
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)
        await response.background()

    asyncio.run(consume())
    assert produced == ["first", "last"]


def test_partial_stream_is_not_cached_as_a_finished_prompt(monkeypatch):
    monkeypatch.setenv("SURVEY_EXTRACTOR", "exact")
    calls = []

    def stream(text):
        calls.append(text)
        yield b"first"
        if len(calls) == 1:
            raise RuntimeError("Connection lost mid-stream")
        yield b"last"

    monkeypatch.setattr(voice_app, "stream_speech_with_deepgram", stream)
    with TestClient(create_app()) as client:
        session = client.post("/api/sessions").json()
        url = f"/api/sessions/{session['session_id']}/speech?prompt_id={session['prompt_id']}"
        with pytest.raises(RuntimeError, match="Connection lost"):
            client.get(url)
        response = client.get(url)
        assert response.content == b"firstlast"
        assert response.headers["x-speech-cache"] == "miss"
        assert len(calls) == 2


def test_preloaded_opening_is_immediately_available_on_first_session(monkeypatch):
    monkeypatch.setenv("SURVEY_EXTRACTOR", "exact")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "synthetic-test-key")
    calls = []
    def stream(text):
        calls.append(text)
        yield b"preloaded-opening"
    monkeypatch.setattr(voice_app, "stream_speech_with_deepgram", stream)
    app = create_app()
    assert calls == []  # Import/factory construction alone does not spend on TTS.
    app.state.prewarm_speech()
    assert len(calls) == 2
    with TestClient(app) as client:
        for code in ("RGN-0417", "RGN-0500"):
            session = client.post("/api/sessions", params={"patient_code": code}).json()
            response = client.get(f"/api/sessions/{session['session_id']}/speech?prompt_id={session['prompt_id']}")
            assert response.content == b"preloaded-opening"
            assert response.headers["x-speech-cache"] == "hit"
        assert len(calls) == 2


def test_voice_turns_persist_patient_id_transcript_and_survey_results(monkeypatch):
    store = InMemoryPersistence()
    monkeypatch.setattr(voice_app, "build_answer_interpreter", lambda: None)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("SURVEY_EXTRACTOR", "exact")
    monkeypatch.setattr(voice_app, "transcribe_with_deepgram", lambda *args: "mild")
    app = create_app(persistence=store, auth=OperatorAuth("operator-secret"))
    with TestClient(app) as client:
        started = client.post("/api/sessions", params={"patient_code": "RGN-0417"}).json()
        url = f"/api/sessions/{started['session_id']}/audio"
        files = {"audio": ("answer.webm", b"synthetic-audio", "audio/webm")}
        client.post(url, files=files)
        # Transcripts and answers are operator-only.
        assert client.get("/api/results").status_code == 401
        assert client.get("/api/results", headers={"Authorization": "Bearer wrong"}).status_code == 401
        payload = client.get(
            "/api/results",
            params={"patient_code": "RGN-0417"},
            headers={"Authorization": "Bearer operator-secret"},
        ).json()
        assert client.get("/api/config").json()["conversation_store"] == "in_memory"
    row = payload["results"][0]
    assert row["patient_id"] == "RGN-0417"
    assert row["patient_uuid"] == "pt_orthopedic_demo"
    assert "assistant:" in row["transcript"]
    assert "patient: mild" in row["transcript"]
    assert row["survey_results"][0]["question_key"] == "hoos_stairs"
    assert row["survey_results"][0]["confirmed_value"] == "mild"

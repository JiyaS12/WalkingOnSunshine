"""Local desktop voice survey app.

Run with ``python voice_app.py`` and open http://127.0.0.1:8000.
The browser owns microphone capture and speech playback. Deepgram transcription
is performed by the local server so the API key never reaches the browser.
"""

from __future__ import annotations

import os
import logging
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.operator_auth import OperatorAuth
from app.patient_repository import InMemoryPatientRepository
from app.answer_interpreter import OpenAIAnswerInterpreter, build_answer_interpreter
from app.survey_engine import SafeSurveyEngine
from app.deepgram import DEFAULT_VOICE, stream_speech_with_deepgram, transcribe_with_deepgram
from app import conversation_policy as speech
from app.question_loader import QUESTION_BANKS
from app.persistence import CompositePersistence, build_persistence

ROOT = Path(__file__).resolve().parent
MAX_AUDIO_BYTES = 10 * 1024 * 1024
# Desktop sessions live only as long as a browser is driving them.
MAX_SESSIONS = 64
SESSION_IDLE_SECONDS = 30 * 60
TERMINAL_STATES = {"complete", "escalated", "stopped"}


def create_app(
    persistence=None,
    auth: OperatorAuth | None = None,
    max_sessions: int = MAX_SESSIONS,
    session_idle_seconds: float = SESSION_IDLE_SECONDS,
    clock=time.monotonic,
) -> FastAPI:
    load_dotenv(ROOT / ".env")
    operator = auth or OperatorAuth.from_env()
    interpreter = build_answer_interpreter()
    store = persistence if persistence is not None else build_persistence()
    app = FastAPI(title="VoiceAIThing desktop voice survey")
    app.state.persistence = store
    sessions: dict[str, SafeSurveyEngine] = {}
    session_locks: dict[str, RLock] = {}
    # Only the current application-produced prompt can be synthesized. The
    # browser cannot submit arbitrary text or change the survey's spoken wording.
    # Ordered by last activity so idle and surplus sessions can be dropped;
    # a finished session keeps only this entry so its closing line can replay.
    speech_turns: OrderedDict[str, dict] = OrderedDict()
    last_seen: dict[str, float] = {}
    app.state.session_ids = lambda: list(speech_turns)
    app.state.live_session_ids = lambda: list(sessions)
    # Spoken text is fixed survey content plus validated non-clinical bridges.
    # Reuse completed prompts across
    # sessions; never store a partial/interrupted stream as a complete recording.
    speech_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
    lock = RLock()

    def cache_audio(cache_key: tuple[str, str], audio: bytes) -> None:
        if len(audio) > 2 * 1024 * 1024:
            return
        with lock:
            speech_cache[cache_key] = audio
            speech_cache.move_to_end(cache_key)
            while len(speech_cache) > 64:
                speech_cache.popitem(last=False)

    def prewarm_speech() -> None:
        """Generate the two fixed openings before the desktop server is ready.

        This avoids putting a provider's cold connection/model startup delay on
        the patient's first click. Failures leave the streaming path available.
        """
        if not os.getenv("DEEPGRAM_API_KEY"):
            return
        def warm(questions):
            question = questions[0]
            prompt = speech.opening_text(question, len(questions))
            model = os.getenv("DEEPGRAM_TTS_MODEL", DEFAULT_VOICE)
            try:
                cache_audio((model, prompt), b"".join(stream_speech_with_deepgram(prompt)))
            except (RuntimeError, ValueError):
                logging.getLogger(__name__).warning("Speech opening preload failed; using on-demand streaming.")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(warm, QUESTION_BANKS.values()))

    app.state.prewarm_speech = prewarm_speech

    def forget(session_id: str) -> None:
        sessions.pop(session_id, None)
        session_locks.pop(session_id, None)
        speech_turns.pop(session_id, None)
        last_seen.pop(session_id, None)

    def release_engine(session_id: str) -> None:
        """A terminal survey needs no engine or lock, only its last prompt."""
        sessions.pop(session_id, None)
        session_locks.pop(session_id, None)

    def expire_sessions() -> None:
        now = clock()
        for session_id in list(speech_turns):
            if now - last_seen.get(session_id, now) >= session_idle_seconds:
                forget(session_id)
        # Called before a new session is added, so leave room for it.
        while len(speech_turns) >= max_sessions:
            forget(next(iter(speech_turns)))

    def speech_turn(session_id: str, prompt: str, engine: SafeSurveyEngine) -> dict[str, str]:
        prompt_id = str(uuid4())
        with lock:
            speech_turns[session_id] = {"prompt_id": prompt_id, "text": prompt}
            speech_turns.move_to_end(session_id)
            last_seen[session_id] = clock()
            if engine.session.state in TERMINAL_STATES:
                release_engine(session_id)
        return {"prompt": prompt, "prompt_id": prompt_id}

    def persist(action, *args) -> None:
        try:
            action(*args)
        except Exception:
            logging.getLogger(__name__).exception("Conversation persistence failed")

    @app.get("/api/config")
    def config() -> dict[str, object]:
        database_enabled = isinstance(store, CompositePersistence) or (
            store is not None and store.__class__.__name__ == "DatabaseConversationStore"
        )
        return {
            "deepgram_configured": bool(os.getenv("DEEPGRAM_API_KEY")),
            "llm_configured": isinstance(interpreter, OpenAIAnswerInterpreter),
            "speech_provider": "deepgram",
            "speech_model": os.getenv("DEEPGRAM_TTS_MODEL", DEFAULT_VOICE),
            "conversation_store": "supabase" if database_enabled else "in_memory",
        }

    @app.get("/api/results", dependencies=[Depends(operator)])
    def results(patient_code: str | None = None) -> dict[str, object]:
        return {"results": store.list_results(patient_code)}

    @app.post("/api/sessions", dependencies=[Depends(operator)])
    def create_session(patient_code: str = "RGN-0417") -> dict[str, object]:
        try:
            engine = SafeSurveyEngine(InMemoryPatientRepository(), patient_code, interpreter=interpreter)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        session_id = str(uuid4())
        prompt = engine.start()
        with lock:
            expire_sessions()
            sessions[session_id] = engine
            session_locks[session_id] = RLock()
        persist(store.persist_session_start, session_id, engine.patient, prompt)
        return {
            "session_id": session_id,
            **speech_turn(session_id, prompt, engine),
            **engine.snapshot(),
        }

    @app.post("/api/sessions/{session_id}/audio", dependencies=[Depends(operator)])
    async def handle_audio(session_id: str, audio: UploadFile = File(...)) -> dict[str, object]:
        with lock:
            engine = sessions.get(session_id)
            session_lock = session_locks.get(session_id)
            if engine is not None:
                last_seen[session_id] = clock()
        if engine is None or session_lock is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        if not os.getenv("DEEPGRAM_API_KEY"):
            raise HTTPException(status_code=503, detail="Set DEEPGRAM_API_KEY in .env first.")

        content = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(content) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio recording is too large.")
        def process_recording():
            # Serialize turns within a session while allowing other patients and
            # speech streams to continue during provider calls.
            with session_lock:
                transcript = transcribe_with_deepgram(content, audio.content_type or "audio/webm")
                prompt, answer = engine.handle_response(transcript)
                persist(store.persist_turn, session_id, transcript, prompt, answer, engine.snapshot())
                return {
                    "transcript": transcript,
                    **speech_turn(session_id, prompt, engine),
                    "answer": answer.normalized_value if answer else None,
                    **engine.snapshot(),
                }

        try:
            return await run_in_threadpool(process_recording)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/sessions/{session_id}/speech", dependencies=[Depends(operator)])
    @app.post("/api/sessions/{session_id}/speech", dependencies=[Depends(operator)])
    def speak_prompt(session_id: str, prompt_id: str) -> Response:
        with lock:
            turn = speech_turns.get(session_id)
            if turn is not None:
                last_seen[session_id] = clock()
            if turn is None:
                raise HTTPException(status_code=404, detail="Session not found. Start a new survey.")
            if turn["prompt_id"] != prompt_id:
                raise HTTPException(status_code=409, detail="That prompt is no longer current.")
            cache_key = (os.getenv("DEEPGRAM_TTS_MODEL", DEFAULT_VOICE), turn["text"])
            audio = speech_cache.get(cache_key)
            if audio is not None:
                speech_cache.move_to_end(cache_key)
        headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
        if audio is not None:
            return Response(audio, media_type="audio/mpeg", headers={**headers, "X-Speech-Cache": "hit"})

        stream = stream_speech_with_deepgram(turn["text"])
        try:
            # Check provider errors before HTTP response headers are sent, but
            # wait for only the first chunk, not the entire MP3.
            first = next(stream)
        except (RuntimeError, ValueError, StopIteration) as exc:
            stream.close()
            raise HTTPException(status_code=502, detail=str(exc) or "No speech audio was returned.") from exc

        def audio_chunks():
            complete_audio = bytearray(first)
            try:
                yield first
                for chunk in stream:
                    complete_audio.extend(chunk)
                    yield chunk
                cache_audio(cache_key, bytes(complete_audio))
            finally:
                stream.close()

        return StreamingResponse(
            audio_chunks(), media_type="audio/mpeg",
            headers={**headers, "X-Speech-Cache": "miss"},
            background=BackgroundTask(stream.close),
        )

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(ROOT / "voice_web" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "voice_web"), name="static")
    return app


if __name__ == "__main__":
    import uvicorn

    desktop_app = create_app()
    desktop_app.state.prewarm_speech()
    uvicorn.run(desktop_app, host="127.0.0.1", port=8000)

"""Phone survey server: Twilio carries the call, Deepgram does the speech.

Run with ``python phone_app.py`` behind a public HTTPS tunnel and set
``PUBLIC_BASE_URL`` to that tunnel. ``POST /api/calls`` dials the patient,
Twilio fetches TwiML from ``/twilio/voice`` and opens a bidirectional media
stream to ``/twilio/media``, where the survey runs turn by turn.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from websockets.exceptions import WebSocketException

from app.answer_interpreter import (
    AnswerInterpreter,
    OpenAIAnswerInterpreter,
    build_answer_interpreter,
)
from app.operator_auth import OperatorAuth
from app.patient_repository import InMemoryPatientRepository, PatientNotFoundError
from app.persistence import InMemoryPersistence
from app.survey_engine import SafeSurveyEngine
from app.telephony import twilio
from app.telephony.call_session import PhoneCallSession
from app.telephony.config import TelephonyConfigurationError, TelephonySettings, load_settings
from app.telephony.deepgram_stt import DeepgramTranscriber
from app.telephony.deepgram_tts import frames, synthesize_mulaw_async
from app.telephony.sms import send_sms_async
from app.telephony.stream_tickets import StreamTickets

ROOT = Path(__file__).resolve().parent
VOICE_PATH = "/twilio/voice"
STATUS_PATH = "/twilio/status"
MEDIA_PATH = "/twilio/media"
SILENCE_TIMEOUT_SECONDS = 10.0
FRAME_SECONDS = 0.02
PLAYBACK_LEAD_SECONDS = 2.0
FIRST_CHUNK_CHARS = 90
SPEECH_CHUNK_CHARS = 200
LINE_OPEN_TIMEOUT_SECONDS = 3.0
PARAGRAPH_PAUSE_SECONDS = 0.8
ECHO_GRACE_SECONDS = 0.5
MULAW_SILENCE = b"\xff" * 160
CARRIER_FAILURES = {"busy", "no-answer", "failed", "canceled"}
# Twilio rings for at most 60s by default; a call still "dialing" well after
# that was never answered and no status callback is coming to say so.
DIALING_TIMEOUT_SECONDS = 90.0

logger = logging.getLogger("phone_app")


class CallRequest(BaseModel):
    to_number: str
    patient_code: str = "RGN-0417"


class MediaStreamBridge:
    """Glue between one Twilio media stream and one Deepgram transcription."""

    def __init__(
        self,
        websocket: WebSocket,
        settings: TelephonySettings,
        repository: InMemoryPatientRepository,
        persistence: InMemoryPersistence,
        tickets: StreamTickets,
        transcriber_factory=None,
        interpreter: AnswerInterpreter | None = None,
        sms_sender: Callable[[str, str], Awaitable[None]] | None = None,
    ):
        self.websocket = websocket
        self.settings = settings
        self.repository = repository
        self.persistence = persistence
        self.tickets = tickets
        self.interpreter = interpreter
        self.sms_sender = sms_sender
        self.transcriber_factory = transcriber_factory or DeepgramTranscriber
        self.stream_sid: str | None = None
        self.session: PhoneCallSession | None = None
        self.transcriber: DeepgramTranscriber | None = None
        self.bot_speaking = False
        self.hangup_mark: str | None = None
        self._send_lock = asyncio.Lock()
        self._silence_task: asyncio.Task[None] | None = None
        self._mark_counter = 0
        self._last_mark: str | None = None
        self._active_mark: str | None = None
        self._closed = False
        self._inbound_frames = 0
        self._turn_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []
        self._line_open = asyncio.Event()
        self._greeted = asyncio.Event()
        self._listen_from = 0.0
        self._interruptible = False
        self._playback: asyncio.Task[None] | None = None

    async def run(self) -> None:
        await self.websocket.accept()
        try:
            while not self._closed:
                message = json.loads(await self.websocket.receive_text())
                event = message.get("event")
                if event == "start":
                    await self._on_start(message)
                elif event == "media":
                    await self._on_media(message)
                elif event == "mark":
                    await self._on_mark(message)
                elif event == "stop":
                    logger.info("Stream %s stopped by Twilio", self.stream_sid)
                    break
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Media stream failed")
        finally:
            self._cancel_silence_timer()
            for task in self._tasks:
                task.cancel()
            if self.transcriber is not None:
                await self.transcriber.close()
            await self._close()

    async def _on_start(self, message: dict[str, Any]) -> None:
        start = message.get("start", {})
        self.stream_sid = start.get("streamSid") or message.get("streamSid")
        parameters = start.get("customParameters", {}) or {}
        patient_code = parameters.get("patientCode", "")
        session_id = parameters.get("sessionId") or start.get("callSid") or str(uuid4())
        # Only a stream Twilio opened in answer to our own TwiML carries the
        # ticket the voice webhook issued for this call.
        if not self.tickets.redeem(session_id, parameters.get("streamToken")):
            logger.warning(
                "Stream %s for call %s presented no valid ticket; closing",
                self.stream_sid,
                start.get("callSid"),
            )
            await self._close()
            return
        logger.info(
            "Stream %s started for call %s (patient %s, format %s)",
            self.stream_sid,
            start.get("callSid"),
            patient_code,
            start.get("mediaFormat"),
        )
        try:
            engine = SafeSurveyEngine(self.repository, patient_code, interpreter=self.interpreter)
        except PatientNotFoundError:
            logger.error("Unknown patient code on call %s", session_id)
            await self._close()
            return

        record = self.persistence.calls.get(session_id)
        to_number = record.to_number if record else None
        self.session = PhoneCallSession(
            engine,
            speak=self._speak,
            session_id=session_id,
            persistence=self.persistence,
            to_number=to_number,
            sms_sender=self.sms_sender,
        )
        self.transcriber = self.transcriber_factory(
            api_key=self.settings.deepgram_api_key or "",
            model=self.settings.stt_model,
            utterance_end_ms=self.settings.utterance_end_ms,
        )
        await self.transcriber.__aenter__()
        # Both the survey turns and the speech events run off the receive loop so
        # inbound audio keeps flowing to Deepgram while a prompt is playing.
        self._tasks.append(asyncio.create_task(self._pump_speech_events()))
        self._tasks.append(asyncio.create_task(self._greet()))

    async def _on_media(self, message: dict[str, Any]) -> None:
        if self.transcriber is None:
            return
        media = message.get("media", {})
        if media.get("track") not in (None, "inbound"):
            return
        payload = media.get("payload")
        if payload:
            self._line_open.set()
            self._inbound_frames += 1
            if self._inbound_frames % 250 == 0:
                logger.info(
                    "Stream %s received %d inbound frames", self.stream_sid, self._inbound_frames
                )
            # While we are talking the caller's line carries our own prompt
            # back to us; transcribing it puts the answer a turn behind. Silence
            # keeps Deepgram's socket warm without feeding it our voice.
            audio = base64.b64decode(payload)
            if self.bot_speaking and not self._interruptible:
                audio = MULAW_SILENCE
            try:
                await self.transcriber.send_audio(audio)
            except (OSError, WebSocketException) as error:
                # Losing transcription should not drop the call: the survey can
                # still speak, and the next frame retries the connection.
                logger.warning("Dropping audio frame: %s", error)

    async def _on_mark(self, message: dict[str, Any]) -> None:
        name = message.get("mark", {}).get("name")
        logger.info("Stream %s finished playing %s", self.stream_sid, name)
        if name == self.hangup_mark:
            await self._close()
            return
        if name != self._active_mark:
            # Twilio still reports marks for audio we cleared after a barge-in;
            # by then a newer prompt may be playing and this one says nothing.
            logger.info("Stream %s ignoring stale mark %s", self.stream_sid, name)
            return
        self._active_mark = None
        self.bot_speaking = False
        self._interruptible = False
        if self.session is not None:
            self.session.discard_pending()
        self._listen_from = asyncio.get_running_loop().time() + ECHO_GRACE_SECONDS
        self._start_silence_timer()

    async def _greet(self) -> None:
        """Open the survey once the carrier is actually carrying audio.

        Twilio opens the stream as the call is answered, and anything sent
        before the first inbound frame arrives can be clipped off the front of
        the greeting.
        """

        assert self.session is not None
        try:
            await asyncio.wait_for(self._line_open.wait(), LINE_OPEN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("Stream %s heard no inbound audio; greeting anyway", self.stream_sid)
        try:
            await self._run_turn(self.session.begin())
        finally:
            self._greeted.set()

    async def _run_turn(self, coroutine: Awaitable[bool | None]) -> None:
        """Serialize survey turns; one prompt finishes speaking before the next."""

        try:
            async with self._turn_lock:
                finished = await coroutine
            if finished:
                await self._end_after_playback()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Survey turn failed")

    async def _pump_speech_events(self) -> None:
        assert self.transcriber is not None
        async for event in self.transcriber.events():
            if self.session is None:
                continue
            # Nothing the caller says counts as an answer before we have asked.
            await self._greeted.wait()
            # The handset feeds our own voice back down the inbound track, so
            # anything heard mid-prompt, or in its echo tail, would otherwise be
            # transcribed as the caller's answer. Only the closing chunk of a
            # prompt, the question itself, can be talked over.
            if self.bot_speaking and not (
                self._interruptible and event.kind == "speech_started"
            ):
                continue
            if asyncio.get_running_loop().time() < self._listen_from:
                continue
            if event.kind == "speech_started":
                self._cancel_silence_timer()
                if self.bot_speaking:
                    await self._stop_playback()
            elif event.kind == "transcript" and event.is_final:
                self._cancel_silence_timer()
                self.session.add_transcript(event.text)
            elif event.kind == "utterance_end":
                await self._run_turn(self.session.flush_utterance())

    async def _speak(self, text: str) -> None:
        if not self.settings.deepgram_api_key or self.stream_sid is None:
            logger.warning("Cannot speak: deepgram key or stream missing")
            return
        self._cancel_silence_timer()
        self.bot_speaking = True
        self._interruptible = False
        self._mark_counter += 1
        self._last_mark = f"prompt-{self._mark_counter}"
        self._active_mark = self._last_mark
        playback = asyncio.create_task(self._stream_speech(text, self._last_mark))
        self._playback = playback
        # Waiting this way keeps a barge-in cancellation local to the playback.
        await asyncio.wait({playback})
        if playback.cancelled() or playback.exception() is None:
            return
        # No mark will ever arrive for a prompt that failed to play, so the call
        # would otherwise sit muted forever. Record the outcome and hang up.
        logger.error(
            "Speech failed on stream %s: %r", self.stream_sid, playback.exception()
        )
        self.bot_speaking = False
        self._interruptible = False
        if self.session is not None and self.session.session_id in self.persistence.calls:
            self.persistence.complete_call(self.session.session_id, "failed")
        await self._close()

    async def _stream_speech(self, text: str, mark: str) -> None:
        """Synthesize the prompt piece by piece and play it at speaking speed.

        A whole prompt takes seconds to synthesize, which the caller would hear
        as dead air, so each sentence group is rendered while the previous one
        plays. Frames go out just ahead of playback: Twilio buffers everything
        it receives, so a burst would starve the inbound audio Deepgram expects.
        """

        loop = asyncio.get_running_loop()
        started = loop.time()
        sent_frames = 0
        plan = speech_plan(text)
        pending = asyncio.create_task(self._synthesize(plan[0][0]))
        for index, (chunk, pause) in enumerate(plan):
            audio = await pending
            if index + 1 < len(plan):
                pending = asyncio.create_task(self._synthesize(plan[index + 1][0]))
            logger.info(
                "Speaking %d chars as %.1fs of audio on stream %s",
                len(chunk),
                len(audio) / 8000,
                self.stream_sid,
            )
            padding = MULAW_SILENCE * round(pause / FRAME_SECONDS)
            for frame in frames(audio + padding):
                await self._send(
                    {
                        "event": "media",
                        "streamSid": self.stream_sid,
                        "media": {"payload": base64.b64encode(frame).decode("ascii")},
                    }
                )
                sent_frames += 1
                # Stay at most PLAYBACK_LEAD_SECONDS of audio ahead of the caller.
                ahead = started + sent_frames * FRAME_SECONDS - PLAYBACK_LEAD_SECONDS - loop.time()
                if ahead > 0:
                    await asyncio.sleep(ahead)
        # Everything is queued now and Twilio is a couple of seconds behind, so
        # the caller is hearing the end of the question: they may talk over it.
        self._interruptible = True
        await self._send({"event": "mark", "streamSid": self.stream_sid, "mark": {"name": mark}})

    async def _stop_playback(self) -> None:
        """Drop the rest of the prompt and Twilio's buffer of it, and listen."""

        self.bot_speaking = False
        self._interruptible = False
        self._active_mark = None
        playback, self._playback = self._playback, None
        if playback is not None:
            playback.cancel()
        await self._send({"event": "clear", "streamSid": self.stream_sid})
        # No mark will come back for a prompt we abandoned, so arm the timer
        # that reprompts a caller who goes quiet again.
        self._start_silence_timer()

    async def _synthesize(self, text: str) -> bytes:
        return await synthesize_mulaw_async(
            text, self.settings.deepgram_api_key, self.settings.tts_model
        )

    async def _end_after_playback(self) -> None:
        self.hangup_mark = self._last_mark
        if not self.bot_speaking:
            await self._close()

    def _start_silence_timer(self) -> None:
        self._cancel_silence_timer()
        if self.session is None or self.session.finished:
            return
        self._silence_task = asyncio.create_task(self._silence_watchdog())

    def _cancel_silence_timer(self) -> None:
        task, self._silence_task = self._silence_task, None
        if task is not None:
            task.cancel()

    async def _silence_watchdog(self) -> None:
        try:
            await asyncio.sleep(SILENCE_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        if self.session is None:
            return
        await self._run_turn(self.session.handle_silence())
        # A paused caller is left in peace rather than reprompted, so no mark
        # will come back to re-arm the timer; keep counting the quiet ourselves.
        if not self.bot_speaking and not self._closed:
            self._start_silence_timer()

    async def _send(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.websocket.send_text(json.dumps(message))

    async def _close(self) -> None:
        self._closed = True
        try:
            await self.websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass


def speech_plan(text: str, pause: float = PARAGRAPH_PAUSE_SECONDS) -> list[tuple[str, float]]:
    """Chunks to speak, each with the silence that follows it.

    A blank line in a prompt is a beat, such as the one between the greeting
    and the first question, and is played as silence rather than spoken.
    """

    paragraphs = [part for part in text.split("\n\n") if part.strip()] or [text]
    plan: list[tuple[str, float]] = []
    for index, paragraph in enumerate(paragraphs):
        chunks = speech_chunks(paragraph)
        trailing = pause if index + 1 < len(paragraphs) else 0.0
        plan.extend((chunk, 0.0) for chunk in chunks[:-1])
        plan.append((chunks[-1], trailing))
    return plan


def speech_chunks(text: str, limit: int = SPEECH_CHUNK_CHARS) -> list[str]:
    """Group a prompt into sentence-sized pieces so speech can start quickly.

    Only the opening piece is kept short: it decides how long the caller waits
    for the first word, while longer pieces afterwards keep the delivery even.
    """

    chunks: list[str] = []
    current = ""
    for sentence in re.findall(r"[^.!?]+[.!?]*\s*", text.strip()) or [text.strip()]:
        budget = FIRST_CHUNK_CHARS if not chunks else limit
        if current and len(current) + len(sentence) > budget:
            chunks.append(current.strip())
            current = sentence
        else:
            current += sentence
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text]


def create_app(
    settings: TelephonySettings | None = None,
    auth: OperatorAuth | None = None,
    dialing_timeout: float = DIALING_TIMEOUT_SECONDS,
) -> FastAPI:
    load_dotenv(ROOT / ".env")
    resolved = settings or load_settings()
    operator = auth or OperatorAuth.from_env()
    app = FastAPI(title="VoiceAIThing phone survey")
    repository = InMemoryPatientRepository()
    persistence = InMemoryPersistence()
    interpreter = build_answer_interpreter()
    sessions_by_call_sid: dict[str, str] = {}
    tickets = StreamTickets()
    watchdogs: set[asyncio.Task[None]] = set()

    async def sms_sender(to_number: str, body: str) -> None:
        await send_sms_async(
            account_sid=resolved.twilio_account_sid,
            auth_token=resolved.twilio_auth_token,
            to_number=to_number,
            from_number=resolved.twilio_from_number,
            body=body,
        )
    app.state.settings = resolved
    app.state.persistence = persistence
    app.state.stream_tickets = tickets

    @app.get("/api/config")
    def config() -> dict[str, object]:
        return {
            "deepgram_configured": resolved.deepgram_ready,
            "twilio_configured": resolved.twilio_ready,
            "public_base_url": resolved.public_base_url,
            "llm_configured": isinstance(interpreter, OpenAIAnswerInterpreter),
            "operator_token_configured": operator.configured,
            "ready": resolved.ready and operator.configured,
        }

    @app.post("/api/calls", dependencies=[Depends(operator)])
    async def start_call(payload: CallRequest) -> dict[str, object]:
        try:
            resolved.require_outbound()
        except TelephonyConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        try:
            patient = repository.lookup_patient(payload.patient_code)
        except PatientNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        session_id = str(uuid4())
        answer_url = (
            f"{resolved.webhook_url(VOICE_PATH)}"
            f"?patient_code={payload.patient_code}&session_id={session_id}"
        )
        try:
            call = await twilio.place_call_async(
                account_sid=resolved.twilio_account_sid,
                auth_token=resolved.twilio_auth_token,
                to_number=payload.to_number,
                from_number=resolved.twilio_from_number,
                answer_url=answer_url,
                status_callback_url=resolved.webhook_url(STATUS_PATH),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except twilio.TwilioError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        record = persistence.start_call(
            session_id, payload.patient_code, patient.condition_category.value
        )
        record.status = "dialing"
        record.call_sid = call.call_sid
        record.to_number = call.to_number
        record.carrier_status = call.status
        sessions_by_call_sid[call.call_sid] = session_id
        if not call.status_callback:
            logger.warning(
                "Call %s placed without a status callback; timing it out locally", call.call_sid
            )
        watchdogs.add(asyncio.create_task(_time_out_dialing(session_id)))
        return {
            "call_sid": call.call_sid,
            "status": call.status,
            "to_number": call.to_number,
            "session_id": session_id,
        }

    async def _time_out_dialing(session_id: str) -> None:
        try:
            await asyncio.sleep(dialing_timeout)
        finally:
            current = asyncio.current_task()
            if current is not None:
                watchdogs.discard(current)
        record = persistence.calls.get(session_id)
        if record is not None and record.status == "dialing" and record.final_status is None:
            logger.info("Call %s never connected; marking it unanswered", record.call_sid)
            record.status = "completed"
            record.final_status = "no-answer"

    @app.get("/api/calls/{session_id}", dependencies=[Depends(operator)])
    def call_record(session_id: str) -> dict[str, object]:
        record = persistence.calls.get(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No call record for that session.")
        return {
            "session_id": record.session_id,
            "patient_code": record.patient_code,
            "condition_category": record.condition_category,
            "status": record.status,
            "final_status": record.final_status,
            "transcript": record.transcript,
            "answers": record.answers,
            "call_sid": record.call_sid,
            "to_number": record.to_number,
            "carrier_status": record.carrier_status,
        }

    @app.post(VOICE_PATH)
    async def voice(request: Request) -> Response:
        form = dict(await request.form())
        if not _signature_ok(resolved, request, form):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature.")
        patient_code = request.query_params.get("patient_code", "")
        session_id = request.query_params.get("session_id") or form.get("CallSid", str(uuid4()))
        try:
            repository.lookup_patient(patient_code)
        except PatientNotFoundError:
            return Response(
                content=twilio.hangup_twiml(
                    "We could not find your record, so this call will end now."
                ),
                media_type="application/xml",
            )
        return Response(
            content=twilio.media_stream_twiml(
                resolved.websocket_url(MEDIA_PATH),
                {
                    "patientCode": patient_code,
                    "sessionId": str(session_id),
                    "streamToken": tickets.issue(str(session_id)),
                },
            ),
            media_type="application/xml",
        )

    @app.post(STATUS_PATH)
    async def status(request: Request) -> Response:
        form = dict(await request.form())
        if not _signature_ok(resolved, request, form):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature.")
        CallSid = str(form.get("CallSid", ""))
        CallStatus = str(form.get("CallStatus", ""))
        logger.info("Call %s status %s", CallSid, CallStatus)
        record = persistence.calls.get(sessions_by_call_sid.get(CallSid, ""))
        if record is not None and CallStatus:
            record.carrier_status = CallStatus
            if CallStatus == "in-progress":
                record.status = "in_progress"
            elif CallStatus in CARRIER_FAILURES and record.final_status is None:
                record.status = "completed"
                record.final_status = CallStatus
            elif CallStatus == "completed" and record.final_status is None:
                # The patient hung up before the survey reached a terminal state.
                record.status = "completed"
                record.final_status = "hung_up"
        return Response(status_code=204)

    @app.websocket(MEDIA_PATH)
    async def media(websocket: WebSocket) -> None:
        await MediaStreamBridge(
            websocket,
            resolved,
            repository,
            persistence,
            tickets,
            interpreter=interpreter,
            sms_sender=sms_sender,
        ).run()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(ROOT / "phone_web" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "phone_web"), name="static")
    return app


def _signature_ok(settings: TelephonySettings, request: Request, form: dict[str, Any]) -> bool:
    # Without the auth token there is nothing to verify against, so nothing
    # reaching these public routes can be trusted; fail closed.
    if not settings.twilio_auth_token or not settings.public_base_url:
        logger.warning("Rejecting %s: Twilio signature checking is not configured", request.url.path)
        return False
    signature = request.headers.get("X-Twilio-Signature", "")
    url = f"{settings.public_base_url.rstrip('/')}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    return twilio.validate_signature(
        settings.twilio_auth_token, url, {k: str(v) for k, v in form.items()}, signature
    )


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s %(message)s")

    uvicorn.run(create_app(), host="0.0.0.0", port=8000)

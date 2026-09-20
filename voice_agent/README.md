# VoiceAIThing

This repository now contains a safer, staged survey runtime instead of the earlier ad hoc spoken-demo prototype.

## What is in this repo now

The active runtime is the `app/` package. It enforces:

- patient lookup by code before starting the survey
- strict condition-based question selection
- direct option selection and confirmation of inferred answers, with bounded retry limits
- persisted call metadata in the in-memory fallback layer
- a voice/handoff boundary that prepares a gait-checker payload without sending real external links

The first-release scope is intentionally narrow:

- orthopedic and stroke branches are supported
- HOOS JR is the default orthopedic instrument
- patient records are not guessed; missing codes fail loudly
- real telephony, real external delivery, and production database wiring remain out of scope

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
```

The safe core can be exercised directly from Python:

```python
from app.patient_repository import InMemoryPatientRepository
from app.survey_engine import SafeSurveyEngine

engine = SafeSurveyEngine(InMemoryPatientRepository(), "RGN-0417")
print(engine.start())
print(engine.handle_response("mild"))
# A named option is saved immediately; the response contains question 2.
```

## Configuration

Copy `.env.example` to `.env` and set any local values you need. The safe runtime includes:

```env
PATIENT_CODE=RGN-0417
PATIENT_REPOSITORY=in_memory
```

To interpret conversational answers in the desktop voice app and the phone
survey, configure:

```env
SURVEY_EXTRACTOR=openai
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4.1-mini
```

Without an OpenAI key, or with `SURVEY_EXTRACTOR=exact`, the app accepts exact
answer labels and basic repeat/pause/resume/stop commands offline. It asks for a
choice when free-form language cannot be interpreted. `/api/config` reports
`llm_configured` so callers can distinguish these modes. Direct Python users can
pass `interpreter=build_answer_interpreter()` (from `app.answer_interpreter`) to
`SafeSurveyEngine`; its default remains offline.

## Survey companion guardrails

The model instructions and fixed survey/confirmation wording are in
[`app/conversation_policy.py`](app/conversation_policy.py). The helper identifies
itself as automated and uses patient, respectful language without baby talk,
pressure, or praise for a particular answer. Deepgram generates the spoken audio
from the server-composed response.

- Questions and answer options come directly from the existing question bank.
  The helper repeats questions verbatim; it never rewrites them or invents
  examples, definitions, or a numeric severity scale.
- The LLM interprets input into an allowed option or a limited intent and may
  compose a short natural acknowledgment in the same call. These bridges are
  not selected from a fixed sentence list. They are bounded to one sentence,
  18 words, and a non-clinical vocabulary; questions, labels, medical advice,
  promises, identifiers and unrestricted patient quotations are not allowed.
  Invalid bridges fall back to reviewed wording without discarding valid answers.
  [`app/conversation_bridge.py`](app/conversation_bridge.py) checks this boundary.
  Survey questions, option-bearing confirmations, medical boundaries and control
  responses remain application-owned. The LLM never directly advances the survey.
- Patients can describe their experience without saying an option label. The
  interpreter proposes the best-fitting choice from symptom intensity and impact,
  using the question as context. For example, “my hip really really hurts i dont
  know what to do” can produce a tentative `extreme` proposal, followed by an
  empathetic “It sounds like your hip pain going up or down stairs may be extreme.
  Would you agree, or would you describe it differently?” The patient can reject
  or correct this interpretation.
- Interpretations must include a supporting quote from the current transcript.
  Statements with no indication of degree, explicitly conflicting options,
  irrelevant symptoms, model refusals, invalid output, and provider errors lead
  to clarification. Evidence quotes are never spoken. Intensity descriptions
  can support a best-fit proposal even when adjacent choices could also fit.
- Clear option selections such as “Severe” are accepted immediately, including
  after a rejected or adjusted proposal. They do not trigger a redundant read-back.
  With the LLM enabled, explicit selections in full sentences (“I would say
  moderate”, “No, I meant mild”) are also accepted. A mere label mention,
  negation, someone else's answer, or uncertainty is not a clear selection.
- AI-inferred choices still require a separate confirmation. Short replies
  like `yes` work locally; with the LLM enabled, conversational agreement such as
  “Yeah I would agree that it's pretty extreme it hurts so much” also commits it.
  The model must return the existing pending value and the entire confirmation
  transcript as evidence. Uncertainty is not agreement.
- Small relative corrections use the existing proposal as context: “I wouldn't
  say extreme, maybe a little less than that” proposes **severe**, rather than
  restarting the question. The model classifies the direction; the engine moves
  exactly one step within the known ordered options and asks the patient to agree.
  Adjustments never wrap past either endpoint or save without patient agreement.
  An unspecified large reduction or a comparison with yesterday is not treated
  as a one-step change to the current proposal.
- `session.answers` and snapshot `answers` contain only confirmed responses;
  `pending_answer` holds the current unconfirmed proposal. Rejected proposals
  are discarded. The answer returned by `handle_response` may still be a proposal;
  consumers must check its `confirmed` flag before persisting it.
  For compatibility, `confirmed=True` means patient-authorized, either by direct
  selection or by separate agreement. `acceptance_method` on accepted answers and
  snapshots distinguishes `explicit_selection` from `confirmation`. Inferred
  proposals keep it null. No remote database integration/schema change is made.
- Three failed clarifications/rejections for one question stop the session and
  set `needs_human_review`. The count resets only when an answer is confirmed.
  Repeat and pause requests do not use up attempts. Completed, stopped, and
  escalated sessions cannot resume accepting answers.
- Medical questions receive a fixed scope boundary. The helper does not diagnose,
  prescribe, promise recovery, impersonate a doctor, or claim that a clinician
  has been contacted. A real doctor's recording is a separate future integration.

The OpenAI adapter uses [strict structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
then validates output again locally. Only the current question, options, pending
proposal, and current transcript are sent; patient record identifiers and prior
answers are not included. `store=False` is set on the request. This does not
establish HIPAA compliance, and transcripts can themselves contain personal data.

The code enforces what can be spoken and when an answer can be accepted. Semantic
interpretation still depends on the model, so schema validation and a matching
quote alone do not prove an interpretation or inferred agreement is correct.
AI-proposed categories require a separate patient decision; clearly selected
categories do not. Classification of full sentences and bridge tone remain
model-dependent; lexical/schema checks are not a semantic correctness guarantee.
The existing question bank was preserved, not independently validated as a
clinical instrument. Human review is a local flag; no notification or clinic
delivery is implemented here.

Offline guardrail and mocked-provider tests run with the normal suite. To evaluate
the configured model on synthetic examples (paid API calls), explicitly opt in:

```bash
RUN_LIVE_SURVEY_TESTS=1 python -m pytest -m live -q
```

## Validation

```bash
. .venv/bin/activate
python -m pytest -q
```

The repo is intentionally designed as a staged, reviewable stack rather than a giant one-shot rewrite.

## Phone call survey (Twilio + Deepgram)

`phone_app.py` runs the survey over a real phone call. Twilio dials the patient
and streams the call audio to the server; Deepgram transcribes it live and
speaks each prompt back into the call. Both API keys stay on the server.

```bash
cp .env.example .env
# Set DEEPGRAM_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
# Set OPERATOR_TOKEN to a long random value (openssl rand -hex 32)
ngrok http 8000                 # in a second terminal
# Put the https tunnel URL in PUBLIC_BASE_URL, then:
python phone_app.py
```

Open <http://127.0.0.1:8000>, enter the patient's number in E.164 format
(`+14155550123`), the `OPERATOR_TOKEN`, and a patient code (`RGN-0417`
orthopedic, `RGN-0500` stroke), then click **Call patient**. Every `/api/*`
route requires `Authorization: Bearer <OPERATOR_TOKEN>` because the tunnel
exposes them publicly; the Twilio webhooks are verified by request signature
instead. The page polls the live transcript while the call
runs. Twilio must reach `PUBLIC_BASE_URL` over HTTPS, so keep the tunnel up for
the whole call.

How a call flows:

| Step | Endpoint |
| --- | --- |
| Operator starts the call | `POST /api/calls` places the Twilio call |
| Twilio asks what to do | `POST /twilio/voice` returns `<Connect><Stream>` TwiML |
| Call audio both ways | `WS /twilio/media` mu-law 8 kHz frames |
| Call lifecycle | `POST /twilio/status` |

The websocket bridge feeds inbound audio to Deepgram, waits for `UtteranceEnd`
before answering, interrupts its own playback when the patient starts talking,
re-prompts on silence, and escalates for human review after repeated confusion
or silence. Webhook requests are rejected unless the `X-Twilio-Signature`
header validates against `TWILIO_AUTH_TOKEN`.

`python scripts/check_deepgram.py` verifies the Deepgram speech round trip in
the telephony audio format without placing a call.

Tuning what the patient hears and how well they are understood:

- `DEEPGRAM_STT_MODEL`: `nova-3` (default) or `nova-2-phonecall`, which is
  trained on 8 kHz call audio. Either way the survey answer words (none, mild,
  moderate, …) are boosted, via `keyterm` on Nova-3 and `keywords` elsewhere.
- `DEEPGRAM_TTS_MODEL`: any Aura-2 voice. `python scripts/audition_voices.py`
  renders the call opening in a handful of warm voices to `voice_samples/` so you
  can pick one by ear before changing it.
- `UTTERANCE_END_MS`: how long a silence ends the patient's turn (default 1200).

## Desktop voice demo with Deepgram

The local voice app uses the browser microphone, sends each recording to the
local Python server, transcribes it with Deepgram Nova-3, and speaks the survey
reply using Deepgram Aura-2 (`aura-2-thalia-en` by default). Set
`DEEPGRAM_TTS_MODEL` to select another Deepgram voice. The Deepgram key stays on
the server. Browser speech synthesis is not used as a fallback.

The speech endpoint accepts a session and current prompt ID, not arbitrary text;
it can only speak the application-produced survey reply. Deepgram MP3 chunks are
forwarded as they arrive, and the browser plays the endpoint directly instead of
waiting for a complete downloaded Blob. The server reuses provider connections,
and caches up to 64 completed prompt recordings (at most 2 MiB each) across sessions
for immediate replay. Partial or failed streams are never cached. Recognition and
interpretation run in a worker thread with turns serialized per session, so they
do not block speech streaming for other sessions. Actual startup time still
depends on provider latency, the connection, and browser audio buffering.
The desktop launcher preloads the two fixed survey openings before it starts
serving requests, moving provider cold-start work out of the patient's first
turn. If that preload fails, the app still starts and streams on demand. Factory
users can call `app.state.prewarm_speech()` before serving the app if desired.
Recording is disabled during speech playback to avoid transcribing the helper.
After playback ends, listening starts automatically. A local Web Audio amplitude
detector waits for speech, then about 1.5 seconds of quiet before sending the turn.
Short pauses are retained, silence alone is never automatically uploaded, quiet
waiting buffers rotate every 30 seconds, and spoken turns are capped at two minutes.
This is pause detection, not semantic end-of-sentence detection: background speech
or long thinking pauses can still affect it. Use a quiet room or headphones.
**Pause microphone** releases the mic and discards unsent audio; **Resume listening**
opens it again. **Send now** is an optional manual override. Microphone access is
released on completion, page exit, or errors. Spoken “pause” pauses the survey but
keeps listening for “resume”; use the button to switch the microphone off entirely.

```bash
cp .env.example .env
# Edit .env and set DEEPGRAM_API_KEY
python voice_app.py
```

Open <http://127.0.0.1:8000>, choose a patient code, click **Start survey**, and
allow microphone access once. After each prompt, just speak and briefly pause;
no per-answer button press is needed.
Your transcript appears as **You:** in the conversation. Use `RGN-0417` for the
orthopedic/HOOS JR branch or `RGN-0500` for the stroke branch.

This is a local turn-based desktop demo, not a phone system. It does not yet
stream microphone audio continuously to Deepgram, support interrupting the helper
by speaking, or send a real gait link.

When `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are set on the server, each
voice session stores the synthetic patient id, the conversation transcript
text, and confirmed survey results. Raw audio is not stored. Inspect saved
rows at `GET /api/results` (requires the `OPERATOR_TOKEN` bearer header) or the `patient_conversation_results` view. See
[DATABASE.md](DATABASE.md).

The active survey implementation is in `app/`; this checkout does not include
the earlier `survey_intelligence/` prototype.

## Database / Supabase (additive, optional)

The voice app looks up patients in memory. When Supabase server credentials are
set, it also stores each conversation's synthetic patient id, transcript text,
and confirmed survey results. An optional Postgres/Supabase schema supports
longitudinal storage, confirmed-value scoring, a clinician dashboard view, and
an answer-audit view.

This layer is **synthetic/demo data only** and is **not HIPAA compliant**.
It does not store raw audio. Scoring uses confirmed answers only, never AI
proposals.

See [DATABASE.md](DATABASE.md) for schema, seed data, migrations, and
backend connection notes. See [SECURITY.md](SECURITY.md) for RLS,
service-role rules, and production gaps.

Do not put `SUPABASE_SERVICE_ROLE_KEY` or `DATABASE_URL` in frontend code.

# Sana voice service

The phone service integrates the confirmed survey runtime with the main
Sana backend. Main owns patient identity, clinician-selected
conditions, clinical storage, signed links, walking events and saved sessions.

## What is in this repo now

The active runtime is the `app/` package. It enforces:

- authenticated main-backend lookup before dialing a registered call
- unknown/null generic intake fields, never inferred from condition answers
- strict condition-based question selection
- direct option selection and confirmation of inferred answers, with bounded retry limits
- durable provider dispatch receipts and versioned status publication to main
- SMS of the exact backend-issued patient URL after consent and successful survey ingestion

The first-release scope is intentionally narrow:

- orthopedic and stroke branches are supported
- HOOS JR is the default orthopedic instrument
- patient records and clinical facts are never guessed
- offline tests use injected providers; live telephony requires separate credentials

## Quickstart

```bash
# Run inside voice_agent; do not activate the main backend's virtualenv.
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
node --test tests/test_voice_ui.cjs
```

Keep this virtualenv separate: the main backend pins `openai==3.16.1`;
this service requires `openai<3`. Main startup and tests do not require
Twilio, Deepgram or voice-side OpenAI credentials.

The standalone demo core can still be exercised directly from Python:

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
choice when free-form language cannot be interpreted. The current integrated
call asks only the six condition questions; generic intake fields remain null.
Direct Python users can
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
  proposals keep it null. The integrated store preserves this distinction.
- Three failed clarifications/rejections for one question leave it unanswered,
  set `needs_human_review`, and advance to the next question. Repeated silence
  on an active phone survey question also skips it. The retry count resets for
  each new question; repeat and pause requests do not use up attempts. Explicit
  stop requests and overall call limits still end the call. Completed, stopped,
  and escalated sessions cannot resume accepting answers.
- Medical questions receive a fixed scope boundary. The helper does not diagnose,
  prescribe, promise recovery, impersonate a doctor, or claim that a clinician
  has been contacted. The phone runtime can play a separate recorded doctor
  greeting before the automated helper; the bundled clip defaults to orthopedic
  calls. `DOCTOR_GREETING_AUDIO` selects the clip (empty disables it), and
  `DOCTOR_GREETING_CONDITIONS` selects eligible conditions.

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
clinical instrument. In the integrated flow, human-review metadata and the
recognized survey transcript are submitted to the main patient store and shown
in the clinician portal. There is no automatic clinician notification. Missing
answers are not assigned values, and incomplete surveys receive no total score.
The transcript is saved with survey submission, not continuously during a call;
an interrupted call may have no persisted transcript.

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

## Integrated phone runtime and downstream contract

The root [operator runbook](../docs/integration-runbook.md) covers the combined
runtime and real-process offline harness. The canonical API/data contract is
in [patient access](../docs/patient-access-contract.md). Keep `voice_agent/.venv`
separate from `backend/.venv` because their OpenAI requirements conflict.

Run main on **8000**, phone on **8001**, frontend on **3000**. Use one phone
process/worker with a persistent receipt volume. Run `python phone_app.py`, or
`uvicorn phone_app:create_app --factory --port 8001 --no-access-log`.
The default factory is integrated; it does not expose the old demo dialer.
The clinician frontend calls main, which reserves the call before contacting phone.

Configure `MAIN_BACKEND_URL` (default `http://127.0.0.1:8000`),
`SURVEY_INGEST_TOKEN` (same as main), and `OPERATOR_TOKEN` (same as main's
phone-client token, at least 16 characters). Real dialing additionally requires
`DEEPGRAM_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_FROM_NUMBER`, and `PUBLIC_BASE_URL`. Point the HTTPS tunnel to 8001.
All provider credentials remain server-side. Origins must be HTTPS, except
loopback HTTP for local use. Keep request-body tracing disabled at proxies.

With no credentials the app starts; `GET /api/config` returns
`{"mode":"integrated","ready":false}` and dialing fails closed with 503.
There is no environment switch that fakes successful clinical storage or sends.
For local fake mode, inject `MainBackend(..., transport=httpx.MockTransport(...))`
and `FakePhoneProvider` into `IntegratedService`, then pass it as
`create_app(settings, auth, integrated_service=service)`.
`tests/test_main_integration.py` supplies a complete runnable example using the
actual backend JSON-store implementation, a temporary store and synthetic data:

```bash
SURVEY_EXTRACTOR=exact .venv/bin/python -m pytest -q tests/test_main_integration.py
```

Explicit `create_app(settings, auth)` injection without an integration service
retains the old standalone test/demo routes. It is not the CLI's runtime.
Desktop `voice_app.py` remains a standalone condition-survey demo.

### Main → phone (operator bearer authentication)

All routes below require `Authorization: Bearer <OPERATOR_TOKEN>` and return a
**bare `PhoneSnapshot`**, without a `call` wrapper.

| Endpoint | Body / purpose |
| --- | --- |
| `POST /api/calls` | `patient_id`, `call_id`, `attempt_id`, `request_id`, `to_number` (E.164), `condition_category` (`orthopedic` or `stroke`) |
| `GET /api/calls/{call_id}` | Latest durable receipt; also retries publication of an unacknowledged snapshot |
| `POST /api/calls/{call_id}/sms-retries` | `patient_id`, `call_id`, `attempt_id`, `request_id`, `sms_attempt` (1–5), `patient_url`, `patient_access_expires_at`; main must first reserve this request/attempt |
| `POST /api/calls/{call_id}/survey-retries` | No body; operator explicitly replays the identical confirmed submission retained in this process |

Snapshot fields: `patient_id`, `call_id`, monotonic integer `version`,
`call_status`, `survey_status`, `sms_status`, `sms_attempt`,
nullable `provider_call_id`, `phone_session_id`, `message_id`, `error_code`.

- Call: `dispatching`, `unknown`, `dialing`, `in_progress`, `completed`, `failed`, `stopped`.
- Survey: `pending`, `in_progress`, `stored`, `stopped`, `needs_review`.
- SMS: `not_requested`, `sending`, `unknown`, `sent`, `delivered`, `failed`.
- Errors: `provider_rejected`, `provider_unavailable`, `timeout`, `busy`,
  `no_answer`, `disconnected`, `stopped`, `needs_review`, or null.
- Identifiers use letters/digits/underscore/hyphen; patient IDs 1–32 characters,
  call/attempt/provider IDs 1–64, request IDs 16–128.
- Unknown receipt: 404; invalid input: 422; authentication: 401/503;
  changed request/scope or reconciliation conflict: 409; unavailable main: 503.

Call IDs come from main. The phone reserves durably before dispatch, fingerprints
the authenticated payload, and never redials an existing receipt. Main patient
and registered-call lookup must agree with the captured patient/attempt/condition
before dispatch and stream entry. Missing clinical context never selects a demo.

### Phone → main (service-token authentication)

Each request carries `X-Survey-Token: <SURVEY_INGEST_TOKEN>`, with a five-second
timeout, redirects disabled and no automatic transport retries:

| Endpoint | Use |
| --- | --- |
| `GET /api/integration/patients/{patient_id}` | Authoritative metadata and active call |
| `GET /api/integration/patients/{patient_id}/calls/{call_id}` | Captured call context and reserved SMS retry |
| `POST /api/integration/patients/{patient_id}/calls/{call_id}/status` | Versioned bare snapshot |
| `POST /api/submit-survey` | Condition survey with confirmed answers, unanswered items, review metadata, and transcript |
| `GET /api/integration/patients/{patient_id}/calls/{call_id}/walking` | Patient/attempt-scoped walking state |

Survey submission is frozen after all six condition questions have been
answered or marked unanswered. Clear selections are accepted directly; inferred
readings require patient agreement. The helper then asks for SMS consent. Both
consent and decline save the survey, but only consent sends the walking link.
Generic fields are sent as unknown/null by the current phone flow. No Likert
value supplies a numeric pain score, fall count or dizziness value.

```json
{
  "patient_id": "patient1",
  "call_id": "main-issued-call-id",
  "submission_kind": "integrated",
  "pain_scale": null,
  "fall_history": {
    "falls_last_6_months": null,
    "injured": null,
    "last_fall_description": null
  },
  "dizziness": null,
  "dizziness_notes": null,
  "primary_complaints": null,
  "condition_survey": {
    "instrument": "hoos_jr",
    "version": "1",
    "condition_category": "orthopedic",
    "answers": [
      {
        "question_id": "hoos_stairs",
        "normalized_value": "mild",
        "confirmed": true,
        "acceptance_method": "explicit_selection",
        "confidence": 1.0,
        "clarification_attempts": 0
      }
    ]
  }
}
```

The example abbreviates `answers`; a valid payload accounts for all six canonical
IDs exactly once across `answers` and `unanswered_questions`. Unanswered entries
contain `question_id`, `reason` (`clarification_limit` or `no_response`), and
`clarification_attempts`, never a normalized answer. They force
`needs_human_review=true`; `skipped` retains their IDs for compatibility.
`transcript` holds speaker, text, question ID, and timestamp for recognized
survey turns. HOOS JR uses `hoos_stairs`, `hoos_uneven_surface`, `hoos_rising`,
`hoos_bending`, `hoos_lying_bed`, `hoos_sitting`. Stroke uses instrument
`stroke_mobility`, condition `stroke`, and `stroke_balance`, `stroke_weakness`,
`stroke_stairs`, `stroke_turning`, `stroke_walking`, `stroke_recovery`.
Both instruments use version `"1"` and values `none`, `mild`, `moderate`,
`severe`, `extreme`. Existing `SafeSurveyEngine` still distinguishes direct
`explicit_selection` from separate `confirmation` of validated proposals.
Raw utterances, timestamps and invented patient names are not added to the payload.

Pain accepts 1–10; falls accept nonnegative counts. Booleans require yes/no.
Free text is bounded to 500 characters; complaints are semicolon-separated,
at most ten entries of 120 characters. Explicit unknown is retained as null
independently in each field.

Successful ingestion must return `status:"stored"`, `patient_url`, and
`patient_access_expires_at`. The URL is validated for the patient path and used
**verbatim** in SMS; phone never constructs or signs a replacement and does not
need `PATIENT_LINK_SIGNING_SECRET`. A 409 does not change the call ID or payload.
Unconfirmed saving yields truthful no-link speech. An operator can replay the
frozen payload; no automatic survey/SMS retry occurs.

### Provider receipts, callbacks and recovery

Twilio REST dispatch has an eight-second bound and no retries. Definitive 4xx
rejections (except 408) become failures; timeout, 5xx and malformed success
responses become unknown. A missing response is never proof no call/message
was sent. `sent` means provider-reported sent; only `delivered` confirms delivery.

Twilio posts signed callbacks to `/twilio/voice?call_id=...`,
`/twilio/status?call_id=...`, and
`/twilio/sms-status?call_id=...&attempt=...`. Validation covers the exact public
URL, query and complete form, including repeated values, then checks account
and provider IDs. Call `SequenceNumber` and terminal states reject stale
progress; message state cannot regress and previous attempts cannot overwrite
the current message. Media uses `/twilio/media`, a one-use stream ticket and
bound main call/phone session/provider call IDs. A consumed stream cannot resume
or repeat a survey.

`PHONE_RECEIPTS_PATH` defaults to
`~/.local/share/walking-on-sunshine/phone_receipts.sqlite3`. SQLite receipts
contain identifiers, HMAC request fingerprints, versions, provider IDs and
retry reservations. They contain no clinical answers, transcript, raw audio,
phone number, URL, patient token or credential. The file is mode 0600; place
it on persistent restricted storage and retain the same operator token for
request-fingerprint comparisons. Run one process/worker; this is not a
distributed dispatch queue.

On restart, uncertain dispatch/message reservations remain unknown; stored
receipts are published without repeating sends. Main can reconcile using GET.
Destination numbers and frozen survey payloads exist only in process memory;
after restart, missing payloads yield 409 and a reserved SMS retry without its
destination fails without sending. Reconcile with main/provider records before
starting a new call. Never delete a receipt to force another dispatch.

### Recorded doctor greeting

Orthopedic calls open with the clinician's own recorded greeting
(`assets/doctor_greeting.ulaw`, raw 8 kHz mono mu-law) before any synthesized
speech. The line is muted and barge-in is off while it plays, so nothing said
during the clip becomes an answer, and the assistant then uses a one-line intro
that does not repeat what the doctor said. Convert a new recording with
`ffmpeg -i greeting.m4a -ar 8000 -ac 1 -f mulaw assets/doctor_greeting.ulaw`;
`DOCTOR_GREETING_AUDIO` points at another file (empty disables the clip) and
`DOCTOR_GREETING_CONDITIONS` lists the condition categories it applies to.
An unreadable or over-long clip is logged and skipped rather than failing calls.

### Walking guidance

Verbal “ready” is conversational only. Polling waits for main `ready`
(camera calibration), then prompts the patient to start capture when safe.
Permission denial and recoverable errors produce retry guidance. Pause and stop
remain responsive because backend polling runs outside the serialized speech
turn. Default walking wait is 180 seconds and total call limit 600 seconds
(`MAX_CALL_SECONDS`).

Fifteen seconds is only suggested walking duration. Capture completion, verbal
completion or an elapsed timer never means a saved test. Only a matching
`call_id`/`attempt_id` with backend `status:"saved"` **and** `session_id` triggers
saved-result speech. Missing or unavailable evidence produces “cannot confirm”
speech. No clinician-follow-up task or notification is promised.

### Offline validation and provider limits

Python tests cover both instruments, independent unknown generic fields,
idempotency, lost responses, failed/unknown SMS and delivery callbacks, signature
checks, stream scope, pause/stop and backend-confirmed saves. All calls, SMS,
links and audio in those tests are synthetic. Live Twilio/Deepgram delivery and
paid OpenAI evaluations require separate explicit validation; none were run for
this integration. Existing Node desktop tests fail eight cases because their
VM fixture lacks `sessionStorage`; the same failure reproduces on the supplied
backend foundation commit, and those tests are unchanged.

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
# Edit .env and set DEEPGRAM_API_KEY and OPERATOR_TOKEN
python voice_app.py
```

Open <http://127.0.0.1:8000>, enter the `OPERATOR_TOKEN`, choose a patient code, click **Start survey**, and
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

### Voice results dashboard

The desktop service also provides `/results`: patient-code search, status
filters, exact question wording, confirmed versus pending answers, prototype
scores, patient quotations and expandable transcripts. It refreshes every
10 seconds while visible. Missing/incomplete scores are displayed as a dash;
these raw sums are not official clinical scores.

Enter `OPERATOR_TOKEN` to unlock. The page sends it only in the authorization
header and retains it in tab-scoped session storage, never in a URL. Locking or
an authentication failure clears displayed records and the stored token.
Patient text is rendered as text, not HTML. Supabase, temporary local results,
and merged recovery results are clearly distinguished; recovered records are
individually labeled. Local copies disappear on restart.

When running the complete WalkingOnSunshine stack, serve this optional desktop
app on **8002**, not main's 8000 or phone's 8001:

```bash
.venv/bin/uvicorn voice_app:create_app --factory --host 127.0.0.1 --port 8002 --no-access-log
```

This is the standalone voice-demo store. The integrated phone-to-walking flow
continues to use main's patient store and clinician portal; it does not send its
clinical records to this Supabase dashboard. No migration is needed to use the
dashboard against the existing voice-demo schema.

The active survey implementation is in `app/`; this checkout does not include
the earlier `survey_intelligence/` prototype.

## Standalone demo database / Supabase (additive, optional)

The standalone desktop demo looks up patients in memory. When Supabase server credentials are
set, it also stores each conversation's synthetic patient id, transcript text,
and confirmed survey results. An optional Postgres/Supabase schema supports
longitudinal storage, confirmed-value scoring, a clinician dashboard view, and
an answer-audit view. The integrated phone runtime does not use this layer;
main's JSON patient store remains authoritative and requires no Supabase migration.

This layer is **synthetic/demo data only** and is **not HIPAA compliant**.
It does not store raw audio. Scoring uses confirmed answers only, never AI
proposals.

See [DATABASE.md](DATABASE.md) for schema, seed data, migrations, and
backend connection notes. See [SECURITY.md](SECURITY.md) for RLS,
service-role rules, and production gaps.

Do not put `SUPABASE_SERVICE_ROLE_KEY` or `DATABASE_URL` in frontend code.

# Integrated voice and walking operator runbook

## Runtime and ownership

| Process | Port | Environment | Data |
| --- | --- | --- | --- |
| Main FastAPI | 8000 | `backend/.venv` | Supabase patient store or explicit local JSON demo store; summary cache |
| Phone FastAPI | 8001 | `voice_agent/.venv` | Private SQLite status receipts; bounded in-memory live sessions |
| Next.js | 3000 | Node 20.19.0 / npm lockfile | No server secrets in public environment |

Use Python 3.12 and **one worker** per Python service. Main's cache and locking are
process-local. Phone's stream tickets, numbers and live sessions are in memory.
Do not install both Python requirements into one environment: main pins
`openai==3.16.1` and voice requires `openai<3`.

Main owns patient IDs, explicit clinician condition metadata, surveys, call
attempts, signed patient URLs, walking state and gait sessions. Phone executes
the reserved call and publishes status. Configure the separate integrated
[Supabase patient store](supabase-patient-store.md) for durable linked records.
Existing standalone voice-demo tables remain separate and unchanged.

## Install and configure

From the repository root:

```bash
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
python3.12 -m venv voice_agent/.venv
voice_agent/.venv/bin/pip install -r voice_agent/requirements.txt
cd frontend
npm ci
```

Copy each service's `.env.example` into its local ignored environment file.
Main needs clinician username/password/session secret, patient-link signing
secret, service ingest token and patient-app base URL. Use independent random
secrets. Main and phone share `SURVEY_INGEST_TOKEN` and `OPERATOR_TOKEN`; the
signing secret stays **only in main**. Frontend has only
`NEXT_PUBLIC_API_URL=http://localhost:8000`.

Configure these links:

| Main environment | Phone environment |
| --- | --- |
| `PHONE_SERVICE_BASE_URL=http://127.0.0.1:8001` | `MAIN_BACKEND_URL=http://127.0.0.1:8000` |
| `OPERATOR_TOKEN=<shared-operator-secret>` | `OPERATOR_TOKEN=<same>` |
| `SURVEY_INGEST_TOKEN=<shared-service-secret>` | `SURVEY_INGEST_TOKEN=<same>` |
| `PATIENT_APP_BASE_URL=http://localhost:3000` | Never configure the patient signing secret here |

Use localhost URLs only for local checks. A patient's real phone requires a
reachable HTTPS frontend/API. Set exact CORS origins and use secure cookies
outside the explicit local HTTP override. Set `PATIENT_LINK_TTL_SECONDS`
according to the intended session duration (default 900, accepted 60–604800).
Expired links fail closed; changing the signing secret invalidates all links.

Run in separate terminals from the repository root:

```bash
(cd backend && .venv/bin/uvicorn main:app --port 8000 --no-access-log)
(cd voice_agent && .venv/bin/python phone_app.py)
(cd frontend && npm run dev)
```

Main `/api/health` is a liveness check, not proof that credentials or providers
are ready. Phone `/api/config` reports integrated mode and readiness; missing
provider/service configuration makes dialing fail closed. Main can still run
and test without Twilio, Deepgram or OpenAI credentials.

## Deterministic offline demo and checks

This is the supported repeatable demonstration without a phone, camera, browser
or paid provider. It runs both real HTTP adapters on ephemeral loopback ports
with synthetic patients/telemetry, isolated stores, fake telephony and the exact
survey engine. It overrides provider credentials and sets no OpenAI key.

```bash
mkdir -p .cache
backend/.venv/bin/python -m pytest -q tests/integration/test_handshake.py --basetemp=.cache/handshake
OPENAI_API_KEY= backend/.venv/bin/python -m pytest -q backend/tests
(cd voice_agent && OPENAI_API_KEY= RUN_LIVE_SURVEY_TESTS= SURVEY_EXTRACTOR=exact \
  .venv/bin/python -m pytest -q -m 'not live')
node --test voice_agent/tests/*.cjs
(cd frontend && npm test && npm run lint && npm run type-check && npm run build)
git diff --check
```

The handshake covers orthopedic/stroke, explicit known and unknown generic
intake, confirmed condition answers, canonical-link SMS, failed/ambiguous SMS,
explicit retry, invalid clinician/service/operator/patient credentials,
wrong-patient/attempt writes, duplicate/reordered status and walking events,
synthetic gait processing, idempotent save, clinician detail and synthesis.
Creating a subsequent call cannot reuse an old walk for active-call synthesis.

Fixture controls exist **only** in `tests/integration/phone_fixture.py`; never
serve that module to users or deploy it. Production uses `phone_app.py`.
Synthetic subprocess logs/stores remain under the selected pytest basetemp;
these contain test-only tokens and answers. Do not use real clinical records.

CI preserves backend/frontend jobs and adds `voice-python`, `voice-js` and
`integration`. Live model evaluations are deselected. Narrow Ruff/mypy checks
cover the new service contracts and providers rather than imposing new rules
on unrelated legacy modules.

The desktop voice fixtures supply a session-storage operator token and model
authenticated speech fetches with blob playback. They assert bearer headers
on session, speech and audio requests, token-free URLs and object URL renewal,
alongside the existing microphone, silence, pause, retry and completion cases.

Run frontend unit tests without a backend listening on port 8000. The legacy
patient-screening fixture stubs patient reads/writes but leaves the walking
status request unmocked; a running server rejects its synthetic credential.
The integration-specific frontend fixtures mock walking status explicitly.

## Clinician and patient flow

1. Sign in to `/doctor`; select the existing canonical patient.
2. Explicitly select orthopedic or stroke if condition metadata is missing.
   Never infer this from symptoms or cohort. Starting the call freezes this
   metadata and allocates `call_id`/`attempt_id`.
3. Provide an E.164 destination and start once. Keep the same request ID when
   recovering an uncertain HTTP result; a new ID is a new request.
4. Phone collects three generic facts (pain, falls, dizziness), preserving refusals/unknowns as null,
   and six condition items. Explicit selections are confirmed values; inferred
   proposals require a separate yes/no confirmation. Stop/clarification limits
   terminate or escalate rather than fabricating answers.
5. Main stores the integrated payload idempotently, issues the signed URL and
   returns it to phone. Phone reserves SMS durably and sends that exact URL.
6. Patient opens the unexpired link. It is removed from the browser address bar
   and held in component memory. Reopening/reloading needs the original URL.
7. Patient permits the camera and calibrates, or uploads a video. Live capture
   starts automatically; choose **Save this walk** when a detected result is
   available. Uploads auto-save. About 15 seconds is guidance, not completion.
8. Patient events and saves carry the same call/attempt identity. Only main's
   `saved` status **and** `session_id` let phone confirm success.
9. Clinician reviews both survey sections, call/SMS status, walking timeline,
   saved session and synthesis. A call that ended without a saved walk is not
   a completed walking test. Synthesis requires the active call's matched data.

## Recovery, retries and reconciliation

| Symptom | Interpretation and next action |
| --- | --- |
| Call start returns `unknown` | Main cannot prove whether provider dispatch occurred. Refresh the existing call; do not auto-redial or mint a new request ID. |
| SMS `sending`/`unknown` | Delivery outcome is unresolved. Wait for a signed callback or refresh/reconcile. Do not resend automatically. |
| SMS `failed` | After a stored survey, clinician may reserve one retry with a new stable retry ID; repeated retry requests reuse that attempt. Main generates its link. |
| Main unavailable before required status publication | No outbound call/SMS is dispatched. Phone marks the operation failed with `provider_unavailable` and republishes when main recovers. |
| Survey submission response lost | Retry the same frozen payload. Main returns the same survey; a changed payload is `409`. No duplicate SMS dispatch follows a repeated submit. |
| Expired patient link | Reopen an unexpired original link or contact the care team. There is no patient-side signing or auth bypass. A successful/unknown SMS cannot be automatically resent solely to renew a link. |
| `401` on patient API | Missing/invalid/expired/wrong-patient token. Stop and request a valid link. Do not fall back to clinician APIs. |
| `409` on event/session | Stale call/attempt, terminal attempt or conflicting idempotency payload. Stop that capture and refresh the active context; never relabel an old result as a new attempt. |
| Capture done but no `session_id` | Not saved. Resolve/retry the same session request with its stable key; phone must not claim success. |
| Microphone/camera denied or recoverable event | Show bounded help/pause state. Respect stop and patient safety; no assumed permission/success. |
| Phone restarts | Durable receipt prevents replaying dispatch. Active audio, numbers, tokens and clinical answers cannot be reconstructed from the phone receipt. Main separately retains the private destination on the patient call record; this does not authorize automatic redispatch. Reconcile, then involve an operator. |
| Main store unreadable | Main fails closed. Restore the authoritative store from an approved backup; do not replace it with seeded data to “fix” the session. |

Main records capped event history (200 per attempt), calls (100 per patient)
and gait sessions (50 per patient). Operate one worker per service; multi-worker
scaling, long-term archival and disaster recovery require further design.
Polls/timeouts are bounded; phone walking wait ends with a care-team message
when backend confirmation never arrives, without claiming a save.

## Provider provisioning and separately approved live validation

For live operation configure `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_FROM_NUMBER`, `DEEPGRAM_API_KEY` and externally reachable HTTPS
`PUBLIC_BASE_URL` terminating at phone port 8001. Ensure the account/number
supports the intended destinations and SMS. The Twilio adapter registers the
TwiML, call-status and SMS-status callback URLs; preserve their exact public
origin through the proxy so signatures validate. Keep media websocket support
and the one-use stream ticket enforcement enabled. Do not log access URLs,
authorization headers, transcripts or raw audio.

Only after separate approval, with synthetic records and an authorized
recipient, validate audio latency, STT/TTS, call/SMS callbacks, actual delivery,
mobile camera/upload permission flows and the displayed clinician result.
The offline harness does not validate these provider/device behaviors and does
not certify clinical accuracy. HOOS JR/stroke raw sums remain prototype
indicators. LLM summaries have a deterministic fallback and are decision
support, not a diagnosis. No live call, SMS, model evaluation, deployment or
browser test was performed as part of this integration.

For exact payloads and status vocabulary, see
[the API/data contract](patient-access-contract.md).

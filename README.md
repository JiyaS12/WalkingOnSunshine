# Sana

Sana connects a conversational check-up call, a personalized walking-assessment
link, and a clinician dashboard. Built as a hackathon prototype for orthopedic
and stroke mobility follow-up, it keeps survey responses and walking results
linked to the same patient and call.

**Prototype only:** use synthetic patients and consenting test recipients.
Survey scores, gait risk estimates, and AI summaries are not validated clinical
decisions. This repository does not establish HIPAA compliance or readiness to
store real patient data.

## What the app does

1. **Start a check-up:** a clinician signs in at `/doctor`, selects an existing
   patient and condition, enters a phone number, and clicks **Start call**.
   Starting the servers alone never places a call.
2. **Talk naturally:** Twilio carries the call; Deepgram handles speech
   recognition and voice output. Configured orthopedic calls start with the
   bundled recorded doctor greeting. The helper then asks six fixed questions
   and detects the end of a spoken turn without a button press.
3. **Keep answers trustworthy:** OpenAI can interpret conversational replies
   into the survey's allowed options. A clear selection is accepted directly;
   an inferred answer or relative correction is proposed for patient agreement.
   Questions and options remain application-controlled. Short acknowledgments
   can vary within the companion's language guardrails.
4. **Continue through uncertainty:** after three unsuccessful clarifications,
   or repeated silence on an active question, the helper leaves the answer
   blank and continues. The submitted survey includes the recognized transcript,
   unanswered-question metadata, and a **Needs human review** flag. An incomplete
   survey receives no total score. Stop requests and overall call limits still
   apply.
5. **Offer a walking link:** after the survey, the helper asks permission to
   send a text. The backend saves the survey; with consent, the phone service
   texts an expiring signed patient link to the call's destination. Declining
   the link still saves the survey, without sending an SMS.
6. **Capture and review:** the link opens a simple camera/video-upload screen,
   without scores or a skeleton overlay. Saved walking results carry the same
   patient, call, and attempt IDs. The clinician can review surveys, transcripts,
   missing answers, call status, and gait results in the portal and its
   authenticated, read-only **Database** tab.

The current phone flow asks the condition survey directly. Generic intake
fields remain in the API contract but are sent as unknown/null; they are not
inferred from condition answers. Transcripts are stored with survey submission,
not continuously backed up during the call. Interrupted calls may therefore
lack a saved survey or transcript.

Gait processing uses MediaPipe/OpenCV and joint telemetry to estimate stride
length, asymmetry, velocity degradation, cadence, knee range of motion, peak
ankle speed, and a prototype leg-length-normalized fall-risk indicator. Missing
landmarks can be interpolated; `dropped_frame_pct` reports repaired frames and
clips missing more than half their frames are rejected.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full system overview:
the patient client, voice-agent intake, doctor's portal, the end-to-end data
workflow, the complete endpoint reference, and which pieces are implemented
today.

## Supported runtimes

- Node.js 20.19.x (the repository's `.nvmrc` selects 20.19.0).
- npm 10 or newer.
- Python 3.12 (the pinned MediaPipe build does not install on Python 3.13).

The repository's `.python-version` selects Python 3.12 for version managers
that support it.

```
Sana/
  backend/
    main.py          FastAPI app and route definitions
    patient_access.py signed patient-token and link generation/verification
    processor.py     GaitProcessor: frames -> GaitMetrics
    video.py         OpenCV + MediaPipe extraction for uploaded video
    store.py         patient records: surveys, sessions, synthesis
    agent.py         clinical summary (OpenAI gpt-4o-mini w/ template fallback)
  frontend/
    app/page.tsx              public secure-link instructions
    app/patient/[id]/page.tsx signed per-patient screening (webcam / upload)
    app/doctor/page.tsx       clinician dashboard (live-syncs)
    app/components/PatientScreening.tsx  patient screening view
    app/components/ClinicianDatabase.tsx  authenticated record browser
  supabase/migrations/       integrated patient-store SQL
  docs/                     setup, architecture, and data contracts
  data/
    mock_patients.json  seed patients for the doctor's portal
  voice_agent/
    phone_app.py     Twilio + Deepgram outbound phone survey and gait SMS handoff
    voice_app.py     desktop push-to-talk variant of the same survey
    app/             survey engine, conversation policy, telephony bridge
```

The automated voice agent lives in [`voice_agent/`](voice_agent/README.md).
Main runs on **8000**, phone on **8001**, and the frontend on **3000**.
Use separate Python virtualenvs: main pins `openai==3.16.1`, while voice
requires `openai<3`. Main starts and tests without any telephony credentials.

The copied voice-results dashboard is available from the optional desktop
voice service on **8002** (keep 8000 reserved for main):

```bash
(cd voice_agent && .venv/bin/uvicorn voice_app:create_app --factory --host 127.0.0.1 --port 8002 --no-access-log)
```

Open <http://127.0.0.1:8002/results> and enter the voice service's
`OPERATOR_TOKEN`. It shows standalone voice-demo answers, scores and transcripts
from Supabase or explicitly labeled local fallback, with search and automatic
refresh. It does **not** mix integrated phone/gait records into the standalone tables;
review those in <http://localhost:3000/doctor>. Both interfaces remain protected
by their existing authentication boundaries. Only synthetic data should be used
for local testing. Starting these servers does not place a phone call.

The authenticated clinician explicitly supplies `orthopedic` or `stroke`
condition metadata. Main reserves the call; phone collects HOOS JR/stroke items.
Main persists confirmed answers and review metadata in its configured patient
store and issues the signed patient URL. With SMS consent, phone texts that
exact URL, then polls the patient's walking events.
Only a persisted session with matching call/attempt IDs confirms completion.
The clinician timeline and synthesis join that saved result to its survey.
Unknown condition metadata is never inferred from complaints, age, or cohort.

See the [integration/operator runbook](docs/integration-runbook.md) for setup,
the offline deterministic demo, failure recovery and live validation steps.
The Sana screens use the shared pastel design system. Patient access still
requires the complete signed SMS link; the public landing page does not offer
patient-ID lookup.
The [API/data contract](docs/patient-access-contract.md) describes all three
authentication boundaries and the call/survey/walking payloads. Integrated
records can use the separate [Supabase patient store](docs/supabase-patient-store.md)
or the explicitly configured local JSON demo store.

## Backend setup

Clone the upstream repository (or use your own fork), then enter it:

```bash
git clone https://github.com/JiyaS12/Sana.git
cd Sana
```

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

In a new terminal, from the repository root:

```bash
cd backend
cp .env.example .env
# Fill the username/password and generate distinct clinician-session,
# patient-link, and survey-ingest secrets with:
# python -c "import secrets; print(secrets.token_urlsafe(48))"
.venv/bin/uvicorn main:app --port 8000 --env-file .env
```

Copy example files only on first setup; do not overwrite an existing `.env`.
Keep actual credentials in ignored local environment files, never in the
tracked examples. Run one worker per Python service.

The three clinician values are required. Clinician APIs fail closed with `503`
until `CLINICIAN_USERNAME`, `CLINICIAN_PASSWORD`, and a random
`CLINICIAN_SESSION_SECRET` of at least 32 characters are all present. `/doctor`
exchanges the credentials for a signed, HttpOnly, `SameSite=Lax` session cookie;
neither the credentials nor signing key belong in a `NEXT_PUBLIC_*` variable.

Runtime environment variables:

- `CLINICIAN_USERNAME` / `CLINICIAN_PASSWORD` — local clinician sign-in.
- `CLINICIAN_SESSION_SECRET` — random server-only session signing key (minimum
  32 characters).
- `CLINICIAN_SESSION_TTL_SECONDS` — session lifetime in seconds (default 28800,
  accepted range 60–86400).
- `CLINICIAN_COOKIE_SECURE` — defaults to `true`; set `false` only for explicit
  local HTTP development.
- `CLINICIAN_LOGIN_MAX_ATTEMPTS` / `CLINICIAN_LOGIN_WINDOW_SECONDS` — per-process,
  per-client login throttle (defaults 5 attempts per 60 seconds).
- `CORS_ALLOWED_ORIGINS` — exact comma-separated frontend origins. Localhost and
  127.0.0.1 on port 3000 are the development defaults. `*` is rejected because
  clinician sessions use credentialed requests.

Required patient-link configuration:

- `PATIENT_LINK_SIGNING_SECRET` — a deployment secret containing at least 32
  bytes. Survey ingestion fails closed when it is absent or too short.
- `PATIENT_APP_BASE_URL` — public patient-app base URL used to construct the
  complete signed link (default `http://localhost:3000`; non-local URLs must use
  HTTPS).
- `PATIENT_LINK_TTL_SECONDS` — link lifetime from 60 to 604800 seconds (default
  `900`).

Service authentication and optional environment variables:

- `OPENAI_API_KEY` — enable LLM-generated summaries/synthesis
  (deterministic template fallback without it). Summaries are cached in
  `backend/.cache/summaries.json`; `GET /api/summary-cache-stats` reports
  cache stats.
- `SURVEY_INGEST_TOKEN` — required for `POST /api/submit-survey`; callers send
  it in `X-Survey-Token`. It also disables `POST
  /api/patients/{id}/ensure-demo` (403) unless `ALLOW_DEMO_PATIENTS` is set.
- `PHONE_SERVICE_BASE_URL` — optional phone origin (local `http://127.0.0.1:8001`).
  `OPERATOR_TOKEN` must match the phone service's server-only bearer token.
- `ALLOW_UNAUTHENTICATED_SURVEY_INGEST` — local-only escape hatch. Set to
  `1`/`true`/`yes` to run survey ingestion without a token. Never enable this
  in a shared or production environment.
- `ALLOW_DEMO_PATIENTS` — local-only support for the clinician-authenticated
  demo-record endpoint. It does not make `/patient/[id]` public or bypass a
  signed patient link.

See [the patient access contract](docs/patient-access-contract.md) for stable
request/response examples used by the patient UI and voice/SMS integrations.

## Frontend setup

From the repository root:

```bash
cd frontend
npm ci
cp .env.example .env.local
npm run dev
```

The app runs at http://localhost:3000. For same-origin API requests, configure
`frontend/.env.local` as follows and restart Next.js:

```dotenv
NEXT_PUBLIC_API_URL=
API_PROXY_TARGET=http://127.0.0.1:8000
```

The intentionally empty public URL makes the browser call `/api/*` on the
frontend's own origin; Next.js proxies those requests to the backend. The
default direct-API alternative is `NEXT_PUBLIC_API_URL=http://localhost:8000`.
Never put credentials or access tokens in `NEXT_PUBLIC_*` variables. For local
HTTP testing set `CLINICIAN_COOKIE_SECURE=false` in the backend; use `true`
with public HTTPS and configure the exact frontend CORS origin.

## Phone service and real-call setup

From the repository root, create the separate phone environment:

```bash
cd voice_agent
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Configure `voice_agent/.env` with `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_FROM_NUMBER`, and `DEEPGRAM_API_KEY`. For conversational interpretation,
set `SURVEY_EXTRACTOR=openai` and `OPENAI_API_KEY`; `SURVEY_EXTRACTOR=exact`
supports offline exact-option handling instead. The backend's OpenAI key is
separate configuration for optional clinician summaries.

Match these service settings (tokens are private, independently generated
secrets, not the literal placeholders below):

| `backend/.env` | `voice_agent/.env` |
| --- | --- |
| `PHONE_SERVICE_BASE_URL=http://127.0.0.1:8001` | `MAIN_BACKEND_URL=http://127.0.0.1:8000` |
| `OPERATOR_TOKEN=<operator-secret>` | `OPERATOR_TOKEN=<same-operator-secret>` |
| `SURVEY_INGEST_TOKEN=<ingest-secret>` | `SURVEY_INGEST_TOKEN=<same-ingest-secret>` |

Keep `PATIENT_LINK_SIGNING_SECRET` only in the backend. The recorded greeting is
controlled by `DOCTOR_GREETING_AUDIO` and `DOCTOR_GREETING_CONDITIONS` on the
phone service; an explicitly empty audio value disables it.

### Public HTTPS for a phone demo

Twilio needs to reach the phone service, and a patient's phone needs to reach
the website. With `cloudflared` installed, run these in two separate terminals:

```bash
cloudflared tunnel --url http://127.0.0.1:8001
```

```bash
cloudflared tunnel --url http://127.0.0.1:3000
```

Use the actual URLs printed by those processes:

| Environment file | Setting |
| --- | --- |
| `voice_agent/.env` | `PUBLIC_BASE_URL=https://<phone-tunnel-host>` |
| `backend/.env` | `PATIENT_APP_BASE_URL=https://<website-tunnel-host>` |
| `backend/.env` | `CORS_ALLOWED_ORIGINS=https://<website-tunnel-host>` |
| `backend/.env` | `CLINICIAN_COOKIE_SECURE=true` |
| `frontend/.env.local` | `ALLOWED_DEV_ORIGINS=<website-tunnel-host>` (hostname only) |

Use the frontend proxy settings above; the backend does not need its own public
tunnel in this arrangement. Keep both tunnel terminals and all three services
running. If tunnel URLs change, update the settings and restart the services;
previously sent links pointing at an old tunnel will not work.

After configuration, run these in three separate terminals from the repository
root (or restart existing instances; do not start duplicate servers):

```bash
(cd backend && .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --env-file .env --no-access-log)
(cd voice_agent && .venv/bin/uvicorn phone_app:create_app --factory --host 127.0.0.1 --port 8001 --env-file .env --no-access-log)
(cd frontend && npm run dev)
```

Check backend `/api/health` and phone `/api/config`, then open `/doctor` on the
website URL. A readiness response checks configuration, not actual provider
delivery. Only click **Start call** for a number you control or have permission
to contact; real calls, texts, and model requests may incur provider charges.

## Supabase and the clinician database

Follow the [Supabase setup guide](docs/supabase-patient-store.md) to apply
`supabase/migrations/20260920080000_integrated_patient_records.sql` to your
project (the demo uses **HackMIT2026**). Set these in `backend/.env`:

```dotenv
PATIENT_STORE=supabase
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<server-only-key>
SUPABASE_SEED_DEMO=false
```

The new store starts empty. For a deliberate synthetic-data demo, the guide
explains one-time seeding; existing local or standalone voice data is not
automatically imported. Supabase failures do not silently fall back to JSON.
For local-only storage, explicitly set `PATIENT_STORE=json`.

In **Doctor portal → Database**, search patients and inspect calls, destination
numbers, survey answers, review flags, transcripts, and saved gait measurements.
The same clinician sign-in protects this read-only view; no Supabase dashboard
login is needed. It does not offer arbitrary SQL, editing, or deletion.

`walking_patient_records` stores the linked patient aggregate;
`walking_assessment_results` provides a survey/walking review view. Patient ID
is the permanent identity; the phone destination stays private on its call
record, not in the patient URL. Review/transcript fields use the existing JSON
record and need no additional migration. Signed links are bearer credentials:
anyone holding a valid link can use it until it expires, so do not share or log
complete links. Audio and raw video recordings are not stored by this flow.

## Local-only signed-link demo

The public home page never lists patients, accepts arbitrary IDs, or creates
demo records. A local demo uses the same signed handshake as production:

1. In the backend's local `.env`, set a random
   `PATIENT_LINK_SIGNING_SECRET`, set `SURVEY_INGEST_TOKEN=local-demo-only`, and
   leave `PATIENT_APP_BASE_URL=http://localhost:3000`.
2. Start the backend and frontend development servers.
3. Submit a local intake and copy the returned `patient_url` (treat it as a
   credential and do not paste it into shared logs):

   ```bash
   curl -sS http://localhost:8000/api/submit-survey \
     -H 'Content-Type: application/json' \
     -H 'X-Survey-Token: local-demo-only' \
     -d '{"patient_id":"RGN-0417","patient_name":"Local Demo Patient","pain_scale":3,"fall_history":{"falls_last_6_months":0,"injured":false},"dizziness":false,"primary_complaints":["local demo"]}'
   ```

4. Open the complete returned URL. A bare `/patient/RGN-0417` path is expected
   to fail. Complete a **Live Camera** or **Upload Video** walk; only a detected
   walk can be saved.
5. Open `/doctor` directly, sign in with the backend-configured clinician
   credentials, and confirm the saved session appears in that patient's
   timeline.

## Verify a clean checkout

From the repository root, use separate Python 3.12 environments:

```bash
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
OPENAI_API_KEY= backend/.venv/bin/python -m pytest -q backend/tests
python3.12 -m venv voice_agent/.venv
voice_agent/.venv/bin/pip install -r voice_agent/requirements.txt
(cd voice_agent && OPENAI_API_KEY= RUN_LIVE_SURVEY_TESTS= SURVEY_EXTRACTOR=exact \
  .venv/bin/python -m pytest -q -m 'not live')
node --test voice_agent/tests/*.cjs
mkdir -p .cache
backend/.venv/bin/python -m pytest -q tests/integration/test_handshake.py --basetemp=.cache/handshake
```

Then use Node 20.19.x to install exactly the locked frontend dependencies,
audit production packages, lint, type-check, and build:

```bash
cd frontend
npm ci
npm audit --omit=dev --audit-level=high
npm test
npm run lint
npm run type-check
npm run build
```

These checks require no repository secrets. The handshake starts real main and
phone HTTP processes on ephemeral loopback ports, using their separate
virtualenvs, fake telephony and synthetic gait frames. It covers both conditions,
unknown generic answers, SMS failure/ambiguity, retries, event ordering, scoped
authorization and saved-session synthesis without cameras or paid providers.
GitHub Actions preserves backend/frontend gates and adds offline voice Python,
voice JavaScript and the integrated handshake.

## License

MIT — see [LICENSE](LICENSE).

# Sana

AI-assisted gait analysis for clinical mobility monitoring. A
Python/FastAPI backend processes MediaPipe-style joint telemetry
(meters, y vertical with larger y = higher) into clinical gait metrics
(stride length, left/right asymmetry — the mean of stance and knee-ROM
asymmetry — velocity degradation, cadence, knee flexion ROM, peak ankle
speed, and a leg-length-normalized weighted-logistic fall risk score),
stores patient records (surveys + gait sessions), and optionally
generates LLM clinical summaries.

Fall risk is normalized by estimated leg length (stride ratio ≈ 1.4–1.6× leg
length and knee flexion ROM ≈ 50–65° for healthy gait score < 0.15); standing
still is never flagged. Trial metrics use leg length estimated from the recorded
frames; callers can also provide `leg_length_m` to the frame endpoint.

Landmarks that drop out mid-clip are linearly interpolated rather than
discarded, so one missing frame cannot zero out a genuine high-risk score;
`dropped_frame_pct` reports how much of a session was repaired, and a clip
missing more than half its frames is rejected outright.

The experimental branch also exposes a separate **Experimental CV Risk Index
(0–100)**. It uses a guided three-second countdown and ten-second walk toward
or away from a fixed camera. The index is percentile-like relative to the
Toronto Older Adults Gait Archive; it is not a fall probability and has no
low/moderate/high clinical bands. If recording quality fails or no trained
artifact passes the validation gate, the API returns `null` with an explicit
status while preserving the original heuristic score.

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
gaitguard-ai/
  backend/
    experimental_risk.py  quality-gated features + learned model runtime
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

The authenticated clinician selects an existing patient and explicitly supplies
`orthopedic` or `stroke` condition metadata. Main reserves the call; phone
collects the generic intake and confirmed HOOS JR/stroke items separately.
Main persists both in its JSON patient store and issues the signed patient
URL. Phone texts that exact URL, then polls the patient's walking events.
Only a persisted session with matching call/attempt IDs confirms completion.
The clinician timeline and synthesis join that saved result to its survey.
Unknown condition metadata is never inferred from complaints, age, or cohort.

See the [integration/operator runbook](docs/integration-runbook.md) for setup,
the offline deterministic demo, failure recovery and live validation steps.
The Sana screens use the shared pastel design system. Patient access still
requires the complete signed SMS link; the public landing page does not offer
patient-ID lookup.
The [API/data contract](docs/patient-access-contract.md) describes all three
authentication boundaries and the call/survey/walking payloads. Supabase remains
an optional standalone voice-demo integration, not the integrated patient store.

## Backend setup

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
cd backend
cp .env.example .env
# Fill the username/password and generate distinct clinician-session,
# patient-link, and survey-ingest secrets with:
# python -c "import secrets; print(secrets.token_urlsafe(48))"
.venv/bin/uvicorn main:app --port 8000 --env-file .env
```

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

```bash
cd frontend
npm ci
cp .env.example .env.local   # sets NEXT_PUBLIC_API_URL (default http://localhost:8000)
npm run dev
```

The app runs at http://localhost:3000 and expects the backend on
`NEXT_PUBLIC_API_URL`. This variable is only the public backend URL; do not add
clinician credentials or access tokens to the frontend environment. For cookie
delivery, deploy the frontend and API on the same site and list the frontend's
exact origin in `CORS_ALLOWED_ORIGINS`.

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
   to fail. Choose **Start 10-second assessment**, wait for the countdown, then
   walk toward or away from the fixed camera with your feet visible.
   Alternatively, upload a walking video. Only a detected walk can be saved.
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

If your shell already has `OPENAI_API_KEY`, unset it when running tests so the
deterministic template assertions do not call the external model.
For the optional public-data experiment and research tests, see
[the training guide](backend/research/README.md).

## License

MIT — see [LICENSE](LICENSE).

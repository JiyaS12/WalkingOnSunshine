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
still is never flagged. With `leg_length_m` supplied by the frontend's
60-frame calibration, live camera metrics use the user's own proportions.

Landmarks that drop out mid-clip are linearly interpolated rather than
discarded, so one missing frame cannot zero out a genuine high-risk score;
`dropped_frame_pct` reports how much of a session was repaired, and a clip
missing more than half its frames is rejected outright.

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
    main.py          FastAPI app and route definitions
    patient_access.py signed patient-token and link generation/verification
    processor.py     GaitProcessor: frames -> GaitMetrics
    video.py         OpenCV + MediaPipe extraction for uploaded video
    store.py         patient records: surveys, sessions, synthesis
    agent.py         clinical summary (OpenAI gpt-4o-mini w/ template fallback)
  frontend/
    app/page.tsx              landing page (patient ID lookup)
    app/patient/[id]/page.tsx per-patient screening (webcam / upload)
    app/doctor/page.tsx       clinician dashboard (live-syncs)
    app/components/PatientScreening.tsx  patient screening view
  data/
    mock_patients.json  seed patients for the doctor's portal
```

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

Optional environment variables:
- `OPENAI_API_KEY` — enable LLM-generated summaries/synthesis
  (deterministic template fallback without it). Summaries are cached in
  `backend/.cache/summaries.json`; `GET /api/summary-cache-stats` reports
  cache stats.
- `SURVEY_INGEST_TOKEN` — required for `POST /api/submit-survey`; callers send
  it in `X-Survey-Token`. It also disables `POST
  /api/patients/{id}/ensure-demo` (403) unless `ALLOW_DEMO_PATIENTS` is set.
- `ALLOW_UNAUTHENTICATED_SURVEY_INGEST` — local-only escape hatch. Set to
  `1`/`true`/`yes` to run survey ingestion without a token. Never enable this
  in a shared or production environment.
- `ALLOW_DEMO_PATIENTS` — set to `1`/`true`/`yes` to keep auto-created demo
  patient profiles enabled while survey ingestion is token-protected.

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

## Demo flow

Quickest path (macOS/Linux, needs Python 3.10-3.12 and Node 20):

```bash
git clone https://github.com/JiyaS12/WalkingOnSunshine.git
cd WalkingOnSunshine
./demo.sh
```

`demo.sh` creates the virtualenv, installs both sides, writes a local
`backend/.env` with demo clinician credentials and random secrets, starts both
servers, and prints the clinician login plus a magic link for the seeded demo
patient. Manual equivalent:

1. `cd backend && .venv/bin/uvicorn main:app --port 8000`
2. `cd frontend && npm run dev`
3. Open http://localhost:3000 — pick a patient link, type a patient ID, or
   click **Load Demo Patient RGN-0417**; any unknown ID auto-creates a demo
   profile (pain 3/10, no prior falls) so you can test right away.
4. On `/patient/<id>`: do a **Live Camera** walk or **Upload Video** —
   metrics update, sessions save to the patient's record.
5. Open `/doctor`, sign in with the backend-configured clinician credentials,
   and select the patient. The portal live-syncs every 5 s and provides the
   Unified Clinical Synthesis Report (survey + gait trend + skeleton replay).

## Verify a clean checkout

From the repository root, create a fresh Python 3.12 environment and run the
backend suite:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt
python -m pytest -q backend/tests
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

These checks require no repository secrets. GitHub Actions runs the same gates
for every push and pull request with npm and pip dependency caching enabled.

## License

MIT — see [LICENSE](LICENSE).

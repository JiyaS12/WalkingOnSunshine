# GaitGuard AI — Architecture

GaitGuard AI is a multi-modal post-stroke/surgery analysis platform that
bridges patient-facing mobile screening with clinician-facing diagnostic
tools. The system correlates subjective patient intake data with objective
biomechanical telemetry to evaluate fall risk and mobility degradation.

This describes the integrated implementation. Deterministic tests exercise real
HTTP services with fake providers; live calls, speech, SMS and physical walking
require separate operator validation.

## 1. System Components

The platform runs as three processes: authoritative FastAPI main on **8000**,
phone FastAPI on **8001**, and Next.js on **3000**. Main and phone use separate
virtualenvs (`openai==3.16.1` versus `openai<3`). Main never imports telephony
credentials or libraries to start. Run one worker for each Python service.

### Patient-Facing Client (`/patient/[id]`)

- Features a **Two-Way Input Mode Switcher**: Live Webcam Mode (client-side
  MediaPipe Pose joint tracking) and Pre-Recorded Video Upload (`.mp4`/`.mov`
  batch processing).
- Automatically calibrates to user proportions and extracts lower-extremity
  biomechanics (stride length, knee flexion, ankle velocity, and asymmetry
  percentage).

### Automated Voice Agent (Phone Interface)

- Manages outbound phone calls to conduct a structured clinical intake survey
  (pain 1–10, fall count/injury/description, dizziness/notes, complaints), then
  confirmed condition-specific HOOS JR or stroke items.
- Requires clinician-provided condition metadata. The condition answers never
  populate generic intake fields. Unknown facts stay null.
- Submits idempotently to main, texts main's exact canonical signed URL, and
  guides the patient using scoped backend walking state. Capture completion or
  elapsed time alone never means a saved walk.
- Keeps durable SQLite receipts containing only correlation and status metadata.
  Phone numbers, answers, transcripts, signed URLs and audio are not receipts.

### Clinician Dashboard / Doctor's Portal (`/doctor`)

- Provides a secure workspace for healthcare providers to review incoming
  patients.
- Generates a **Unified Clinical Synthesis Report** that maps subjective phone
  survey data against objective movement telemetry.

## 2. End-to-End User & Data Workflow

```
[ Authenticated Clinician ]
       │
       └─ Select canonical patient + explicit condition; request call
             │
             ▼
[ Main :8000 / JSON store ] ── reserve call/attempt ──► [ Phone :8001 ]
       │
       ◄── confirmed generic + condition surveys ────────────┘
       ├── persist once; issue canonical signed URL ────────► SMS provider
       └── walking state + saved session ◄──────────────────┐
             │
             ▼
[ Patient Web Client :3000 ]                              │
       │
       ├─ Option A: Live Webcam (MediaPipe client-side joint mapping)
       ├─ Option B: Upload video (.mp4 batch processing via FastAPI)
       └─ Signed events + gait session with call_id/attempt_id ─┘
             │
             ▼
[ FastAPI Backend Engine ]
       │
       ├─ Ingests & persists survey JSON payload (/api/submit-survey)
       ├─ Computes adaptive baseline metrics and fall-risk scores (GaitProcessor)
       └─ Synthesizes AI clinical summary via OpenAI (Long Lake track)
             │
             ▼
[ Doctor's Portal Dashboard ]
       └─ Displays unified side-by-side report: Subjective Survey + Objective Telemetry
```

## 3. Data Handshake & API Endpoints

The core endpoints that carry the cross-domain handshake:

- **`POST /api/submit-survey`** — Receives structured JSON from the voice agent
  containing patient symptoms and metadata, linking them securely to the
  patient record. Gated by an `X-Survey-Token` header using the required
  `SURVEY_INGEST_TOKEN` configuration (with an explicit local-only opt-out).
  Its response includes a
  complete, expiring `patient_url` for the voice/SMS service.
- **`POST /api/process-video`** — Accepts multipart video file uploads,
  executes batch MediaPipe pose extraction across frames, and returns computed
  stride and asymmetry telemetry.
- **`POST /api/generate-summary`** — Compiles patient survey responses and
  movement data into a plain-language clinical report using an optimized local
  heuristic token-caching layer.
- **`GET /api/patient-access/{pid}`** and **`POST
  /api/patient-access/{pid}/sessions`** — Read the minimum patient-facing view
  and save a session using the signed link's bearer credential.

### Full endpoint reference

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness probe. |
| `POST` | `/api/clinician/session` | Validate server-side clinician credentials and issue an HttpOnly session. |
| `GET` | `/api/clinician/session` | Validate the active clinician session. |
| `DELETE` | `/api/clinician/session` | Sign out and clear the clinician session. |
| `POST` | `/api/process-frame` | Joint telemetry (one session's frames) → `GaitMetrics`. |
| `POST` | `/api/process-video` | Multipart `.mp4`/`.mov` upload → `VideoAnalysis`. |
| `POST` | `/api/generate-summary` | Plain-language clinical summary (cached). |
| `GET` | `/api/summary-cache-stats` | Token-cache hit rate and tokens saved. |
| `POST` | `/api/submit-survey` | Voice-agent intake payload → patient record. |
| `GET` | `/api/patient-access/{pid}` | Signed-link patient view (bearer token required). |
| `POST` | `/api/patient-access/{pid}/sessions` | Signed-link session write (bearer token required). |
| `GET` | `/api/patients` | Cohort list for the doctor's portal (`?q=` search). |
| `GET` | `/api/patients/{pid}` | One patient: survey + session history. |
| `POST` | `/api/patients/{pid}/sessions` | Attach a gait session to a patient. |
| `POST` | `/api/patients/{pid}/synthesis` | Unified subjective + objective report. |
| `PATCH` | `/api/patients/{pid}/condition` | Persist explicit clinician condition. |
| `GET`, `POST` | `/api/patients/{pid}/calls` | List calls or reserve/dispatch one with a stable request ID. |
| `GET` | `/api/patients/{pid}/calls/{call_id}` | Read canonical call/survey/SMS/walking status. |
| `POST` | `/api/patients/{pid}/calls/{call_id}/refresh` | Reconcile bounded phone snapshot. |
| `POST` | `/api/patients/{pid}/calls/{call_id}/sms-retries` | Explicit retry after definitive SMS failure. |
| `GET` | `/api/patient-access/{pid}/walking` | Read active call/attempt walking context (patient bearer). |
| `POST` | `/api/patient-access/{pid}/walking/events` | Publish sequenced walking event (patient bearer). |
| `GET` | `/api/integration/patients/{pid}` | Read canonical condition and active call (service token). |
| `GET` | `/api/integration/patients/{pid}/calls/{call_id}` | Read reserved call (service token). |
| `POST` | `/api/integration/patients/{pid}/calls/{call_id}/status` | Merge versioned phone snapshot (service token). |
| `POST` | `/api/integration/patients/{pid}/calls/{call_id}/patient-link` | Issue fresh canonical link for active stored survey (service token). |
| `GET` | `/api/integration/patients/{pid}/calls/{call_id}/walking` | Poll scoped walking state (service token). |

The cohort list, patient detail, session administration, summary generation,
synthesis, and cache-statistics routes require the signed clinician session.
The session is separate from patient-link authorization: patient link tokens
are not accepted as clinician credentials. CORS uses the exact origins in
`CORS_ALLOWED_ORIGINS` and never combines a wildcard origin with credentials.

`/api/generate-summary` and `/api/patients/{pid}/synthesis` call OpenAI
`gpt-4o-mini` when `OPENAI_API_KEY` is set and fall back to a deterministic
template otherwise. Summaries are cached in `backend/.cache/summaries.json`.
For an active integrated call, synthesis requires that call's stored survey and
its saved gait session with the same attempt ID; otherwise it returns `422`.
Earlier sessions may inform the historical trend, but an unrelated old walk
cannot stand in for the active call's result. Raw condition-item sums are
explicitly labeled prototype scores, not validated clinical instrument scores.
The signed patient-link request/response examples and configuration contract are
defined in [`docs/patient-access-contract.md`](patient-access-contract.md).

## 4. Implementation status

| Capability | Status |
| --- | --- |
| Live webcam mode (client-side MediaPipe Pose) | Implemented |
| Pre-recorded video upload (`.mp4`/`.mov`) | Implemented |
| Auto-calibration to user proportions (leg length) | Implemented — 60-frame calibration feeds `leg_length_m` |
| Lower-extremity biomechanics + fall-risk score | Implemented — `GaitProcessor` |
| Survey ingestion, signed patient links, and patient-scoped APIs | Implemented — `/api/submit-survey`, `/api/patient-access/{pid}` |
| Doctor's portal and unified synthesis report | Implemented — `/doctor` |
| Clinician sign-in/session boundary | Implemented — signed HttpOnly session; server-only credentials |
| Patient client at `/patient/[id]` | Implemented — signed-link-only live/upload flow with route-safe loading and session writes. |
| Automated voice agent (outbound calls, intake) | Implemented: Twilio/Deepgram adapters, separate generic/condition surveys; fake-provider tests. |
| SMS with personalized deep link | Implemented: main-issued URL, durable dispatch receipts, explicit failed-SMS retry, ambiguity retained. |
| Live voice guidance during the walking test | Implemented: backend event polling and persisted-session completion gate; physical flow not validated here. |

See the [operator runbook](integration-runbook.md) for provisioning, offline
verification, provider validation and recovery limits. The JSON patient store
is authoritative. Optional Supabase remains confined to the standalone voice
demo; it is not a migration target for the integrated flow.

## Code map

```
gaitguard-ai/
  backend/
    main.py          FastAPI app and route definitions
    patient_access.py signed patient-token and link generation/verification
    integration_api.py clinician call controls and service/patient adapters
    integration_models.py strict condition/snapshot/walking contracts
    phone_client.py   bounded authenticated phone proxy
    processor.py     GaitProcessor: frames -> GaitMetrics
    video.py         OpenCV + MediaPipe extraction for uploaded video
    store.py         patient records: surveys, sessions, synthesis
    agent.py         clinical summary (OpenAI gpt-4o-mini w/ template fallback)
  frontend/
    app/page.tsx              public secure-link instructions
    app/patient/[id]/page.tsx signed per-patient screening view
    app/doctor/page.tsx       clinician dashboard
    app/components/           WebcamFeed, PatientScreening, SkeletonReplay,
                              TrendGraph, TokenEfficiency
    app/lib/api.ts            typed backend client
    app/lib/gait.ts           client-side pose helpers and calibration
    app/lib/walkingReporter.ts serialized scoped walking event reporter
  voice_agent/
    phone_app.py              operator endpoints and Twilio webhooks/media
    app/integrated_service.py reservations, submission and SMS reconciliation
    app/integrated_session.py generic/condition survey and walking guidance
    app/main_backend.py       authenticated main backend adapter
    app/phone_receipts.py      durable private status receipts
    app/telephony/integrated_provider.py bounded Twilio and fake providers
  tests/integration/           separate-process offline HTTP handshake
  data/
    mock_patients.json  seed patients for the doctor's portal
```

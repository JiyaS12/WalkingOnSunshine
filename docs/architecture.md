# GaitGuard AI — Architecture

GaitGuard AI is a multi-modal post-stroke/surgery analysis platform that
bridges patient-facing mobile screening with clinician-facing diagnostic
tools. The system correlates subjective patient intake data with objective
biomechanical telemetry to evaluate fall risk and mobility degradation.

> This document describes the target architecture. Parts of the voice-agent
> domain are not built yet; see [Implementation status](#4-implementation-status)
> for what exists in the repository today.

## 1. System Components

The platform consists of two primary operational domains:

### Patient-Facing Client (`/patient/[id]`)

- Features a **Two-Way Input Mode Switcher**: Live Webcam Mode (client-side
  MediaPipe Pose joint tracking) and Pre-Recorded Video Upload (`.mp4`/`.mov`
  batch processing).
- Automatically calibrates to user proportions and extracts lower-extremity
  biomechanics (stride length, knee flexion, ankle velocity, and asymmetry
  percentage).

### Automated Voice Agent (Phone Interface)

- Manages outbound phone calls to conduct a structured clinical intake survey
  (pain scores, fall history, dizziness, primary complaints).
- Automatically generates and texts a secure, personalized web link to the
  patient and guides them through the walking test.

### Clinician Dashboard / Doctor's Portal (`/doctor`)

- Provides a secure workspace for healthcare providers to review incoming
  patients.
- Generates a **Unified Clinical Synthesis Report** that maps subjective phone
  survey data against objective movement telemetry.

## 2. End-to-End User & Data Workflow

```
[ AI Voice Agent ]  (planned)
       │
       ├─ (1) Outbound Call & Voice Intake Survey
       ├─ (2) Real-Time SMS Text with Personalized Link (e.g., /patient/RGN-0417)
       └─ (3) Live Voice Guidance through Walking Test across Camera Frame
             │
             ▼
[ Patient Web Client ]
       │
       ├─ Option A: Live Webcam (MediaPipe client-side joint mapping)
       └─ Option B: Upload Pre-Recorded Video (.mp4 batch processing via FastAPI)
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

The cohort list, patient detail, session administration, summary generation,
synthesis, and cache-statistics routes require the signed clinician session.
The session is separate from patient-link authorization: patient link tokens
are not accepted as clinician credentials. CORS uses the exact origins in
`CORS_ALLOWED_ORIGINS` and never combines a wildcard origin with credentials.

`/api/generate-summary` and `/api/patients/{pid}/synthesis` call OpenAI
`gpt-4o-mini` when `OPENAI_API_KEY` is set and fall back to a deterministic
template otherwise. Summaries are cached in `backend/.cache/summaries.json`.
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
| Patient client at `/patient/[id]` | Base flow implemented; signed-link frontend integration is tracked in #23. |
| Automated voice agent (outbound calls, intake) | **Not implemented** — no telephony integration in the repository. |
| SMS with personalized deep link | **Not implemented** — depends on both the voice agent and the `/patient/[id]` route. |
| Live voice guidance during the walking test | **Not implemented** |

Shipping the remaining voice-agent domain requires a telephony provider (call +
SMS webhooks) and frontend consumption of the implemented signed-link contract.

## Code map

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
    app/page.tsx              landing page (patient links)
    app/patient/[id]/page.tsx per-patient screening view
    app/doctor/page.tsx       clinician dashboard
    app/components/           WebcamFeed, PatientScreening, SkeletonReplay,
                              TrendGraph, TokenEfficiency
    app/lib/api.ts            typed backend client
    app/lib/gait.ts           client-side pose helpers and calibration
  data/
    mock_patients.json  seed patients for the doctor's portal
```

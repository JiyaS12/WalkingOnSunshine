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

The three endpoints that carry the cross-domain handshake:

- **`POST /api/submit-survey`** — Receives structured JSON from the voice agent
  containing patient symptoms and metadata, linking them securely to the
  patient record. Gated by an `X-Survey-Token` header whenever the
  `SURVEY_INGEST_TOKEN` environment variable is set.
- **`POST /api/process-video`** — Accepts multipart video file uploads,
  executes batch MediaPipe pose extraction across frames, and returns computed
  stride and asymmetry telemetry.
- **`POST /api/generate-summary`** — Compiles patient survey responses and
  movement data into a plain-language clinical report using an optimized local
  heuristic token-caching layer.

### Full endpoint reference

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness probe. |
| `POST` | `/api/process-frame` | Joint telemetry (one session's frames) → `GaitMetrics`. |
| `POST` | `/api/process-video` | Multipart `.mp4`/`.mov` upload → `VideoAnalysis`. |
| `POST` | `/api/generate-summary` | Plain-language clinical summary (cached). |
| `GET` | `/api/summary-cache-stats` | Token-cache hit rate and tokens saved. |
| `POST` | `/api/submit-survey` | Voice-agent intake payload → patient record. |
| `GET` | `/api/patients` | Cohort list for the doctor's portal (`?q=` search). |
| `GET` | `/api/patients/{pid}` | One patient: survey + session history. |
| `POST` | `/api/patients/{pid}/sessions` | Attach a gait session to a patient. |
| `POST` | `/api/patients/{pid}/synthesis` | Unified subjective + objective report. |

`/api/generate-summary` and `/api/patients/{pid}/synthesis` call OpenAI
`gpt-4o-mini` when `OPENAI_API_KEY` is set and fall back to a deterministic
template otherwise. Summaries are cached in `backend/.cache/summaries.json`.

## 4. Implementation status

| Capability | Status |
| --- | --- |
| Live webcam mode (client-side MediaPipe Pose) | Implemented |
| Pre-recorded video upload (`.mp4`/`.mov`) | Implemented |
| Auto-calibration to user proportions (leg length) | Implemented — 60-frame calibration feeds `leg_length_m` |
| Lower-extremity biomechanics + fall-risk score | Implemented — `GaitProcessor` |
| Survey ingestion and patient records | Implemented — `/api/submit-survey`, `backend/store.py` |
| Doctor's portal and unified synthesis report | Implemented — `/doctor` |
| Patient client at `/patient/[id]` | Implemented — per-patient screening page backed by `store.get_patient`. |
| Automated voice agent (outbound calls, intake) | **Not implemented** — no telephony integration in the repository. |
| SMS with personalized deep link | **Not implemented** — depends on both the voice agent and the `/patient/[id]` route. |
| Live voice guidance during the walking test | **Not implemented** |

Shipping the voice-agent domain requires a telephony provider (call + SMS
webhooks), a dynamic `/patient/[id]` route that resolves the id server-side,
and a token or signed-link scheme so the texted URL is usable by the patient
without exposing patient data to anyone holding a guessable id.

## Code map

```
gaitguard-ai/
  backend/
    main.py          FastAPI app and route definitions
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

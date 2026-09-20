# GaitGuard AI

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

```
gaitguard-ai/
  backend/
    main.py          FastAPI app and route definitions
    processor.py     GaitProcessor: frames -> GaitMetrics
    video.py         OpenCV + MediaPipe extraction for uploaded video
    store.py         patient records: surveys, sessions, synthesis
    agent.py         clinical summary (OpenAI gpt-4o-mini w/ template fallback)
  frontend/
    app/page.tsx              landing page with patient links
    app/patient/[id]/page.tsx per-patient screening (webcam / upload)
    app/doctor/page.tsx       clinician dashboard (live-syncs)
    app/components/PatientScreening.tsx  patient screening view
  data/
    mock_patients.json  seed patients for the doctor's portal
```

## Backend setup

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
cd backend
.venv/bin/uvicorn main:app --port 8000
```

Optional environment variables:
- `OPENAI_API_KEY` — enable LLM-generated summaries/synthesis
  (deterministic template fallback without it). Summaries are cached in
  `backend/.cache/summaries.json`; `GET /api/summary-cache-stats` reports
  cache stats.
- `SURVEY_INGEST_TOKEN` — when set, `POST /api/submit-survey` requires the
  `X-Survey-Token` header to match.

## Frontend setup

```bash
cd frontend
npm install
cp .env.example .env.local   # sets NEXT_PUBLIC_API_URL (default http://localhost:8000)
npm run dev
```

The app runs at http://localhost:3000 and expects the backend on
`NEXT_PUBLIC_API_URL`.

## Demo flow

1. `cd backend && .venv/bin/uvicorn main:app --port 8000`
2. `cd frontend && npm run dev`
3. Open http://localhost:3000 — pick a patient link, type a patient ID, or
   click **Load Demo Patient RGN-0417**; any unknown ID auto-creates a demo
   profile (pain 3/10, no prior falls) so you can test right away.
4. On `/patient/<id>`: do a **Live Camera** walk or **Upload Video** —
   metrics update, sessions save to the patient's record.
5. Open `/doctor` — the portal live-syncs every 5 s; select the patient to
   see the Unified Clinical Synthesis Report (survey + gait trend +
   skeleton replay) and click **Generate synthesis**.

## Test

```bash
cd backend
.venv/bin/pytest -q
```

## License

MIT — see [LICENSE](LICENSE).

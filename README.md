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

Stride length is `2 x` the mean fore-aft ankle separation at its peaks —
the ankle-to-ankle vector projected onto the walking direction (perpendicular
to the hip line), so step width and any height difference between the feet
stay out of it. It therefore assumes left and right steps are similar; the
left/right difference is reported by `asymmetry_pct` instead. Because
MediaPipe world coordinates are a scale *estimate*, treat `stride_ratio`
(stride / leg length) as the reliable figure and the metre value as
approximate.

A reading only counts as gait when the feet leave the floor **and** the
fore-aft swing is periodic — most of its power sits at stepping rates
(0.3–3 Hz). Amplitude alone cannot decide it: tracker jitter separates the
ankles further than a short shuffling step does, so thresholding on distance
either flags a stationary subject or dismisses a genuinely impaired walker.

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
3. Open http://localhost:3000 — pick a patient link (or type the patient ID).
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

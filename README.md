# GaitGuard AI

AI-assisted gait analysis for clinical mobility monitoring. Phase 1 provides a
Python/FastAPI backend that processes MediaPipe-style joint telemetry
(meters, y vertical with larger y = higher) into clinical gait metrics
(stride length, stance asymmetry, velocity degradation, cadence, and a
weighted-logistic fall risk score), plus a mock cohort dataset and an optional
LLM-generated clinical summary.

## Architecture

```
gaitguard-ai/
  backend/
    main.py          FastAPI app: /api/health, /api/process-frame,
                     /api/get-simulation, /api/generate-summary
    processor.py     GaitProcessor: frames -> GaitMetrics
    simulator.py     mock cohort loading + day-1 vs day-14 comparison
    agent.py         clinical summary (OpenAI gpt-4o-mini w/ template fallback)
  frontend/          Phase 2 — not yet implemented
  data/
    mock_cohort.json synthetic 10s @30fps gait sessions (day_1, day_14)
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

Optional: set `OPENAI_API_KEY` to enable LLM-generated summaries on
`POST /api/generate-summary` (falls back to a deterministic template without
it). Summaries are cached in `backend/.cache/summaries.json` (heuristic
token-saving layer); `GET /api/summary-cache-stats` reports cache stats.

## Frontend setup

```bash
cd frontend
npm install
cp .env.example .env.local   # sets NEXT_PUBLIC_API_URL (default http://localhost:8000)
npm run dev
```

Dashboard runs at http://localhost:3000 and expects the backend on
`NEXT_PUBLIC_API_URL`.

## Demo script for the pitch

1. `cd backend && .venv/bin/uvicorn main:app --port 8000`
2. `cd frontend && npm run dev`
3. Open http://localhost:3000 — the header shows the patient chip and a
   "Backend: connected" status pill.
4. In Simulated Trial Mode, flip Day 1 → Day 14 to show rehab progress on the
   metrics cards and trend chart.
5. Click **Generate Patient Summary** twice — the second call is served from
   the heuristic cache (watch the Token Efficiency panel's "Cache hit" badge
   and tokens-saved counter).

## Test

```bash
cd backend
.venv/bin/pytest -q
```

## License

MIT — see [LICENSE](LICENSE).

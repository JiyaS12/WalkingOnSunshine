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
`POST /api/generate-summary` (falls back to a deterministic template without it).

## Test

```bash
cd backend
.venv/bin/pytest -q
```

## License

MIT — see [LICENSE](LICENSE).

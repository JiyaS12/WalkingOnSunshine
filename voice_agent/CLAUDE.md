# CLAUDE.md

This repository is intentionally staged and should not be treated as a single all-in-one production rewrite.

## Current state

The supported runtime is the safe core under `app/`:

- `app/models.py` defines the patient, question, and answer schema
- `app/patient_repository.py` performs strict patient lookup
- `app/question_loader.py` selects question banks by condition
- `app/survey_engine.py` manages the bounded confirmation flow
- `app/persistence.py` stores call metadata and conversation transcripts in memory, and writes through to Supabase when server credentials are set
- `app/database.py` is the server-side Supabase REST adapter (service-role key never goes to the browser)
- `app/voice_adapter.py` defines the voice interaction contract
- `app/gait_handoff.py` fetches the backend-signed patient magic link for the handoff (SMS delivery stays in the call session)
- `app/telephony/` carries the survey over a real phone call: Twilio transport, Deepgram streaming speech-to-text and speech synthesis, and `PhoneCallSession`, which runs the turn loop without knowing either provider
- `phone_app.py` serves the operator dialer, the Twilio webhooks, and the media-stream websocket

The older `survey_intelligence/` module is retained as reference/demo code only. Do not treat it as the active implementation path.

Optional Postgres/Supabase schema lives under `supabase/migrations/` and is
documented in `DATABASE.md` / `SECURITY.md`. It must stay additive: no table
drops, no replacing in-memory runtime defaults, no real PHI, no raw audio,
and scoring only from confirmed values.

## Safety principles

- Never guess a condition when a patient code is missing
- Accept a clear patient selection of an allowed option directly; require a
  separate confirmation for AI-inferred or relatively adjusted proposals.
- Keep stored questions verbatim. Only short, validated non-clinical bridges
  may be model-written; the engine owns all option-bearing speech and state.
- After bounded clarification retries, leave the question unanswered, flag human review, and continue; explicit stop requests and overall call limits still end the call.
- Keep external integrations behind failure-tolerant wrappers
- Separate demo behavior from production behavior

## Working rules for future changes

1. Keep the runtime layered: patient lookup, survey flow, persistence, voice, handoff.
2. Prefer small, reviewable slices over large monolithic rewrites.
3. Add tests for every new guardrail or state transition.
4. If a provider integration is added, isolate it behind a thin adapter.
5. Do not silently invent patient metadata or branch selection.

## Validation

Use the repo test suite as the baseline:

```bash
. .venv/bin/activate
python -m pytest -q
```

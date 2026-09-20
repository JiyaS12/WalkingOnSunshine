---
name: testing-integrated-demo
description: Run local clinician portal, signed patient-link, and integrated fake-phone browser demos with isolated stores and credentials.
---

# Local clinician and patient-link testing

Use the repository blueprint for dependency installation. Run FastAPI from
`backend/` on 8000 and `npm run dev` from `frontend/` on 3000. The phone service
on 8001 is unnecessary for portal rendering and link issuance.

## Configuration

Generate distinct random process-environment values for
`CLINICIAN_SESSION_SECRET` and `PATIENT_LINK_SIGNING_SECRET` (32+ characters)
and `SURVEY_INGEST_TOKEN` (16+ characters). Set local test
`CLINICIAN_USERNAME` and `CLINICIAN_PASSWORD`.
Set `PATIENT_APP_BASE_URL=http://localhost:3000` and explicitly set
`CLINICIAN_COOKIE_SECURE=false` only for local HTTP. Keep secrets out of
frontend environment variables, source files, console logs, and evidence.
Unset `OPENAI_API_KEY` for deterministic template synthesis when live AI is
outside scope.

## Browser paths

Sign in at `/doctor`; select a patient, expand survey history, and use
Generate synthesis. Verify metrics, trend, summary, and empty states on
desktop and a 390px emulated viewport.

Trace the current portal before assuming it contains a copy-link button.
If testing a backend-only clinician link endpoint, call
`POST /api/patients/{id}/link` using browser fetch with
`credentials: "include"` in the authenticated tab; do not extract cookies
for curl. Repeat with credentials omitted for the unauthorized case.

The service-key endpoint is `POST /api/voice/patient-link`, body
`{"patient_id":"..."}`, header `X-Survey-Token`. Expect no-store and a signed
patient_url; unknown patient is 404, missing/wrong token is 401.

Open the real returned link in incognito. The patient flow currently uses
bearer-scoped access and removes the token from the address bar. Check the
visible patient identity, walking entry, and patient API status. Alter a
signature character and mismatch the path's patient ID to verify denial.
Do not mistake lack of a physical camera for a link-authorization failure;
report capture as untested if it is in scope.

## Cache merge fixture

`backend/.cache/patients.json` is a fixed path. Back it up before preparing
an older-cache fixture, remove only the new seed, and start the backend
afterward. Verify that the new patient appears while the disk cache still
lacks it: the merge is in memory until a mutation. Restore the original
cache afterward. Do not infer that a cohort is a confirmed condition:
the portal may require explicit clinician selection independently.

## Integrated offline voice-to-walking demo

The supported deterministic integration surface is documented in
`docs/integration-runbook.md` and `tests/integration/test_handshake.py`.
Use `tests/integration/main_fixture.py` and `phone_fixture.py` to run the
real HTTP applications with FakePhoneProvider. Do not assume that production
`phone_app.py` exposes fixture routes or has a fake-provider environment flag.

Run main on 8000 and phone on 8001 with isolated `FIXTURE_STORE`,
`FIXTURE_CACHE`, and `PHONE_RECEIPTS_PATH`. The fixture store must be a JSON
object keyed by patient ID, not the seed array, so convert the seed first:

```bash
python -c 'import json,sys; s=json.load(open("data/mock_patients.json")); json.dump({p["patient_id"]: p for p in s}, open(sys.argv[1], "w"))' "$FIXTURE_STORE"
```

Set each subprocess's PYTHONPATH to `tests/integration` plus its own
backend/voice_agent module directory. Launch with uvicorn `main_fixture:app`
or `phone_fixture:app` from the respective directory.

Generate `OPERATOR_TOKEN` and `SURVEY_INGEST_TOKEN` shared by main and phone.
Main uses `PHONE_SERVICE_BASE_URL=http://127.0.0.1:8001`; phone uses
`MAIN_BACKEND_URL=http://127.0.0.1:8000` and
`PUBLIC_BASE_URL=http://127.0.0.1:8001`. Set `SURVEY_EXTRACTOR=exact`.
Keep patient signing and clinician session secrets on main only. Omit
Twilio/Deepgram/Supabase/OpenAI credentials for deterministic offline tests.
Phone controls use bearer OPERATOR_TOKEN; main integration endpoints use
X-Survey-Token. `/api/config` should report integrated and ready.

In /doctor select a patient, explicitly choose and save their condition,
enter an E.164 fixture destination, then Start call. Verify dialing status
and call/attempt identifiers. Obtain active_call_id using the service-key
lookup; do not extract clinician cookies for shell requests.

On phone, POST `/fixture/calls/{call_id}/begin`, then POST utterances as
`{"text":"..."}` to `/fixture/calls/{call_id}/utterance`. The shipped handshake
test documents generic answer/yes pairs and six condition responses.
GET `/fixture/calls/{call_id}` provides captured speech, submission, canonical
URL, and fake-provider counters. Wait for asynchronous narration as well as
SMS status; status can commit before the speech callback has appended text.

For manual browser observation, the fixture's 30-second walking timeout may
be too short. Use an external, temporary wrapper to lengthen its session
timeout; clearly disclose this. Such a wrapper can expose handle_silence
behind the same loopback-only operator authentication to verify reminder limits.
Never add fixture controls to the production server.

Open the captured canonical URL in incognito. A loopback, non-logging redirect
relay avoids printing bearer tokens if clipboard utilities are unavailable.
The page itself sends page_ready and may send camera_unavailable. Read the
current walking last_sequence before injecting any later events, because
browser events already consume sequence numbers. Drive calibration_started,
calibration_completed, capture_started, capture_completed with the matching
call/attempt. Wait for each expected speech output; do not emit all states
too quickly for the phone poller.

For camera-free save verification, use backend/tests/gait_gen.py frames and
the actual /api/process-frame API, then post accepted metrics through the
patient-scoped sessions endpoint. Label the session clearly as synthetic
and associate the call/attempt. A captured event alone must not produce saved
or a goodbye. Verify session persistence, saved status, captured final speech,
and portal reconciliation/event history. Patient metric cards may remain
empty when a separate test client saves because no browser capture occurred;
do not claim camera or browser Save-this-walk coverage from an API save.

Report fake telephony, injected transcripts/events, and synthetic frames
separately from real services, browser access, storage, and reconciliation.
Captured speech strings do not prove STT/TTS audio quality or real SMS delivery.

## Devin Secrets Needed

None for local portal and patient-link issuance tests: generate local
ephemeral credentials. `OPENAI_API_KEY` is optional only for live AI tests.
Phone-provider secrets are not needed for the fake-provider integration flow.

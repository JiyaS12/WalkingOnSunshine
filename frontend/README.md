# Sana frontend

This App Router frontend requires Node.js 20.19.x and npm 10 or newer.
The root `.nvmrc` selects the supported Node 20 release.

## Getting started

Install the locked dependencies and run the development server:

```bash
npm ci
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in a browser. Copy
`.env.example` to `.env.local` only when the API is not available at its
default `http://localhost:8000` URL.

The home page is informational and does not expose patient lookup or demo
creation. Patient screening requires the complete expiring URL returned by
`POST /api/submit-survey`; a bare `/patient/[id]` route cannot load a record.
See the root README for the local-only signed-link demo procedure.

## Voice follow-up and patient walking

Run the frontend on 3000 and the main FastAPI backend on 8000. The phone
service runs separately on 8001; browsers only call the main backend.
Keep backend and phone-service Python environments separate (their OpenAI
dependency versions conflict). Never put operator, Twilio, Deepgram, or
other provider credentials in a `NEXT_PUBLIC_*` variable.

After signing in at `/doctor`, select a patient, explicitly select their
confirmed orthopedic or stroke condition when missing, and enter an E.164
destination number. A selection is never inferred from age or complaints.
“Save condition” persists metadata independently; “Start call” also supplies
the clinician's selection. The input clears immediately on submission. Its
request body exists only in component memory while the result is ambiguous,
so “Retry same call request” uses the same request ID and destination. It is
cleared when a receipt arrives or the patient component unmounts.

Call status is polled every five seconds. `unknown` means unresolved: use
“Reconcile call status,” which reads phone status without redialing.
Only the latest call with a stored survey, definitively failed SMS, and
fewer than five SMS attempts offers a new SMS retry. Ambiguous retry receipts
use the same request ID until polling or the response confirms acceptance.
Failed calls may be followed by an explicit new call; no calls or SMS are
automatically retried. Auth expiry clears protected portal data.

The portal shows generic intake independently from confirmed condition
answers, instrument/version, call and survey IDs, timestamped walking
events, correlated gait sessions, and the existing synthesis controls.
Unknown pain, falls, injury, and dizziness stay unknown; Likert responses
are never converted into intake values or validated HOOS JR interval scores.

The patient opens the canonical signed URL sent by the phone service.
The browser does not build or copy unsigned patient URLs. The token is
captured in memory and removed from the address bar/history state, never
stored in browser storage, and sent only as a bearer header to patient
access endpoints. Reloading a scrubbed URL requires reopening the original
signed link. There are no clinician links or call/survey records on the
patient screen.

For live capture, stand with the full body visible for 60 usable calibration
frames, then walk across the frame only if safe. Choose “Save this walk”
after walking is detected. Upload mode analyzes and saves a valid walking
video automatically; it does not wait for camera permission or model startup.
“Pause camera” is reversible; “Stop assessment” is an explicit terminal
request and prevents further capture while confirmation is pending.

### Browser API contract

All clinician operations use `credentials: "include"` with the existing
HttpOnly clinician session and browser Origin validation. Patient access
uses `credentials: "omit"`, `Authorization: Bearer <signed-token>`,
`cache: "no-store"`, and `referrerPolicy: "no-referrer"`.

| Operation | Endpoint | Request / response fields used |
| --- | --- | --- |
| Condition | `PATCH /api/patients/{pid}/condition` | `{condition_category: "orthopedic" \| "stroke"}`; returns patient ID, condition and clinician source |
| Start call | `POST /api/patients/{pid}/calls` | `{request_id, to_number, condition_category}`; returns `{call, replayed}` |
| Poll calls | `GET /api/patients/{pid}/calls` | `{calls}`; merge by `call_id` and monotonic `version` |
| Reconcile | `POST /api/patients/{pid}/calls/{call_id}/refresh` | `{call, phone_available}` |
| Retry failed SMS | `POST /api/patients/{pid}/calls/{call_id}/sms-retries` | `{request_id}`; returns `{call, replayed}` |
| Patient record | `GET /api/patient-access/{pid}` | Scoped patient record and existing gait sessions |
| Walking context | `GET /api/patient-access/{pid}/walking` | `{walking: null \| {call_id, attempt_id, version, survey_status, status, last_sequence, last_event, session_id}}` |
| Walking event | `POST /api/patient-access/{pid}/walking/events` | `{event_id, call_id, attempt_id, sequence, event, error_code?}`; returns authoritative `{walking}` |
| Save walk | `POST /api/patient-access/{pid}/sessions` | Existing `{label, source, idempotency_key, metrics, frames}` plus `call_id` and `attempt_id` when a call exists; returns scoped patient record with saved `session_id` |

`CallRecord` also consumes `patient_id`, `request_id`, `condition_category`,
`call_status`, `survey_status`, `survey_id`, `sms_status`, `sms_attempt`,
`sms_retries`, `error_code`, creation/update timestamps, and
`walking.{status,last_sequence,last_event,session_id,events}`.
The generic survey remains `pain_scale`, `fall_history`, `dizziness`,
`dizziness_notes`, and `primary_complaints`. `condition_survey` separately
contains `instrument`, `version`, `condition_category`, and individually
confirmed answers with `question_id`, `normalized_value`,
`acceptance_method`, and optional `confirmed_at`.

Lifecycle events use UUIDs and increasing sequences, serialized through one
queue; retries preserve the exact event body. `page_ready` means the page
opened (or the patient requested a camera retry), **not camera readiness**.
Actual usable pose frames trigger `calibration_started`; 60 calibration
frames trigger `calibration_completed` (backend state `ready`) followed by
`capture_started`. Valid live analysis or upload results trigger
`capture_completed`, which is **not** a saved walk. Upload starts at
`capture_started` without simulated calibration. Permission denial and
recoverable errors are emitted by the actual camera/upload callbacks.

Only an authoritative walking response or a session response containing the
matching idempotency key, call ID, attempt ID, and session ID confirms `saved`.
Save retries preserve the complete request, including the original label.
Walking context is polled every five seconds and rechecked before saves.
A changed call/attempt blocks old readings and requires “Load current
assessment.” Aborts, generation guards, and reporter identity checks prevent
old routes, camera callbacks, and save responses from updating a new scope.
Expired tokens show a non-leaking invalid-link screen.

Integration/read/save requests have a 15-second deadline. When walking
status is unavailable, independent analysis and legacy (no-call) saving stay
available with a warning. The backend still rejects uncorrelated writes to
an active call; the browser never silently associates a legacy reading with
a newly discovered attempt. Progress events are bounded to 180 per loaded
page (plus an explicit stop), with server limits also enforced.

### Verification boundaries

Vitest covers authenticated call controls, ambiguous/replayed requests,
SMS eligibility, separated survey data, actual component lifecycle callback
wiring with fake camera/model providers, patient event/session contracts,
auth expiry, cancellation, and stale route/attempt/error responses.
The standard commands below require no provider secrets, real calls, SMS,
paid LLM requests, browser automation, or running services. Real camera
hardware, phone delivery, and cross-service end-to-end behavior require
separate integration validation.

## Verification

```bash
npm ci
npm audit --omit=dev --audit-level=high
npm test
npm run lint
npm run type-check
npm run build
```

See the repository README for the Python 3.12 backend setup and complete
clean-checkout verification sequence.

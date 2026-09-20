# Patient access contract

This is the backend contract consumed by the patient UI and integrated
voice/SMS workflow. Patient-link tokens authorize exactly one patient,
expire after a configured lifetime, and are never valid as clinician
credentials.

## Configuration

The backend requires `PATIENT_LINK_SIGNING_SECRET` before survey ingestion can
issue a link. Use at least 32 bytes of cryptographically random data; for
example, generate a deployment secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

| Variable | Required/default | Behavior |
| --- | --- | --- |
| `PATIENT_LINK_SIGNING_SECRET` | Required, minimum 32 bytes | HMAC-SHA256 signing key. The backend fails closed with `503` when it is absent or too short. Rotate it to invalidate every outstanding patient link. |
| `PATIENT_APP_BASE_URL` | `http://localhost:3000` | Public base URL used in `patient_url`. Non-local deployments must use HTTPS. Queries and fragments are rejected. |
| `PATIENT_LINK_TTL_SECONDS` | `900` | Link lifetime in seconds; accepted range is 60–604800 (one minute to seven days). |
| `SURVEY_INGEST_TOKEN` | Required by default | Callers must send it in `X-Survey-Token` so only the voice intake service can create links. Missing configuration fails closed with `503`. |
| `ALLOW_UNAUTHENTICATED_SURVEY_INGEST` | Disabled | Local-only escape hatch for running intake without a token. Never enable it in a shared or production environment. |

Provide secrets through the deployment secret manager, not `.env` files in
source control. Tokens are credentials: redact full patient URLs and
`Authorization` headers from application, proxy, analytics, and support logs.

## 1. Complete intake and receive a link

```http
POST /api/submit-survey HTTP/1.1
Content-Type: application/json
X-Survey-Token: <configured-ingest-token>

{
  "patient_id": "RGN-0417",
  "patient_name": "Demo Patient",
  "pain_scale": 4,
  "fall_history": {
    "falls_last_6_months": 0,
    "injured": false
  },
  "dizziness": false,
  "primary_complaints": ["reduced walking confidence"],
  "call_id": "call-123"
}
```

```json
{
  "status": "stored",
  "patient": {
    "patient_id": "RGN-0417",
    "name": "Demo Patient",
    "age": null,
    "cohort": null,
    "surveys": [
      {
        "patient_id": "RGN-0417",
        "patient_name": "Demo Patient",
        "pain_scale": 4,
        "fall_history": {
          "falls_last_6_months": 0,
          "injured": false,
          "last_fall_description": null
        },
        "dizziness": false,
        "dizziness_notes": null,
        "primary_complaints": ["reduced walking confidence"],
        "call_id": "call-123",
        "recorded_at": "2026-09-19T17:00:00+00:00"
      }
    ],
    "gait_sessions": []
  },
  "patient_url": "https://patient.example/patient/RGN-0417?token=<signed-token>",
  "patient_access_expires_at": "2026-09-19T17:15:00+00:00"
}
```

The voice/SMS caller sends `patient_url` as returned. It must not reconstruct,
decode, durably store, or log the token. Repeating identical survey ingestion
can issue a fresh link while retaining the stored survey ID. A conflicting
payload for the same call returns `409`. The integrated phone service freezes
its confirmed payload and durably reserves SMS before dispatch; repeating
ingestion never implies another SMS. Responses
that contain the link or patient-scoped data include `Cache-Control: no-store`.

## 1a. Re-issue a link for an existing patient (voice agent)

The phone survey runs in a separate process that must not hold the signing
secret. After the last answer it asks for the link with the same ingest token:

```http
POST /api/voice/patient-link HTTP/1.1
Content-Type: application/json
X-Survey-Token: <configured-ingest-token>

{ "patient_id": "RGN-0417", "call_id": "CAxxxxxxxx" }
```

```json
{
  "patient_id": "RGN-0417",
  "patient_url": "https://patient.example/patient/RGN-0417?token=<signed-token>",
  "patient_access_expires_at": "2026-09-19T17:15:00+00:00"
}
```

`404` when the patient record does not exist (the endpoint never creates
records), `401` for a missing or wrong ingest token, `503` when signing is not
configured. The caller texts `patient_url` verbatim and otherwise treats it like
the `submit-survey` link above.

A signed-in clinician can mint the same link from the doctor portal ("Copy
patient link") via `POST /api/patients/{pid}/link`, authenticated by the
clinician session cookie instead of the ingest token; the response shape and
error codes are identical.

## 1b. Optional patient-session exchange

The backend also supports exchanging a signed link for a short-lived HttpOnly
`sana_patient_session` cookie. The integrated patient UI uses the in-memory
bearer flow in section 2; it does not call this exchange or rely on cookies.
The cookie is itself a signed token bound to the same patient id
(`PATIENT_SESSION_TTL_SECONDS`, default 4 hours; `Secure` follows
`CLINICIAN_COOKIE_SECURE`), so it can only read and write that patient.

```http
GET /api/auth/verify?patient_id=RGN-0417&token=<signed-token> HTTP/1.1
```

```json
{ "authenticated": true, "patient_id": "RGN-0417", "expires_at": 1780000000 }
```

`401` for a bad/expired/mismatched token, `404` when the patient record does
not exist, `503` when signing is not configured. `DELETE /api/auth/verify`
clears the cookie. Non-`GET` requests authenticated by the cookie must carry an
allow-listed browser `Origin`; the bearer form below still works unchanged.

## 2. Read the patient walking-test view

The integrated patient UI sends the link token as a bearer credential and
omits cookies from its patient-scoped requests. It must not put the token into
subsequent API query strings or browser storage. The API additionally accepts
the optional patient session described above.

The implemented client captures the token in component memory and immediately
removes it from the visible URL while preserving unrelated query parameters.
The patient page uses a `no-referrer` policy, does not log the credential, and
does not use local or session storage. Reloading the scrubbed URL therefore
requires reopening the original link (or requesting a new one).

```http
GET /api/patient-access/RGN-0417 HTTP/1.1
Authorization: Bearer <signed-token>
```

```json
{
  "patient_id": "RGN-0417",
  "name": "Demo Patient",
  "gait_sessions": []
}
```

The patient view deliberately omits surveys, cohort metadata, and the cohort
list. A token for `RGN-0417` cannot read any other path id.

## 3. Save a patient session

```http
POST /api/patient-access/RGN-0417/sessions HTTP/1.1
Authorization: Bearer <signed-token>
Content-Type: application/json

{
  "label": "Walking test",
  "source": "live",
  "idempotency_key": "live_a24f2f4ba26c41e8b3108d57fdd03162_1",
  "metrics": {
    "stride_length_m": 1.0,
    "asymmetry_pct": 5.0,
    "velocity_degradation_pct": 2.0,
    "fall_risk_score": 0.4,
    "cadence_steps_per_min": 100.0,
    "frame_count": 100,
    "leg_length_m": 0.9,
    "stride_ratio": 1.1,
    "knee_flexion_rom_deg": 40.0,
    "peak_ankle_speed_mps": 3.0,
    "gait_detected": true,
    "dropped_frame_pct": 0.0
  },
  "frames": null,
  "recorded_at": "2026-09-19T17:05:00Z"
}
```

The response includes the authenticated read fields, updated `gait_sessions`,
and the persisted `session_id`. This route shares the existing implementation:
Pydantic metric validation, the 300-frame request cap, landmark validation,
50-session record cap, timestamp ordering, and persistence rollback all remain
in force. `idempotency_key` is a non-secret, client-generated identifier that
must remain stable when retrying the same analyzed walk. Repeating a write with
the same key returns the existing record without appending another session.

The client permits saves only when `gait_detected` is true. Upload analyses are
auto-saved once per analysis, live saves use an in-flight/completed guard, and
failed saves remain available for an explicit retry. Changing the patient path
or credential aborts active reads, video work, and session writes; responses
from the previous route are ignored.

## Authorization failures

Missing, malformed, tampered, expired, and wrong-patient credentials all
receive the same response so callers cannot use error details as an oracle:

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer
Content-Type: application/json

{"detail":"invalid or expired patient access"}
```

The existing `/api/patients*` routes are a separate clinician surface. Patient
tokens are not read or accepted by those routes.

The patient UI presents the same invalid-link state for expired, malformed,
wrong-patient, and record-not-found responses. It does not fall back to the
clinician surface or reveal which condition occurred.

## 4. Integrated call and survey contract

The examples above also support legacy/local survey ingestion. Integrated calls
must use the reserved main patient ID and `call_id`; no phone-demo patient IDs
are substituted. Main owns the patient JSON store and all signed URLs.

### Authentication and endpoints

| Caller | Authentication | Operations |
| --- | --- | --- |
| Clinician → main | HttpOnly cookie from `POST /api/clinician/session` | `PATCH /api/patients/{pid}/condition`; `GET/POST /api/patients/{pid}/calls`; `GET .../calls/{call_id}`; `POST .../calls/{call_id}/refresh`; `POST .../calls/{call_id}/sms-retries`; patient detail and synthesis |
| Main → phone (`8001`) | `Authorization: Bearer OPERATOR_TOKEN` | `POST /api/calls`, `GET /api/calls/{call_id}`, `POST /api/calls/{call_id}/sms-retries` |
| Phone → main (`8000`) | `X-Survey-Token: SURVEY_INGEST_TOKEN` | `POST /api/submit-survey`; `GET /api/integration/patients/{pid}`; `GET /api/integration/patients/{pid}/calls/{call_id}`; `POST .../status`; `POST .../patient-link`; `GET .../walking` |
| Patient → main | Signed patient bearer | Patient read, walking context/events, session write |
| Twilio → phone | Twilio signature and matching provider scope | Call/SMS callbacks, TwiML; media websocket additionally requires a one-use stream ticket |

Condition metadata is `{"condition_category":"orthopedic"}` or `"stroke"`.
Main records `condition_source:"clinician"` and freezes the condition in the
call. Starting without explicit patient/call condition returns `409`; age,
cohort and complaints are never used to select an instrument.

Clinician call creation:

```json
{
  "request_id": "client-generated-stable-id",
  "to_number": "+15555550123",
  "condition_category": "orthopedic"
}
```

The response is `{"call": CallRecord}`. Main allocates `call_id` and
`attempt_id`, sets `active_call_id`, persists its reservation before dispatch,
and sends phone `{patient_id, call_id, attempt_id, request_id, to_number, condition_category}`.
Repeating the same request returns its call; changing a reused request or
starting another unresolved call returns `409`. Numbers are not public call
fields or durable phone receipts.

Integrated survey fields:

```json
{
  "submission_kind": "integrated",
  "patient_id": "RGN-0417",
  "call_id": "<reserved-call-id>",
  "pain_scale": 7,
  "fall_history": {
    "falls_last_6_months": 2,
    "injured": true,
    "last_fall_description": "Bruised knee"
  },
  "dizziness": true,
  "dizziness_notes": "On standing",
  "primary_complaints": ["Hip soreness"],
  "condition_survey": {
    "condition_category": "orthopedic",
    "instrument": "hoos_jr",
    "version": "1",
    "answers": [
      {
        "question_id": "hoos_stairs",
        "normalized_value": "mild",
        "confirmed": true,
        "acceptance_method": "explicit_selection"
      }
    ]
  }
}
```

The abbreviated answer array above must contain **all six** distinct IDs:

| Category/instrument | IDs |
| --- | --- |
| `orthopedic` / `hoos_jr` | `hoos_stairs`, `hoos_uneven_surface`, `hoos_rising`, `hoos_bending`, `hoos_lying_bed`, `hoos_sitting` |
| `stroke` / `stroke_mobility` | `stroke_balance`, `stroke_weakness`, `stroke_stairs`, `stroke_turning`, `stroke_walking`, `stroke_recovery` |

Values: `none`, `mild`, `moderate`, `severe`, `extreme`.
`acceptance_method` is `explicit_selection` or `confirmation`; every answer
must be confirmed. Questions remain verbatim in the phone engine's approved
bank. Missing, duplicate, unconfirmed or wrong-instrument answers fail `422`.
The prototype raw item sum is not a validated HOOS JR interval or stroke score.

Generic intake is collected and confirmed independently: numeric pain 1–10,
nonnegative fall count, injury and dizziness booleans, bounded descriptions and
complaints. Unknown/refused values are independently `null`; they never become
zeros or false from Likert answers. Text limits are 500 characters for notes,
ten complaints with 120 characters each. The condition section never fills
generic facts. Main requires a complete integrated condition section and
rejects conflicting replay with `409`.

Stored response adds `survey` (with stable `survey_id`) to the legacy response
shown above. The associated call gains `survey_status:"stored"` and `survey_id`.
Phone sends the exact response URL inside its fixed SMS message.

### Status and retry semantics

`CallRecord` contains `patient_id`, `call_id`, `request_id`, `attempt_id`,
`condition_category`, `created_at`, `updated_at`, `version`, `phone_version`,
`call_status`, `survey_status`, `survey_id`, `sms_status`, `sms_attempt`,
provider IDs, bounded `error_code`, and `walking`.

Phone snapshots contain `patient_id`, `call_id`, monotonic `version`,
`call_status`, `survey_status`, `sms_status`, `sms_attempt`, `provider_call_id`,
`phone_session_id`, `message_id`, and `error_code`. Older callbacks cannot
regress terminal state; equal-version conflicting snapshots fail `409`.

| State | Values |
| --- | --- |
| Call | `dispatching`, `unknown`, `dialing`, `in_progress`, `completed`, `failed`, `stopped` |
| Survey | `pending`, `in_progress`, `stored`, `stopped`, `needs_review` |
| SMS | `not_requested`, `sending`, `unknown`, `sent`, `delivered`, `failed` |

Call completion is separate from walking success. For example, a call may
complete after SMS failure with no saved walk. Definitive pre-dispatch
unavailability/provider rejection can fail safely. Timeout or cancellation
after provider dispatch is ambiguous: preserve `unknown`, do not auto-redial
or resend. GET/refresh reconciles snapshots.

SMS retry is clinician-initiated with `{"request_id":"stable-retry-id"}`.
Main only reserves another SMS attempt after definitive failure and a stored
survey, generates the new canonical link, and sends phone
`{patient_id, call_id, attempt_id, request_id, sms_attempt, patient_url,
patient_access_expires_at}`. Replay of that reservation
does not resend. `unknown`, `sending`, or successful SMS states reject retry
with `409`. A provider 2xx/queued result is not proof of delivery.

## 5. Walking correlation and completion

`GET /api/patient-access/{pid}/walking` returns `{"walking": WalkingView|null}`.
The active view contains `call_id`, `attempt_id`, `version`, `survey_status`,
`status`, `last_sequence`, `last_event`, and `session_id`.
Statuses are `waiting`, `page_ready`, `calibrating`, `ready`, `capturing`,
`captured`, `saved`, and `stopped`. Error events update `last_event` without
inventing progress; their bounded codes are retained in the clinician event log.

```json
{
  "event_id": "stable-event-uuid",
  "call_id": "<call-id>",
  "attempt_id": "<attempt-id>",
  "sequence": 1,
  "event": "page_ready"
}
```

Post this to `/api/patient-access/{pid}/walking/events`. Allowed events:
`page_ready`, `permission_denied`, `calibration_started`,
`calibration_completed`, `capture_started`, `capture_completed`,
`recoverable_error`, `stopped`. Matching event-ID replay is idempotent;
conflicting replay is `409`; lower sequence events cannot regress state.
There are at most 200 stored events per attempt. Errors use allowlisted codes,
not raw browser messages.

Add `call_id` and `attempt_id` from that view to the session payload in section
3. With an active call, main rejects missing or stale correlation, absent stored
survey, `gait_detected:false`, or a stopped/already-saved attempt. Stable
idempotent replay returns the same `session_id`; changed correlated content is
`409`. Tokens are patient-scoped, while the supplied IDs enforce attempt scope;
an older link can read the patient's current context but cannot write an old
attempt. Reopening a scrubbed page requires the original unexpired URL.

The frontend serializes events and stops sending on auth/scope conflict. Live
capture starts after calibration; the patient chooses **Save this walk**.
Successful uploads auto-save. Main atomically persists the session and changes
walking to `saved` with `session_id`; phone waits for both. No event can claim
`saved` on the patient's behalf. Doctor detail exposes both survey sections,
call/SMS state, event timeline and saved session. Synthesis for an active call
returns `422` until that call's survey and matching persisted attempt exist,
then uses those records and any preceding gait history.

See [the operator runbook](integration-runbook.md) for expiry, restart,
provider failure and offline-test procedures.

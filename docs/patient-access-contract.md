# Patient access contract

This is the stable backend contract consumed by the patient UI (#23) and the
voice/SMS workflow (#27). Patient-link tokens authorize exactly one patient,
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
decode, store, or log the token. Repeating survey ingestion intentionally
issues a new link; SMS idempotency remains the responsibility of #27. Responses
that contain the link or patient-scoped data include `Cache-Control: no-store`.

## 1b. Exchange the link token for a patient session (magic link)

The patient UI takes `token` from its initial page URL
(`/patient/{pid}?token=…`) and exchanges it once for a short-lived HttpOnly
`sana_patient_session` cookie, then removes the token from the address bar.
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

The patient UI sends either the session cookie or the link token as a bearer
credential. It must not put the token into subsequent API query strings or
browser storage.

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

The response has the same shape as the authenticated read and includes the
updated `gait_sessions`. This route shares the existing session implementation:
Pydantic metric validation, the 300-frame request cap, landmark validation,
50-session record cap, timestamp ordering, and persistence rollback all remain
in force.

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

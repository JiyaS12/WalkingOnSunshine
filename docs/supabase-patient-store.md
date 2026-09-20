# Personalized walking links and Supabase

## What happens

1. In the clinician portal, select the patient and condition and enter the
   destination phone number. Main saves a call reservation before dialing.
2. After the confirmed survey is saved, main issues an expiring signed URL for
   that patient. Phone sends that exact URL by SMS to the dialed number.
3. The link opens `/patient/<patient_id>`: a simple camera/upload screen without
   scores, trend charts or a skeleton overlay. Opening it reports `page_ready`.
4. Progress events and the saved walking analysis carry the patient, call and
   attempt IDs. A completed capture alone does not count as a saved result.
   Uploads save automatically; live capture offers **Save this walk**.
5. The survey, call, progress and saved gait result live together in the
   Supabase patient record. The private call destination identifies which
   number received the link; patient ID remains the permanent identity.

The URL is a bearer credential, not proof that the person holding it owns the
phone. Forwarding it grants patient-scoped access until expiry. The client
removes the token from the address bar; never log or publish full links.
The database stores analysis metrics and bounded derived frame data, not raw
video or audio recordings. The upload is processed to obtain those results.

## Enable in HackMIT2026

1. Open **HackMIT2026 → SQL Editor → New query** in Supabase.
2. Paste and run all of
   [`20260920080000_integrated_patient_records.sql`](../supabase/migrations/20260920080000_integrated_patient_records.sql).
   It creates a separate table, function and review view; it does not delete
   records or change the existing standalone survey tables.
3. Configure these values in the ignored **backend/.env**, never in frontend
   variables or a tracked example file:

   ```dotenv
   PATIENT_STORE=supabase
   SUPABASE_URL=https://YOUR_PROJECT.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=YOUR_SERVER_ONLY_KEY
   SUPABASE_SEED_DEMO=false
   ```

The new table starts empty. Existing local JSON data and standalone voice-demo
tables are **not** automatically imported. To deliberately add the repository's
synthetic demonstration patients, set `SUPABASE_SEED_DEMO=true` for one backend
startup, then set it back to `false`. Existing database patient IDs win over
seeds and are not overwritten. Do not use real patient data for this prototype.

Restart the backend only when ready to run the program. Public HTTPS URLs for
the phone callbacks, patient frontend and API are still required for a real
phone test. Installing this migration does not start services, send messages
or place calls.

## Where to see results

- **Doctor portal → Database**: sign in at `/doctor` and select **Database**.
  No separate Supabase login is needed. Search by patient name, ID, phone number
  or call ID; select a patient to inspect calls, survey answers and saved walking
  measurements. Walking results are joined to both call ID and attempt ID.
  The tab refreshes every 10 seconds while visible and has a manual Refresh button.
  Its badge identifies Supabase or explicitly configured local JSON storage.
  **Record JSON** is a sanitized projection, not a raw database export.
- **walking_patient_records**: one authoritative JSON aggregate per patient,
  with revision and updated time. Calls, surveys and gait sessions are inside
  `record`. The phone is `record.calls[...]._destination_phone`.
- **walking_assessment_results**: a review view associating `patient_id`,
  `call_id`, `attempt_id`, `destination_phone`, survey and saved gait result.
  A null gait result means no matching saved walk yet, not a score of zero.
- The authenticated clinician portal reads the same main patient store.
  The separate voice-demo `/results` dashboard still displays its original
  standalone survey tables, not this integrated dataset.

Both database objects are server/service-role only. Row-level security is
enabled; anonymous and ordinary authenticated database roles cannot read them.
Patient API access remains signed and patient-scoped. Private phone destinations
are stripped from normal API responses and are never placed in link URLs.
The dedicated read-only `/api/clinician/database` endpoint exposes destination
phone numbers only after clinician authentication. It omits link tokens, provider
snapshots, fingerprints and frame arrays. Responses are marked `no-store` and the
UI clears loaded records on authentication failure or sign-out. There are no SQL,
edit or delete controls, and this feature requires no additional migration.

## Failure behavior and limits

Every write uses a transactional database function with expected revisions.
Stale writes fail instead of overwriting a newer walk; a failed batch rolls
back. Supabase outages or missing schema cause a visible failure, never a
silent switch to a local JSON file. After an ambiguous write failure, the
backend discards its cache and reloads the authoritative state on the next
request before retrying.

Run one backend worker: reads use a process-local cache. After manual database
edits, restart the backend to reload it. This is a prototype persistence layer,
not a claim of HIPAA compliance or readiness for real clinical data.
The Database tab is an exception to cached reads: it takes a fresh, read-only
Supabase snapshot without replacing the active-call cache. Search and pagination
currently happen after loading aggregates on the server; large deployments need
database-side querying rather than this demo-scale viewer.

## Offline verification

`backend/tests/test_supabase_store.py` uses an isolated mocked Supabase REST
transport to test persistence, call/phone association, survey-to-walk joins,
reloads, conflicting writes, failure handling and private API projections.
`voice_agent/tests/test_main_integration.py` checks that the exact main-issued
URL goes to the same destination as the call using a fake phone provider.
Frontend tests check that patient capture hides analysis without suppressing
the saved metrics. These tests do not prove real SMS delivery or a deployed
database connection.

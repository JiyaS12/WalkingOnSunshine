# Security assumptions

This is a **hackathon prototype** for synthetic/demo patient-reported
outcomes. It is **not HIPAA compliant** and must not be used with real PHI.

## Data that is allowed here

- Synthetic display codes such as `RGN-0417`
- Demo survey text and confirmed/proposed answers created for the prototype
- Call metadata without recordings

## Data that must not be added

- Real names, medical record numbers, dates of birth, or real phone numbers
- Raw audio or recording URLs
- Real clinical diagnoses, treatment recommendations, or “the doctor has
  been notified” claims
- Service-role or database credentials in frontend code

`patients.is_synthetic` is constrained to `true` so non-demo rows cannot be
inserted without a later, explicit migration.

## Secrets stay server-side

| Key | Where it may live | Where it must not live |
| --- | --- | --- |
| `SUPABASE_SERVICE_ROLE_KEY` | FastAPI / server env only | Browser, `voice_web/`, `NEXT_PUBLIC_*` |
| `DATABASE_URL` | Server env only | Frontend |
| `SUPABASE_ANON_KEY` | Frontend only if RLS is sufficient for that client | Must never be treated as a privileged key |
| `DEEPGRAM_API_KEY` / `OPENAI_API_KEY` | Already server-side in `voice_app.py` | Browser |

The service-role key **bypasses Row Level Security**. Exposing it is equivalent
to giving every caller full table access.

Placeholders are in `.env.example`. Do not commit `.env`.

## How RLS works in this prototype

RLS is enabled on all survey tables.

| Role | Access |
| --- | --- |
| `anon` | No table grants and no policies → cannot read clinical-style rows |
| `authenticated` | `SELECT` only on tables and dashboard/audit views |
| `service_role` | Full access, for a future server-side backend |

There is **no `organization_id`** in the existing app, so this change does
not invent a clinic-tenancy model. `USING (true)` for authenticated reads is a
demo shortcut: any signed-in app user can see all synthetic rows.

Views `clinician_dashboard` and `survey_response_audit` are
`security_invoker = true`, so they do not bypass base-table RLS.

Scoring functions that **write** (`refresh_survey_instance_score`) are
revoked from `PUBLIC` and granted to `service_role` only when that role
exists.

## Future tenancy path

When clinic/org tenancy is needed:

1. Add an `organizations` table (or reuse one if it exists by then).
2. Add `organization_id` to `patients` and `survey_instances`.
3. Replace authenticated `USING (true)` with a claim comparison such as
   `organization_id = (auth.jwt() ->> 'organization_id')::uuid`.
4. Keep anon denied.

Do not introduce a second, competing tenancy scheme if `organization_id`
already exists elsewhere in the repo.

## Production controls still required later

This prototype does **not** provide:

- HIPAA / BAA coverage, audit logging to an immutable store, or PHI minimization review
- Per-clinic isolation, break-glass access, or clinician authz beyond “logged in”
- Encryption key management beyond whatever the host already offers
- Retention, deletion, or export workflows
- Telephony consent, STIR/SHAKEN, or call recording bans enforced in a carrier
- Rate limiting, WAF, or secret rotation
- Independent validation of HOOS JR as used here
- Notification of a real clinician when `needs_human_review` or review flags fire

Human review in the current voice runtime is a local session flag only.

## Runtime reminder

The live desktop demo still stores sessions **in memory**. Adding SQL tables
does not automatically persist voice calls. Wire persistence later behind
the existing `app/persistence.py` boundary, using the service role on the
server, without storing audio.

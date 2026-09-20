-- Additive: does not alter the existing standalone voice survey tables.
-- Run in HackMIT2026's SQL editor. Only the server service role can access it.
begin;
create table if not exists public.walking_patient_records (
  patient_id text primary key,
  record jsonb not null,
  revision bigint not null default 1 check (revision > 0),
  updated_at timestamptz not null default now(),
  constraint walking_record_identity check (
    jsonb_typeof(record) = 'object'
    and record ? 'patient_id'
    and record ->> 'patient_id' = patient_id
  )
);

alter table public.walking_patient_records enable row level security;
revoke all on public.walking_patient_records from public, anon, authenticated;
grant select, insert, update on public.walking_patient_records to service_role;

-- Each request commits all changed patient aggregates atomically. A stale
-- revision rejects the entire write, instead of replacing another saved walk.
create or replace function public.commit_walking_patient_records(changes jsonb)
returns table(patient_id text, revision bigint)
language plpgsql security invoker set search_path = '' as $$
declare
  item jsonb;
  target_id text;
  expected bigint;
  saved_revision bigint;
begin
  if changes is null or jsonb_typeof(changes) <> 'array' or jsonb_array_length(changes) > 1000 then
    raise exception 'invalid change batch' using errcode = '22023';
  end if;
  for item in select value from jsonb_array_elements(changes) order by value ->> 'patient_id' loop
    target_id := item ->> 'patient_id';
    expected := (item ->> 'expected_revision')::bigint;
    if target_id is null or target_id = '' or expected is null or expected < 0
       or jsonb_typeof(item -> 'record') is distinct from 'object'
       or (item -> 'record' ->> 'patient_id') is distinct from target_id then
      raise exception 'invalid patient change' using errcode = '22023';
    end if;
    if expected = 0 then
      insert into public.walking_patient_records as p (patient_id, record)
        values (target_id, item -> 'record')
        on conflict do nothing returning p.revision into saved_revision;
    else
      update public.walking_patient_records as p
        set record = item -> 'record', revision = p.revision + 1, updated_at = now()
        where p.patient_id = target_id and p.revision = expected
        returning p.revision into saved_revision;
    end if;
    if not found then
      raise exception 'patient record changed; reload before retrying' using errcode = '40001';
    end if;
    patient_id := target_id;
    revision := saved_revision;
    return next;
  end loop;
end;
$$;
revoke all on function public.commit_walking_patient_records(jsonb) from public, anon, authenticated;
grant execute on function public.commit_walking_patient_records(jsonb) to service_role;

-- One review row per call: the private destination number, confirmed survey,
-- and saved walk share patient_id + call_id; walks also match attempt_id.
create or replace view public.walking_assessment_results
with (security_invoker = true) as
select p.patient_id,
       c.value ->> 'call_id' as call_id,
       c.value ->> 'attempt_id' as attempt_id,
       c.value ->> '_destination_phone' as destination_phone,
       c.value ->> 'survey_status' as survey_status,
       c.value ->> 'sms_status' as sms_status,
       c.value -> 'walking' as walking,
       s.value as survey,
       g.value as gait_session
from public.walking_patient_records p
cross join lateral jsonb_array_elements(coalesce(p.record -> 'calls', '[]'::jsonb)) c
left join lateral jsonb_array_elements(coalesce(p.record -> 'surveys', '[]'::jsonb)) s
  on s.value ->> 'call_id' = c.value ->> 'call_id'
left join lateral jsonb_array_elements(coalesce(p.record -> 'gait_sessions', '[]'::jsonb)) g
  on g.value ->> 'call_id' = c.value ->> 'call_id'
  and g.value ->> 'attempt_id' = c.value ->> 'attempt_id';
revoke all on public.walking_assessment_results from public, anon, authenticated;
grant select on public.walking_assessment_results to service_role;
commit;

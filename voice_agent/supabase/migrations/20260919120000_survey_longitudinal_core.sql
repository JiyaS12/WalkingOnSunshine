-- Additive longitudinal survey schema for the VoiceAIThing prototype.
-- Synthetic/demo data only. This does not establish HIPAA compliance.
-- Do not store raw audio. Scoring must use confirmed_value only.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- updated_at helper
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- patients
-- display_id maps to the existing in-memory patient_code (e.g. RGN-0417).
-- Identifying fields (names, DOB) are omitted as unnecessary for this demo.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.patients (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  display_id text NOT NULL,
  condition_category text NOT NULL,
  procedure_type text,
  procedure_date date,
  phone_number text,
  is_synthetic boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT patients_display_id_key UNIQUE (display_id),
  CONSTRAINT patients_condition_category_check
    CHECK (condition_category IN ('orthopedic', 'stroke')),
  CONSTRAINT patients_synthetic_only CHECK (is_synthetic)
);

COMMENT ON TABLE public.patients IS
  'Synthetic/demo patients only. display_id matches existing patient_code values. Not real PHI.';
COMMENT ON COLUMN public.patients.display_id IS
  'Public demo code used by the existing runtime (patient_code), not a real MRN.';
COMMENT ON COLUMN public.patients.phone_number IS
  'Optional placeholder for future telephony. Leave null unless using a clearly fake demo number.';

DROP TRIGGER IF EXISTS patients_set_updated_at ON public.patients;
CREATE TRIGGER patients_set_updated_at
  BEFORE UPDATE ON public.patients
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

-- ---------------------------------------------------------------------------
-- survey_templates / survey_questions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.survey_templates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  version text NOT NULL,
  description text,
  condition_category text,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT survey_templates_name_version_key UNIQUE (name, version),
  CONSTRAINT survey_templates_condition_category_check
    CHECK (condition_category IS NULL OR condition_category IN ('orthopedic', 'stroke'))
);

COMMENT ON TABLE public.survey_templates IS
  'Survey instruments. Seeded HOOS JR matches the existing orthopedic question bank.';

CREATE TABLE IF NOT EXISTS public.survey_questions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  survey_template_id uuid NOT NULL REFERENCES public.survey_templates (id),
  question_order integer NOT NULL,
  question_key text NOT NULL,
  question_text text NOT NULL,
  response_type text NOT NULL DEFAULT 'ordinal',
  response_options jsonb,
  scoring_map jsonb,
  required boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT survey_questions_template_key_key UNIQUE (survey_template_id, question_key),
  CONSTRAINT survey_questions_template_order_key UNIQUE (survey_template_id, question_order),
  CONSTRAINT survey_questions_order_positive CHECK (question_order >= 1)
);

COMMENT ON TABLE public.survey_questions IS
  'question_key matches existing SurveyQuestion.id values such as hoos_stairs.';
COMMENT ON COLUMN public.survey_questions.scoring_map IS
  'Maps a confirmed response label to a numeric item weight. Never applied to ai_proposed_value.';

CREATE INDEX IF NOT EXISTS survey_questions_template_order_idx
  ON public.survey_questions (survey_template_id, question_order);

-- ---------------------------------------------------------------------------
-- survey_instances
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.survey_instances (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id uuid NOT NULL REFERENCES public.patients (id),
  survey_template_id uuid NOT NULL REFERENCES public.survey_templates (id),
  follow_up_label text NOT NULL,
  scheduled_for date,
  status text NOT NULL DEFAULT 'scheduled',
  total_score numeric,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT survey_instances_status_check
    CHECK (status IN ('scheduled', 'in_progress', 'completed', 'needs_review', 'failed')),
  CONSTRAINT survey_instances_patient_template_follow_up_key
    UNIQUE (patient_id, survey_template_id, follow_up_label)
);

COMMENT ON COLUMN public.survey_instances.total_score IS
  'Prototype sum of confirmed item weights only. Not a validated clinical instrument score.';
COMMENT ON COLUMN public.survey_instances.follow_up_label IS
  'Longitudinal label such as pre-op, 3 months, 1 year, or 5 years.';

CREATE INDEX IF NOT EXISTS survey_instances_patient_id_idx
  ON public.survey_instances (patient_id);
CREATE INDEX IF NOT EXISTS survey_instances_status_idx
  ON public.survey_instances (status);
CREATE INDEX IF NOT EXISTS survey_instances_scheduled_for_idx
  ON public.survey_instances (scheduled_for);

DROP TRIGGER IF EXISTS survey_instances_set_updated_at ON public.survey_instances;
CREATE TRIGGER survey_instances_set_updated_at
  BEFORE UPDATE ON public.survey_instances
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

-- ---------------------------------------------------------------------------
-- call_sessions (metadata only; never store raw audio)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.call_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  survey_instance_id uuid NOT NULL REFERENCES public.survey_instances (id),
  external_call_id text,
  provider text,
  started_at timestamptz,
  ended_at timestamptz,
  status text NOT NULL DEFAULT 'initiated',
  duration_seconds integer,
  failure_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT call_sessions_status_check
    CHECK (status IN ('initiated', 'in_progress', 'completed', 'failed')),
  CONSTRAINT call_sessions_duration_nonnegative
    CHECK (duration_seconds IS NULL OR duration_seconds >= 0)
);

COMMENT ON TABLE public.call_sessions IS
  'Call metadata only. Do not add audio, recording URLs, or other raw media columns.';

CREATE INDEX IF NOT EXISTS call_sessions_instance_idx
  ON public.call_sessions (survey_instance_id);
CREATE UNIQUE INDEX IF NOT EXISTS call_sessions_external_call_id_key
  ON public.call_sessions (external_call_id)
  WHERE external_call_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- survey_responses
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.survey_responses (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  survey_instance_id uuid NOT NULL REFERENCES public.survey_instances (id),
  survey_question_id uuid NOT NULL REFERENCES public.survey_questions (id),
  call_session_id uuid REFERENCES public.call_sessions (id),
  raw_patient_text text,
  ai_proposed_value jsonb,
  ai_confidence numeric,
  confirmation_status text NOT NULL DEFAULT 'pending',
  confirmed_value jsonb,
  confirmation_text text,
  evidence_text text,
  requires_review boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT survey_responses_instance_question_key
    UNIQUE (survey_instance_id, survey_question_id),
  CONSTRAINT survey_responses_confirmation_status_check
    CHECK (confirmation_status IN ('pending', 'confirmed', 'corrected', 'unclear', 'clinician_review')),
  CONSTRAINT survey_responses_confidence_range
    CHECK (ai_confidence IS NULL OR (ai_confidence >= 0 AND ai_confidence <= 1))
);

COMMENT ON TABLE public.survey_responses IS
  'Audit trail for proposed vs confirmed answers. Scoring reads confirmed_value only.';
COMMENT ON COLUMN public.survey_responses.ai_proposed_value IS
  'Interpreter proposal for audit. NEVER used as a score input.';
COMMENT ON COLUMN public.survey_responses.confirmed_value IS
  'Source of truth after patient confirmation or correction. The only scoring input.';
COMMENT ON COLUMN public.survey_responses.raw_patient_text IS
  'Turn transcript text for audit. Not raw audio.';

CREATE INDEX IF NOT EXISTS survey_responses_instance_idx
  ON public.survey_responses (survey_instance_id);
CREATE INDEX IF NOT EXISTS survey_responses_question_idx
  ON public.survey_responses (survey_question_id);
CREATE INDEX IF NOT EXISTS survey_responses_call_session_idx
  ON public.survey_responses (call_session_id);
CREATE INDEX IF NOT EXISTS survey_responses_confirmation_idx
  ON public.survey_responses (confirmation_status);

DROP TRIGGER IF EXISTS survey_responses_set_updated_at ON public.survey_responses;
CREATE TRIGGER survey_responses_set_updated_at
  BEFORE UPDATE ON public.survey_responses
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

-- ---------------------------------------------------------------------------
-- review_flags / audit_events
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.review_flags (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  survey_instance_id uuid NOT NULL REFERENCES public.survey_instances (id),
  survey_response_id uuid REFERENCES public.survey_responses (id),
  call_session_id uuid REFERENCES public.call_sessions (id),
  flag_type text NOT NULL,
  severity text NOT NULL DEFAULT 'medium',
  reason text,
  resolved boolean NOT NULL DEFAULT false,
  resolved_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT review_flags_type_check
    CHECK (flag_type IN (
      'low_confidence',
      'ambiguous_response',
      'clinical_concern',
      'call_failure',
      'user_requested_human'
    )),
  CONSTRAINT review_flags_severity_check
    CHECK (severity IN ('low', 'medium', 'high'))
);

COMMENT ON TABLE public.review_flags IS
  'Human-review markers only. flag_type clinical_concern is not a diagnosis.';

CREATE INDEX IF NOT EXISTS review_flags_instance_idx
  ON public.review_flags (survey_instance_id);
CREATE INDEX IF NOT EXISTS review_flags_unresolved_idx
  ON public.review_flags (survey_instance_id)
  WHERE resolved = false;

CREATE TABLE IF NOT EXISTS public.audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_type text NOT NULL,
  entity_id uuid NOT NULL,
  action text NOT NULL,
  actor_type text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT audit_events_actor_type_check
    CHECK (actor_type IN ('system', 'patient', 'clinician', 'ai'))
);

COMMENT ON TABLE public.audit_events IS
  'Append-only workflow audit (survey_started, answer_proposed, answer_confirmed, ...).';

CREATE INDEX IF NOT EXISTS audit_events_entity_idx
  ON public.audit_events (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS audit_events_action_idx
  ON public.audit_events (action);
CREATE INDEX IF NOT EXISTS audit_events_created_at_idx
  ON public.audit_events (created_at);

-- ---------------------------------------------------------------------------
-- Scoring: confirmed_value is the only input. ai_proposed_value is never read.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.confirmed_value_label(value jsonb)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET search_path = public
AS $$
  SELECT CASE
    WHEN value IS NULL THEN NULL
    WHEN jsonb_typeof(value) = 'string' THEN NULLIF(value #>> '{}', '')
    WHEN jsonb_typeof(value) = 'object' AND value ? 'value' THEN NULLIF(value ->> 'value', '')
    WHEN jsonb_typeof(value) = 'object' AND value ? 'label' THEN NULLIF(value ->> 'label', '')
    ELSE NULL
  END
$$;

COMMENT ON FUNCTION public.confirmed_value_label(jsonb) IS
  'Extracts a scoring label from confirmed_value JSON (string or {value|label}).';

CREATE OR REPLACE FUNCTION public.score_survey_instance(p_instance_id uuid)
RETURNS numeric
LANGUAGE plpgsql
STABLE
SET search_path = public
AS $$
DECLARE
  v_template_id uuid;
  v_required_missing integer;
  v_unmapped integer;
  v_total numeric;
BEGIN
  SELECT survey_template_id INTO v_template_id
  FROM public.survey_instances
  WHERE id = p_instance_id;

  IF v_template_id IS NULL THEN
    RETURN NULL;
  END IF;

  -- Required questions without a confirmed/corrected confirmed_value are incomplete.
  SELECT COUNT(*) INTO v_required_missing
  FROM public.survey_questions q
  WHERE q.survey_template_id = v_template_id
    AND q.required IS TRUE
    AND NOT EXISTS (
      SELECT 1
      FROM public.survey_responses r
      WHERE r.survey_instance_id = p_instance_id
        AND r.survey_question_id = q.id
        AND r.confirmation_status IN ('confirmed', 'corrected')
        AND r.confirmed_value IS NOT NULL
        AND public.confirmed_value_label(r.confirmed_value) IS NOT NULL
    );

  IF v_required_missing > 0 THEN
    RETURN NULL;
  END IF;

  -- Confirmed labels that cannot be mapped are unsafe to score.
  SELECT COUNT(*) INTO v_unmapped
  FROM public.survey_responses r
  JOIN public.survey_questions q ON q.id = r.survey_question_id
  WHERE r.survey_instance_id = p_instance_id
    AND r.confirmation_status IN ('confirmed', 'corrected')
    AND r.confirmed_value IS NOT NULL
    AND (q.scoring_map ->> public.confirmed_value_label(r.confirmed_value)) IS NULL;

  IF v_unmapped > 0 THEN
    RETURN NULL;
  END IF;

  -- Intentionally selects confirmed_value / scoring_map only. Interpreter proposals are unused.
  SELECT COALESCE(
    SUM((q.scoring_map ->> public.confirmed_value_label(r.confirmed_value))::numeric),
    0
  )
  INTO v_total
  FROM public.survey_responses r
  JOIN public.survey_questions q ON q.id = r.survey_question_id
  WHERE r.survey_instance_id = p_instance_id
    AND r.confirmation_status IN ('confirmed', 'corrected')
    AND r.confirmed_value IS NOT NULL;

  RETURN v_total;
END;
$$;

COMMENT ON FUNCTION public.score_survey_instance(uuid) IS
  'Returns the prototype item-weight sum from confirmed_value only. NULL if required answers are missing. Never reads ai_proposed_value. Not a validated HOOS JR interval score.';

CREATE OR REPLACE FUNCTION public.refresh_survey_instance_score(p_instance_id uuid)
RETURNS numeric
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  v_score numeric;
BEGIN
  v_score := public.score_survey_instance(p_instance_id);
  UPDATE public.survey_instances
  SET total_score = v_score
  WHERE id = p_instance_id;
  RETURN v_score;
END;
$$;

CREATE OR REPLACE FUNCTION public.trg_refresh_instance_score()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  v_instance_id uuid;
BEGIN
  v_instance_id := COALESCE(NEW.survey_instance_id, OLD.survey_instance_id);
  IF v_instance_id IS NOT NULL THEN
    PERFORM public.refresh_survey_instance_score(v_instance_id);
  END IF;
  RETURN COALESCE(NEW, OLD);
END;
$$;

DROP TRIGGER IF EXISTS survey_responses_refresh_score ON public.survey_responses;
CREATE TRIGGER survey_responses_refresh_score
  AFTER INSERT OR UPDATE OF confirmed_value, confirmation_status, survey_question_id
  OR DELETE ON public.survey_responses
  FOR EACH ROW
  EXECUTE FUNCTION public.trg_refresh_instance_score();

-- ---------------------------------------------------------------------------
-- Dashboard and audit views (security_invoker so base-table RLS applies)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW public.clinician_dashboard
WITH (security_invoker = true) AS
SELECT
  si.id AS survey_instance_id,
  p.display_id AS patient_display_id,
  p.condition_category,
  st.name AS survey_name,
  st.version AS survey_version,
  si.follow_up_label,
  si.status AS survey_status,
  si.scheduled_for,
  si.completed_at,
  si.total_score,
  (
    SELECT COUNT(*)
    FROM public.review_flags rf
    WHERE rf.survey_instance_id = si.id
      AND rf.resolved = false
  ) AS review_flag_count
FROM public.survey_instances si
JOIN public.patients p ON p.id = si.patient_id
JOIN public.survey_templates st ON st.id = si.survey_template_id;

COMMENT ON VIEW public.clinician_dashboard IS
  'Clinician/research dashboard rows. total_score is the stored confirmed-value score.';

CREATE OR REPLACE VIEW public.survey_response_audit
WITH (security_invoker = true) AS
SELECT
  r.id AS survey_response_id,
  si.id AS survey_instance_id,
  p.display_id AS patient_display_id,
  si.follow_up_label,
  q.question_key,
  q.question_order,
  q.question_text,
  r.raw_patient_text,
  r.ai_proposed_value,
  r.ai_confidence,
  r.confirmed_value,
  r.confirmation_status,
  r.confirmation_text,
  r.evidence_text,
  r.requires_review,
  r.call_session_id,
  r.created_at,
  r.updated_at
FROM public.survey_responses r
JOIN public.survey_instances si ON si.id = r.survey_instance_id
JOIN public.patients p ON p.id = si.patient_id
JOIN public.survey_questions q ON q.id = r.survey_question_id;

COMMENT ON VIEW public.survey_response_audit IS
  'Per-question audit of raw text, AI proposal, confidence, and confirmed value.';

-- ---------------------------------------------------------------------------
-- RLS: anon has no policies (deny). authenticated is read-only for the demo.
-- service_role is granted write access for a future server-side backend.
-- There is no organization_id in this repo; tenancy is documented, not invented.
-- ---------------------------------------------------------------------------

ALTER TABLE public.patients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.survey_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.survey_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.survey_instances ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.call_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.survey_responses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.review_flags ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.patients FROM PUBLIC;
REVOKE ALL ON TABLE public.survey_templates FROM PUBLIC;
REVOKE ALL ON TABLE public.survey_questions FROM PUBLIC;
REVOKE ALL ON TABLE public.survey_instances FROM PUBLIC;
REVOKE ALL ON TABLE public.call_sessions FROM PUBLIC;
REVOKE ALL ON TABLE public.survey_responses FROM PUBLIC;
REVOKE ALL ON TABLE public.review_flags FROM PUBLIC;
REVOKE ALL ON TABLE public.audit_events FROM PUBLIC;
REVOKE ALL ON TABLE public.clinician_dashboard FROM PUBLIC;
REVOKE ALL ON TABLE public.survey_response_audit FROM PUBLIC;

REVOKE ALL ON FUNCTION public.refresh_survey_instance_score(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.trg_refresh_instance_score() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.set_updated_at() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.score_survey_instance(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.confirmed_value_label(jsonb) FROM PUBLIC;

-- Role-specific grants/policies are applied only when Supabase roles exist so
-- the same file can be loaded into a local Postgres without those roles.
DO $$
DECLARE
  tbl text;
  tables text[] := ARRAY[
    'patients',
    'survey_templates',
    'survey_questions',
    'survey_instances',
    'call_sessions',
    'survey_responses',
    'review_flags',
    'audit_events'
  ];
  views text[] := ARRAY[
    'clinician_dashboard',
    'survey_response_audit'
  ];
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    FOREACH tbl IN ARRAY tables LOOP
      EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon', tbl);
    END LOOP;
    FOREACH tbl IN ARRAY views LOOP
      EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon', tbl);
    END LOOP;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    FOREACH tbl IN ARRAY tables LOOP
      EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_authenticated_select', tbl);
      EXECUTE format(
        'CREATE POLICY %I ON public.%I FOR SELECT TO authenticated USING (true)',
        tbl || '_authenticated_select',
        tbl
      );
      EXECUTE format('GRANT SELECT ON TABLE public.%I TO authenticated', tbl);
    END LOOP;
    FOREACH tbl IN ARRAY views LOOP
      EXECUTE format('GRANT SELECT ON TABLE public.%I TO authenticated', tbl);
    END LOOP;
    GRANT EXECUTE ON FUNCTION public.score_survey_instance(uuid) TO authenticated;
    GRANT EXECUTE ON FUNCTION public.confirmed_value_label(jsonb) TO authenticated;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    FOREACH tbl IN ARRAY tables LOOP
      EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_service_role_all', tbl);
      EXECUTE format(
        'CREATE POLICY %I ON public.%I FOR ALL TO service_role USING (true) WITH CHECK (true)',
        tbl || '_service_role_all',
        tbl
      );
      EXECUTE format('GRANT ALL ON TABLE public.%I TO service_role', tbl);
    END LOOP;
    FOREACH tbl IN ARRAY views LOOP
      EXECUTE format('GRANT SELECT ON TABLE public.%I TO service_role', tbl);
    END LOOP;
    GRANT EXECUTE ON FUNCTION public.score_survey_instance(uuid) TO service_role;
    GRANT EXECUTE ON FUNCTION public.confirmed_value_label(jsonb) TO service_role;
    GRANT EXECUTE ON FUNCTION public.refresh_survey_instance_score(uuid) TO service_role;
    GRANT EXECUTE ON FUNCTION public.set_updated_at() TO service_role;
    GRANT EXECUTE ON FUNCTION public.trg_refresh_instance_score() TO service_role;
  END IF;
END;
$$;

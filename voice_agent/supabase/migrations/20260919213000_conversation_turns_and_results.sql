-- Additive conversation transcript storage and a per-patient results view.
-- Text transcripts only. Do not store raw audio.
-- Also seeds the existing stroke question bank so RGN-0500 can persist results.

-- ---------------------------------------------------------------------------
-- conversation_turns
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.conversation_turns (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id uuid NOT NULL REFERENCES public.patients (id),
  survey_instance_id uuid REFERENCES public.survey_instances (id),
  call_session_id uuid REFERENCES public.call_sessions (id),
  turn_index integer NOT NULL,
  speaker text NOT NULL,
  text text NOT NULL,
  question_key text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT conversation_turns_speaker_check
    CHECK (speaker IN ('patient', 'assistant', 'system')),
  CONSTRAINT conversation_turns_turn_index_positive
    CHECK (turn_index >= 1),
  CONSTRAINT conversation_turns_session_index_key
    UNIQUE (call_session_id, turn_index)
);

COMMENT ON TABLE public.conversation_turns IS
  'Ordered conversation text for a call. Patient turns are STT transcripts; assistant turns are application-owned speech. Never store audio.';
COMMENT ON COLUMN public.conversation_turns.text IS
  'Transcript or spoken prompt text only. Not a recording.';

CREATE INDEX IF NOT EXISTS conversation_turns_patient_id_idx
  ON public.conversation_turns (patient_id);
CREATE INDEX IF NOT EXISTS conversation_turns_instance_idx
  ON public.conversation_turns (survey_instance_id);
CREATE INDEX IF NOT EXISTS conversation_turns_call_session_idx
  ON public.conversation_turns (call_session_id);

ALTER TABLE public.conversation_turns ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.conversation_turns FROM PUBLIC;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON TABLE public.conversation_turns FROM anon;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    DROP POLICY IF EXISTS conversation_turns_authenticated_select ON public.conversation_turns;
    CREATE POLICY conversation_turns_authenticated_select
      ON public.conversation_turns FOR SELECT TO authenticated
      USING (true);
    GRANT SELECT ON TABLE public.conversation_turns TO authenticated;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    DROP POLICY IF EXISTS conversation_turns_service_role_all ON public.conversation_turns;
    CREATE POLICY conversation_turns_service_role_all
      ON public.conversation_turns FOR ALL TO service_role
      USING (true) WITH CHECK (true);
    GRANT ALL ON TABLE public.conversation_turns TO service_role;
  END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- Per-user identifying ID + full transcript + survey results
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW public.patient_conversation_results
WITH (security_invoker = true) AS
SELECT
  p.id AS patient_uuid,
  p.display_id AS patient_id,
  si.id AS survey_instance_id,
  cs.id AS call_session_id,
  cs.external_call_id,
  si.follow_up_label,
  si.status AS survey_status,
  si.total_score,
  si.started_at,
  si.completed_at,
  (
    SELECT string_agg(ct.speaker || ': ' || ct.text, E'\n' ORDER BY ct.turn_index)
    FROM public.conversation_turns ct
    WHERE cs.id IS NOT NULL AND ct.call_session_id = cs.id
  ) AS transcript,
  (
    SELECT COALESCE(jsonb_agg(
      jsonb_build_object(
        'question_key', q.question_key,
        'question_text', q.question_text,
        'raw_patient_text', r.raw_patient_text,
        'ai_proposed_value', r.ai_proposed_value,
        'confirmed_value', r.confirmed_value,
        'confirmation_status', r.confirmation_status,
        'evidence_text', r.evidence_text
      ) ORDER BY q.question_order
    ), '[]'::jsonb)
    FROM public.survey_responses r
    JOIN public.survey_questions q ON q.id = r.survey_question_id
    WHERE r.survey_instance_id = si.id
  ) AS survey_results
FROM public.survey_instances si
JOIN public.patients p ON p.id = si.patient_id
LEFT JOIN LATERAL (
  SELECT *
  FROM public.call_sessions inner_cs
  WHERE inner_cs.survey_instance_id = si.id
  ORDER BY inner_cs.created_at DESC
  LIMIT 1
) cs ON true;

COMMENT ON VIEW public.patient_conversation_results IS
  'One row per survey instance: synthetic patient_id, conversation transcript text, and structured survey results.';

REVOKE ALL ON TABLE public.patient_conversation_results FROM PUBLIC;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON TABLE public.patient_conversation_results FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    GRANT SELECT ON TABLE public.patient_conversation_results TO authenticated;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    GRANT SELECT ON TABLE public.patient_conversation_results TO service_role;
  END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- Stroke template matching app/question_loader.py (idempotent)
-- ---------------------------------------------------------------------------

INSERT INTO public.survey_templates (
  id, name, version, description, condition_category, is_active
)
SELECT
  'bbbbbbbb-bbbb-4bbb-8bbb-000000000002'::uuid,
  'Stroke function subset',
  'demo-1',
  'Demo 6-item stroke function subset matching the existing stroke question bank. Prototype scoring only.',
  'stroke',
  true
WHERE NOT EXISTS (
  SELECT 1
  FROM public.survey_templates t
  WHERE t.id = 'bbbbbbbb-bbbb-4bbb-8bbb-000000000002'
     OR (t.name = 'Stroke function subset' AND t.version = 'demo-1')
);

INSERT INTO public.survey_questions (
  id,
  survey_template_id,
  question_order,
  question_key,
  question_text,
  response_type,
  response_options,
  scoring_map,
  required
)
SELECT
  seed.id,
  t.id,
  seed.question_order,
  seed.question_key,
  seed.question_text,
  'ordinal',
  '["none", "mild", "moderate", "severe", "extreme"]'::jsonb,
  '{"none": 0, "mild": 1, "moderate": 2, "severe": 3, "extreme": 4}'::jsonb,
  true
FROM public.survey_templates t
JOIN (
  VALUES
    (
      'cccccccc-cccc-4ccc-8ccc-000000000101'::uuid,
      1,
      'stroke_balance',
      'Over the past week, how much difficulty have you had with balance while standing or walking?'
    ),
    (
      'cccccccc-cccc-4ccc-8ccc-000000000102'::uuid,
      2,
      'stroke_weakness',
      'Over the past week, how much weakness have you experienced when moving your affected side?'
    ),
    (
      'cccccccc-cccc-4ccc-8ccc-000000000103'::uuid,
      3,
      'stroke_stairs',
      'Over the past week, how much difficulty have you had climbing stairs?'
    ),
    (
      'cccccccc-cccc-4ccc-8ccc-000000000104'::uuid,
      4,
      'stroke_turning',
      'Over the past week, how much difficulty have you had turning or changing direction while walking?'
    ),
    (
      'cccccccc-cccc-4ccc-8ccc-000000000105'::uuid,
      5,
      'stroke_walking',
      'Over the past week, how much difficulty have you had walking across a room or short distance?'
    ),
    (
      'cccccccc-cccc-4ccc-8ccc-000000000106'::uuid,
      6,
      'stroke_recovery',
      'Over the past week, how much difficulty have you had completing your normal daily activities because of your recovery?'
    )
) AS seed(id, question_order, question_key, question_text) ON true
WHERE (t.id = 'bbbbbbbb-bbbb-4bbb-8bbb-000000000002' OR (t.name = 'Stroke function subset' AND t.version = 'demo-1'))
  AND NOT EXISTS (
    SELECT 1
    FROM public.survey_questions q
    WHERE q.id = seed.id
       OR (q.survey_template_id = t.id AND q.question_key = seed.question_key)
  );

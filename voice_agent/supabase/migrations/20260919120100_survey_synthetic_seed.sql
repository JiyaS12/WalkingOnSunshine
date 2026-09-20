-- Idempotent synthetic/demo seed only. Does not delete or replace existing rows.
-- Patient codes match the existing in-memory repository (RGN-0417, RGN-0500)
-- plus one additional orthopedic demo patient (RGN-0600).
-- HOOS JR questions match app/question_loader.py rather than adding a duplicate
-- knee instrument.

INSERT INTO public.patients (
  id, display_id, condition_category, procedure_type, procedure_date, is_synthetic
)
SELECT *
FROM (
  VALUES
    (
      'aaaaaaaa-aaaa-4aaa-8aaa-000000000417'::uuid,
      'RGN-0417',
      'orthopedic',
      'total hip arthroplasty (synthetic demo)',
      DATE '2026-06-19',
      true
    ),
    (
      'aaaaaaaa-aaaa-4aaa-8aaa-000000000500'::uuid,
      'RGN-0500',
      'stroke',
      NULL,
      NULL,
      true
    ),
    (
      'aaaaaaaa-aaaa-4aaa-8aaa-000000000600'::uuid,
      'RGN-0600',
      'orthopedic',
      'total hip arthroplasty (synthetic demo)',
      DATE '2026-10-03',
      true
    )
) AS seed(id, display_id, condition_category, procedure_type, procedure_date, is_synthetic)
WHERE NOT EXISTS (
  SELECT 1
  FROM public.patients p
  WHERE p.id = seed.id OR p.display_id = seed.display_id
);

INSERT INTO public.survey_templates (
  id, name, version, description, condition_category, is_active
)
SELECT
  'bbbbbbbb-bbbb-4bbb-8bbb-000000000001'::uuid,
  'HOOS JR',
  'demo-1',
  'Demo 6-item hip osteoarthritis outcome survey matching the existing orthopedic question bank. Prototype scoring only; not a validated clinical instrument.',
  'orthopedic',
  true
WHERE NOT EXISTS (
  SELECT 1
  FROM public.survey_templates t
  WHERE t.id = 'bbbbbbbb-bbbb-4bbb-8bbb-000000000001'
     OR (t.name = 'HOOS JR' AND t.version = 'demo-1')
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
  seed.response_options,
  seed.scoring_map,
  true
FROM public.survey_templates t
JOIN (
  VALUES
    (
      'cccccccc-cccc-4ccc-8ccc-000000000001'::uuid,
      1,
      'hoos_stairs',
      'Over the past week, how much hip pain have you experienced going up or down stairs?',
      '["none", "mild", "moderate", "severe", "extreme"]'::jsonb,
      '{"none": 0, "mild": 1, "moderate": 2, "severe": 3, "extreme": 4}'::jsonb
    ),
    (
      'cccccccc-cccc-4ccc-8ccc-000000000002'::uuid,
      2,
      'hoos_uneven_surface',
      'Over the past week, how much hip pain have you experienced walking on an uneven surface?',
      '["none", "mild", "moderate", "severe", "extreme"]'::jsonb,
      '{"none": 0, "mild": 1, "moderate": 2, "severe": 3, "extreme": 4}'::jsonb
    ),
    (
      'cccccccc-cccc-4ccc-8ccc-000000000003'::uuid,
      3,
      'hoos_rising',
      'Over the past week, how much difficulty have you had rising from sitting because of your hip?',
      '["none", "mild", "moderate", "severe", "extreme"]'::jsonb,
      '{"none": 0, "mild": 1, "moderate": 2, "severe": 3, "extreme": 4}'::jsonb
    ),
    (
      'cccccccc-cccc-4ccc-8ccc-000000000004'::uuid,
      4,
      'hoos_bending',
      'Over the past week, how much difficulty have you had bending to the floor or picking up an object because of your hip?',
      '["none", "mild", "moderate", "severe", "extreme"]'::jsonb,
      '{"none": 0, "mild": 1, "moderate": 2, "severe": 3, "extreme": 4}'::jsonb
    ),
    (
      'cccccccc-cccc-4ccc-8ccc-000000000005'::uuid,
      5,
      'hoos_lying_bed',
      'Over the past week, how much difficulty have you had lying in bed, turning over, or maintaining your hip position because of your hip?',
      '["none", "mild", "moderate", "severe", "extreme"]'::jsonb,
      '{"none": 0, "mild": 1, "moderate": 2, "severe": 3, "extreme": 4}'::jsonb
    ),
    (
      'cccccccc-cccc-4ccc-8ccc-000000000006'::uuid,
      6,
      'hoos_sitting',
      'Over the past week, how much difficulty have you had sitting because of your hip?',
      '["none", "mild", "moderate", "severe", "extreme"]'::jsonb,
      '{"none": 0, "mild": 1, "moderate": 2, "severe": 3, "extreme": 4}'::jsonb
    )
) AS seed(id, question_order, question_key, question_text, response_options, scoring_map)
  ON true
WHERE (t.id = 'bbbbbbbb-bbbb-4bbb-8bbb-000000000001' OR (t.name = 'HOOS JR' AND t.version = 'demo-1'))
  AND NOT EXISTS (
    SELECT 1
    FROM public.survey_questions q
    WHERE q.id = seed.id
       OR (q.survey_template_id = t.id AND q.question_key = seed.question_key)
  );

-- Completed 3-month survey for RGN-0417.
INSERT INTO public.survey_instances (
  id,
  patient_id,
  survey_template_id,
  follow_up_label,
  scheduled_for,
  status,
  started_at,
  completed_at
)
SELECT
  'dddddddd-dddd-4ddd-8ddd-000000000001'::uuid,
  p.id,
  t.id,
  '3 months',
  DATE '2026-09-19',
  'completed',
  TIMESTAMPTZ '2026-09-19 14:02:00+00',
  TIMESTAMPTZ '2026-09-19 14:11:00+00'
FROM public.patients p
JOIN public.survey_templates t
  ON t.id = 'bbbbbbbb-bbbb-4bbb-8bbb-000000000001'
  OR (t.name = 'HOOS JR' AND t.version = 'demo-1')
WHERE p.display_id = 'RGN-0417'
  AND NOT EXISTS (
    SELECT 1
    FROM public.survey_instances si
    WHERE si.id = 'dddddddd-dddd-4ddd-8ddd-000000000001'
       OR (
         si.patient_id = p.id
         AND si.survey_template_id = t.id
         AND si.follow_up_label = '3 months'
       )
  );

-- Pending pre-op survey for RGN-0600.
INSERT INTO public.survey_instances (
  id,
  patient_id,
  survey_template_id,
  follow_up_label,
  scheduled_for,
  status
)
SELECT
  'dddddddd-dddd-4ddd-8ddd-000000000002'::uuid,
  p.id,
  t.id,
  'pre-op',
  DATE '2026-09-26',
  'scheduled'
FROM public.patients p
JOIN public.survey_templates t
  ON t.id = 'bbbbbbbb-bbbb-4bbb-8bbb-000000000001'
  OR (t.name = 'HOOS JR' AND t.version = 'demo-1')
WHERE p.display_id = 'RGN-0600'
  AND NOT EXISTS (
    SELECT 1
    FROM public.survey_instances si
    WHERE si.id = 'dddddddd-dddd-4ddd-8ddd-000000000002'
       OR (
         si.patient_id = p.id
         AND si.survey_template_id = t.id
         AND si.follow_up_label = 'pre-op'
       )
  );

INSERT INTO public.call_sessions (
  id,
  survey_instance_id,
  external_call_id,
  provider,
  started_at,
  ended_at,
  status,
  duration_seconds
)
SELECT
  'eeeeeeee-eeee-4eee-8eee-000000000001'::uuid,
  si.id,
  'demo-desktop-rg0417-3mo',
  'desktop_demo',
  TIMESTAMPTZ '2026-09-19 14:02:00+00',
  TIMESTAMPTZ '2026-09-19 14:11:00+00',
  'completed',
  540
FROM public.survey_instances si
JOIN public.patients p ON p.id = si.patient_id
WHERE (si.id = 'dddddddd-dddd-4ddd-8ddd-000000000001' OR (p.display_id = 'RGN-0417' AND si.follow_up_label = '3 months'))
  AND NOT EXISTS (
    SELECT 1
    FROM public.call_sessions cs
    WHERE cs.id = 'eeeeeeee-eeee-4eee-8eee-000000000001'
       OR cs.external_call_id = 'demo-desktop-rg0417-3mo'
  );

-- Confirmed responses. Question 2 is a correction so scoring must use
-- confirmed_value (moderate=2) and ignore ai_proposed_value (severe=3).
INSERT INTO public.survey_responses (
  id,
  survey_instance_id,
  survey_question_id,
  call_session_id,
  raw_patient_text,
  ai_proposed_value,
  ai_confidence,
  confirmation_status,
  confirmed_value,
  confirmation_text,
  evidence_text,
  requires_review
)
SELECT
  seed.id,
  si.id,
  q.id,
  cs.id,
  seed.raw_patient_text,
  seed.ai_proposed_value,
  NULL,
  seed.confirmation_status,
  seed.confirmed_value,
  seed.confirmation_text,
  seed.evidence_text,
  false
FROM public.survey_instances si
JOIN public.patients p ON p.id = si.patient_id
JOIN public.survey_questions q ON q.survey_template_id = si.survey_template_id
LEFT JOIN public.call_sessions cs
  ON cs.survey_instance_id = si.id
 AND (cs.id = 'eeeeeeee-eeee-4eee-8eee-000000000001'
      OR cs.external_call_id = 'demo-desktop-rg0417-3mo')
JOIN (
  VALUES
    (
      'ffffffff-ffff-4fff-8fff-000000000001'::uuid,
      'hoos_stairs',
      'Going up stairs is a little painful, but I can still do it.',
      '"mild"'::jsonb,
      'confirmed',
      '"mild"'::jsonb,
      'yes',
      'a little painful'
    ),
    (
      'ffffffff-ffff-4fff-8fff-000000000002'::uuid,
      'hoos_uneven_surface',
      'Uneven ground bothers me more. I would not call it severe, more like medium.',
      '"severe"'::jsonb,
      'corrected',
      '"moderate"'::jsonb,
      'no, it is moderate',
      'more like medium'
    ),
    (
      'ffffffff-ffff-4fff-8fff-000000000003'::uuid,
      'hoos_rising',
      'Getting up from a chair is a bit stiff.',
      '"mild"'::jsonb,
      'confirmed',
      '"mild"'::jsonb,
      'yes that is right',
      'a bit stiff'
    ),
    (
      'ffffffff-ffff-4fff-8fff-000000000004'::uuid,
      'hoos_bending',
      'Bending to pick something up is pretty hard some days.',
      '"moderate"'::jsonb,
      'confirmed',
      '"moderate"'::jsonb,
      'yes',
      'pretty hard some days'
    ),
    (
      'ffffffff-ffff-4fff-8fff-000000000005'::uuid,
      'hoos_lying_bed',
      'Sleeping and turning in bed has not been a problem.',
      '"none"'::jsonb,
      'confirmed',
      '"none"'::jsonb,
      'yes',
      'has not been a problem'
    ),
    (
      'ffffffff-ffff-4fff-8fff-000000000006'::uuid,
      'hoos_sitting',
      'Sitting is okay, just a little sore if I stay too long.',
      '"mild"'::jsonb,
      'confirmed',
      '"mild"'::jsonb,
      'that is right',
      'a little sore if I stay too long'
    )
) AS seed(
  id,
  question_key,
  raw_patient_text,
  ai_proposed_value,
  confirmation_status,
  confirmed_value,
  confirmation_text,
  evidence_text
) ON seed.question_key = q.question_key
WHERE (si.id = 'dddddddd-dddd-4ddd-8ddd-000000000001' OR (p.display_id = 'RGN-0417' AND si.follow_up_label = '3 months'))
  AND NOT EXISTS (
    SELECT 1
    FROM public.survey_responses r
    WHERE r.id = seed.id
       OR (r.survey_instance_id = si.id AND r.survey_question_id = q.id)
  );

INSERT INTO public.audit_events (
  id, entity_type, entity_id, action, actor_type, metadata, created_at
)
SELECT *
FROM (
  VALUES
    (
      '99999999-9999-4999-8999-000000000001'::uuid,
      'survey_instance',
      'dddddddd-dddd-4ddd-8ddd-000000000001'::uuid,
      'survey_started',
      'system',
      '{"patient_display_id": "RGN-0417", "follow_up_label": "3 months"}'::jsonb,
      TIMESTAMPTZ '2026-09-19 14:02:00+00'
    ),
    (
      '99999999-9999-4999-8999-000000000002'::uuid,
      'survey_response',
      'ffffffff-ffff-4fff-8fff-000000000001'::uuid,
      'answer_proposed',
      'ai',
      '{"question_key": "hoos_stairs", "ai_proposed_value": "mild"}'::jsonb,
      TIMESTAMPTZ '2026-09-19 14:03:10+00'
    ),
    (
      '99999999-9999-4999-8999-000000000003'::uuid,
      'survey_response',
      'ffffffff-ffff-4fff-8fff-000000000001'::uuid,
      'answer_confirmed',
      'patient',
      '{"question_key": "hoos_stairs", "confirmed_value": "mild"}'::jsonb,
      TIMESTAMPTZ '2026-09-19 14:03:18+00'
    ),
    (
      '99999999-9999-4999-8999-000000000004'::uuid,
      'survey_response',
      'ffffffff-ffff-4fff-8fff-000000000002'::uuid,
      'answer_proposed',
      'ai',
      '{"question_key": "hoos_uneven_surface", "ai_proposed_value": "severe"}'::jsonb,
      TIMESTAMPTZ '2026-09-19 14:04:20+00'
    ),
    (
      '99999999-9999-4999-8999-000000000005'::uuid,
      'survey_response',
      'ffffffff-ffff-4fff-8fff-000000000002'::uuid,
      'answer_corrected',
      'patient',
      '{"question_key": "hoos_uneven_surface", "ai_proposed_value": "severe", "confirmed_value": "moderate"}'::jsonb,
      TIMESTAMPTZ '2026-09-19 14:04:40+00'
    ),
    (
      '99999999-9999-4999-8999-000000000006'::uuid,
      'survey_instance',
      'dddddddd-dddd-4ddd-8ddd-000000000001'::uuid,
      'survey_completed',
      'system',
      '{"source": "confirmed_value_only"}'::jsonb,
      TIMESTAMPTZ '2026-09-19 14:11:00+00'
    )
) AS seed(id, entity_type, entity_id, action, actor_type, metadata, created_at)
WHERE NOT EXISTS (
  SELECT 1 FROM public.audit_events e WHERE e.id = seed.id
);

-- Recompute in case this seed is applied against rows that already existed
-- without firing the score trigger.
SELECT public.refresh_survey_instance_score(si.id)
FROM public.survey_instances si
JOIN public.patients p ON p.id = si.patient_id
WHERE si.id IN (
  'dddddddd-dddd-4ddd-8ddd-000000000001',
  'dddddddd-dddd-4ddd-8ddd-000000000002'
)
OR (p.display_id IN ('RGN-0417', 'RGN-0600') AND si.follow_up_label IN ('3 months', 'pre-op'));

import type { GaitMetrics, PatientRecord } from "./api";
import type { CallRecord, WalkingView } from "./integration";

export const metrics: GaitMetrics = {
  stride_length_m: 1, asymmetry_pct: 5, velocity_degradation_pct: 2,
  fall_risk_score: 0.2, cadence_steps_per_min: 100, frame_count: 90,
  leg_length_m: 0.9, stride_ratio: 1.1, knee_flexion_rom_deg: 40,
  peak_ankle_speed_mps: 3, gait_detected: true, dropped_frame_pct: 0,
};

export function callRecord(patch: Partial<CallRecord> = {}): CallRecord {
  return {
    patient_id: "patient-1", call_id: "call-1", attempt_id: "attempt-1",
    request_id: "request_0123456789", condition_category: "orthopedic",
    call_status: "completed", survey_status: "stored", survey_id: "survey-1",
    sms_status: "sent", sms_attempt: 1, sms_retries: [], provider_call_id: null,
    phone_session_id: null, message_id: null, error_code: null, version: 3,
    phone_version: 2, created_at: "2026-09-20T00:00:00Z", updated_at: "2026-09-20T00:00:00Z",
    walking: { status: "waiting", last_sequence: 0, last_event: null, session_id: null, events: [] },
    ...patch,
  };
}

export function walkingView(patch: Partial<WalkingView> = {}): WalkingView {
  return {
    call_id: "call-1", attempt_id: "attempt-1", version: 3, survey_status: "stored",
    status: "waiting", last_sequence: 0, last_event: null, session_id: null, ...patch,
  };
}

export function patientRecord(patch: Partial<PatientRecord> = {}): PatientRecord {
  return { patient_id: "patient-1", name: "Patient One", surveys: [], gait_sessions: [], ...patch };
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

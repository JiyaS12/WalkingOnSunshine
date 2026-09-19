export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface GaitMetrics {
  stride_length_m: number;
  asymmetry_pct: number;
  velocity_degradation_pct: number;
  fall_risk_score: number;
  cadence_steps_per_min: number;
  frame_count: number;
  leg_length_m: number;
  stride_ratio: number;
  knee_flexion_rom_deg: number;
  peak_ankle_speed_mps: number;
  gait_detected: boolean;
}

export type JointFrame = Record<string, number[]>;

export interface SimulationSession {
  day: number;
  fps: number;
  frame_count: number;
  metrics: GaitMetrics;
  frames: JointFrame[];
}

export interface Comparison {
  patient_id: string;
  cohort: string;
  day_1: GaitMetrics;
  day_14: GaitMetrics;
  deltas: Record<string, number>;
}

export interface SummaryResponse {
  summary: string;
  source: "openai" | "template";
  cached: boolean;
  estimated_tokens_saved: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, init);
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function fetchSimulation(
  day?: 1 | 14
): Promise<SimulationSession | Comparison> {
  if (day === undefined) {
    return request<Comparison>("/api/get-simulation");
  }
  return request<SimulationSession>(`/api/get-simulation?day=${day}`);
}

export async function processFrames(
  frames: JointFrame[],
  fps: number,
  legLengthM?: number,
  signal?: AbortSignal
): Promise<GaitMetrics> {
  return request<GaitMetrics>("/api/process-frame", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      frames,
      fps,
      ...(legLengthM !== undefined ? { leg_length_m: legLengthM } : {}),
    }),
    signal,
  });
}

export interface Survey {
  patient_id: string;
  patient_name?: string | null;
  pain_scale: number;
  fall_history: {
    falls_last_6_months: number;
    injured: boolean;
    last_fall_description?: string | null;
  };
  dizziness: boolean;
  dizziness_notes?: string | null;
  primary_complaints: string[];
  call_id?: string | null;
  recorded_at?: string | null;
}

export interface GaitSession {
  label: string;
  recorded_at?: string | null;
  source: string;
  metrics: GaitMetrics;
  frames_ref?: string | null;
  frames?: JointFrame[] | null;
}

export interface PatientSummary {
  patient_id: string;
  name?: string | null;
  age?: number | null;
  latest_survey_at?: string | null;
  pain_scale?: number | null;
  dizziness?: boolean | null;
  falls_last_6_months?: number | null;
  latest_fall_risk?: number | null;
  latest_asymmetry_pct?: number | null;
  primary_complaints: string[];
}

export interface PatientRecord {
  patient_id: string;
  name?: string | null;
  age?: number | null;
  cohort?: string | null;
  surveys: Survey[];
  gait_sessions: GaitSession[];
}

export async function fetchPatients(q?: string): Promise<PatientSummary[]> {
  return request<PatientSummary[]>(
    `/api/patients${q ? `?q=${encodeURIComponent(q)}` : ""}`
  );
}

export async function fetchPatient(id: string): Promise<PatientRecord> {
  return request<PatientRecord>(`/api/patients/${encodeURIComponent(id)}`);
}

export async function addPatientSession(
  id: string,
  body: {
    label: string;
    source: string;
    metrics: GaitMetrics;
    frames?: JointFrame[] | null;
  }
): Promise<PatientRecord> {
  return request<PatientRecord>(
    `/api/patients/${encodeURIComponent(id)}/sessions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
}

export async function generateSynthesis(
  id: string
): Promise<SummaryResponse> {
  return request<SummaryResponse>(
    `/api/patients/${encodeURIComponent(id)}/synthesis`,
    { method: "POST" }
  );
}

export async function generateSummary(
  patientId?: string
): Promise<SummaryResponse> {
  return request<SummaryResponse>("/api/generate-summary", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patientId ? { patient_id: patientId } : {}),
  });
}

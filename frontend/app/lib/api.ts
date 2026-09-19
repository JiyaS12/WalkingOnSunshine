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

export async function generateSummary(
  patientId?: string
): Promise<SummaryResponse> {
  return request<SummaryResponse>("/api/generate-summary", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patientId ? { patient_id: patientId } : {}),
  });
}

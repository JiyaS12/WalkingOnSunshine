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

export interface VideoAnalysis {
  metrics: GaitMetrics;
  frames: JointFrame[];
  fps: number;
  frames_processed: number;
  frames_total: number;
  filename: string;
}

export async function processVideo(
  file: File,
  signal?: AbortSignal
): Promise<VideoAnalysis> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/api/process-video`, {
    method: "POST",
    body: form,
    signal,
  });
  if (!res.ok) {
    let detail = `API /api/process-video failed: ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* keep generic detail */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<VideoAnalysis>;
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

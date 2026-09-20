import type { CallRecord, CallStart, ConditionCategory, ConditionSurvey, WalkingEvent, WalkingView } from "./integration";

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
  /** share of frames where a dropped landmark had to be interpolated */
  dropped_frame_pct: number;
}

export type JointFrame = Record<string, number[]>;

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export interface SummaryResponse {
  summary: string;
  source: "openai" | "template";
  cached: boolean;
  estimated_tokens_saved: number;
}

export interface SummaryCacheStats {
  entries: number;
  cache_hits: number;
  estimated_tokens_saved: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = `API ${path} failed: ${res.status}`;
    let code: string | undefined;
    try {
      const body = (await res.json()) as {
        detail?: string | { code?: string; message?: string };
      };
      if (typeof body.detail === "string") detail = body.detail;
      if (body.detail && typeof body.detail === "object") {
        if (body.detail.message) detail = body.detail.message;
        code = body.detail.code;
      }
    } catch {
      /* keep generic detail */
    }
    throw new ApiError(detail || res.statusText, res.status, code);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export interface ClinicianSession {
  authenticated: true;
  username: string;
  expires_at: number;
}

export async function getClinicianSession(): Promise<ClinicianSession> {
  return request<ClinicianSession>("/api/clinician/session");
}

export async function signInClinician(
  username: string,
  password: string
): Promise<ClinicianSession> {
  return request<ClinicianSession>("/api/clinician/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export async function signOutClinician(): Promise<void> {
  return request<void>("/api/clinician/session", { method: "DELETE" });
}

export async function fetchSummaryCacheStats(): Promise<SummaryCacheStats> {
  return request<SummaryCacheStats>("/api/summary-cache-stats");
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
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep generic detail */
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<VideoAnalysis>;
}

export interface Survey {
  patient_id: string;
  patient_name?: string | null;
  submission_kind?: "manual" | "integrated";
  survey_id?: string;
  condition_survey?: ConditionSurvey | null;
  pain_scale?: number | null;
  fall_history?: {
    falls_last_6_months: number | null;
    injured?: boolean | null;
    last_fall_description?: string | null;
  } | null;
  dizziness?: boolean | null;
  dizziness_notes?: string | null;
  primary_complaints?: string[] | null;
  call_id?: string | null;
  recorded_at?: string | null;
}

export interface GaitSession {
  session_id?: string;
  call_id?: string | null;
  attempt_id?: string | null;
  label: string;
  idempotency_key?: string | null;
  recorded_at?: string | null;
  source: string;
  metrics: GaitMetrics;
  frames_ref?: string | null;
  frames?: JointFrame[] | null;
}

export interface PatientSummary {
  condition_category?: ConditionCategory | null;
  active_call_id?: string | null;
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
  condition_category?: ConditionCategory | null;
  condition_source?: "clinician" | null;
  active_call_id?: string | null;
  calls?: CallRecord[];
  patient_id: string;
  name?: string | null;
  age?: number | null;
  cohort?: string | null;
  surveys: Survey[];
  gait_sessions: GaitSession[];
}

/** Minimum record exposed by a signed patient link. */
export interface PatientAccessRecord {
  patient_id: string;
  name?: string | null;
  gait_sessions: GaitSession[];
}

export interface PatientSessionInput {
  call_id?: string | null;
  attempt_id?: string | null;
  label: string;
  source: "live" | "upload";
  idempotency_key: string;
  metrics: GaitMetrics;
  frames?: JointFrame[] | null;
}

function boundedSignal(signal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(15_000);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}

export function fetchPatientWalking(id: string, token: string, signal?: AbortSignal) {
  return patientAccessRequest<{ walking: WalkingView | null }>(
    `/api/patient-access/${encodeURIComponent(id)}/walking`, token,
    { signal: boundedSignal(signal) }
  );
}

export function publishWalkingEvent(id: string, token: string, body: WalkingEvent, signal?: AbortSignal) {
  return patientAccessRequest<{ walking: WalkingView }>(
    `/api/patient-access/${encodeURIComponent(id)}/walking/events`, token,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: boundedSignal(signal) }
  );
}

export function setPatientCondition(id: string, condition_category: ConditionCategory, signal?: AbortSignal) {
  return request<{ patient_id: string; condition_category: ConditionCategory; condition_source: "clinician" }>(
    `/api/patients/${encodeURIComponent(id)}/condition`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ condition_category }), signal: boundedSignal(signal) }
  );
}

export function fetchPatientCalls(id: string, signal?: AbortSignal) {
  return request<{ calls: CallRecord[] }>(`/api/patients/${encodeURIComponent(id)}/calls`, { signal: boundedSignal(signal) });
}

export function startPatientCall(id: string, body: CallStart, signal?: AbortSignal) {
  return request<{ call: CallRecord; replayed: boolean }>(`/api/patients/${encodeURIComponent(id)}/calls`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: boundedSignal(signal),
  });
}

export function refreshPatientCall(id: string, callId: string, signal?: AbortSignal) {
  return request<{ call: CallRecord; phone_available: boolean }>(
    `/api/patients/${encodeURIComponent(id)}/calls/${encodeURIComponent(callId)}/refresh`,
    { method: "POST", signal: boundedSignal(signal) }
  );
}

export function retryPatientSMS(id: string, callId: string, request_id: string, signal?: AbortSignal) {
  return request<{ call: CallRecord; replayed: boolean }>(
    `/api/patients/${encodeURIComponent(id)}/calls/${encodeURIComponent(callId)}/sms-retries`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request_id }), signal: boundedSignal(signal) }
  );
}

async function patientAccessRequest<T>(
  path: string,
  token: string,
  init?: RequestInit
): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    cache: "no-store",
    credentials: API_URL === "" ? "same-origin" : "omit",
    referrerPolicy: "no-referrer",
  });
  if (!res.ok) {
    let detail = "Patient access request failed";
    try {
      const body = (await res.json()) as { detail?: string };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep the non-sensitive fallback */
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export async function fetchPatientAccess(
  id: string,
  token: string,
  signal?: AbortSignal
): Promise<PatientAccessRecord> {
  return patientAccessRequest<PatientAccessRecord>(
    `/api/patient-access/${encodeURIComponent(id)}`,
    token,
    { signal: boundedSignal(signal) }
  );
}

export async function addPatientAccessSession(
  id: string,
  token: string,
  body: PatientSessionInput,
  signal?: AbortSignal
): Promise<PatientAccessRecord> {
  return patientAccessRequest<PatientAccessRecord>(
    `/api/patient-access/${encodeURIComponent(id)}/sessions`,
    token,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: boundedSignal(signal),
    }
  );
}

export async function fetchPatients(q?: string): Promise<PatientSummary[]> {
  return request<PatientSummary[]>(
    `/api/patients${q ? `?q=${encodeURIComponent(q)}` : ""}`
  );
}

export async function fetchPatient(id: string): Promise<PatientRecord> {
  return request<PatientRecord>(`/api/patients/${encodeURIComponent(id)}`);
}

export interface PatientLink {
  patient_id: string;
  patient_url: string;
  patient_access_expires_at: string;
}

/** Mint the signed magic link a clinician can hand to the patient. */
export async function createPatientLink(id: string): Promise<PatientLink> {
  return request<PatientLink>(
    `/api/patients/${encodeURIComponent(id)}/link`,
    { method: "POST" }
  );
}

/** The patient-scoped view returned by the signed-link / magic-link APIs. */
export interface PatientView {
  patient_id: string;
  name?: string | null;
  gait_sessions: GaitSession[];
}

export interface PatientSession {
  authenticated: true;
  patient_id: string;
  expires_at: number;
}

/** Exchange a magic-link token for an HttpOnly patient session cookie. */
export async function verifyPatientToken(
  id: string,
  token: string
): Promise<PatientSession> {
  const q = new URLSearchParams({ patient_id: id, token });
  return request<PatientSession>(`/api/auth/verify?${q.toString()}`);
}

export async function fetchPatientView(id: string): Promise<PatientView> {
  return request<PatientView>(
    `/api/patient-access/${encodeURIComponent(id)}`
  );
}

export async function generatePatientViewSummary(
  id: string
): Promise<SummaryResponse> {
  return request<SummaryResponse>(
    `/api/patient-access/${encodeURIComponent(id)}/summary`,
    { method: "POST" }
  );
}

export async function addPatientViewSession(
  id: string,
  body: {
    label: string;
    source: string;
    metrics: GaitMetrics;
    frames?: JointFrame[] | null;
  }
): Promise<PatientView> {
  return request<PatientView>(
    `/api/patient-access/${encodeURIComponent(id)}/sessions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );
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

export async function ensureDemoPatient(
  id: string
): Promise<{ created: boolean; patient: PatientRecord }> {
  return request<{ created: boolean; patient: PatientRecord }>(
    `/api/patients/${encodeURIComponent(id)}/ensure-demo`,
    { method: "POST" }
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
  patientId: string
): Promise<SummaryResponse> {
  return request<SummaryResponse>("/api/generate-summary", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ patient_id: patientId }),
  });
}

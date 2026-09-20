export type ConditionCategory = "orthopedic" | "stroke";
export type AnswerOption = "none" | "mild" | "moderate" | "severe" | "extreme";
export type CallStatus = "dispatching" | "unknown" | "dialing" | "in_progress" | "completed" | "failed" | "stopped";
export type SurveyStatus = "pending" | "in_progress" | "stored" | "stopped" | "needs_review";
export type SMSStatus = "not_requested" | "sending" | "unknown" | "sent" | "delivered" | "failed";
export type WalkingStatus = "waiting" | "page_ready" | "calibrating" | "ready" | "capturing" | "captured" | "saved" | "stopped";
export type WalkEventName = "page_ready" | "permission_denied" | "calibration_started" | "calibration_completed" | "capture_started" | "capture_completed" | "recoverable_error" | "stopped";
export type WalkError = "permission_denied" | "camera_unavailable" | "tracking_lost" | "network_error" | "save_failed" | "unsupported_browser";
export type PhoneError = "provider_rejected" | "provider_unavailable" | "timeout" | "busy" | "no_answer" | "disconnected" | "stopped" | "needs_review";

export interface ConfirmedAnswer {
  question_id: string;
  normalized_value: AnswerOption;
  confirmed: true;
  acceptance_method: "explicit_selection" | "confirmation";
  confirmed_at?: string | null;
  confidence?: number | null;
  clarification_attempts?: number;
}

export interface ConditionSurvey {
  instrument: "hoos_jr" | "stroke_mobility";
  version: "1";
  condition_category: ConditionCategory;
  answers: ConfirmedAnswer[];
  skipped?: string[];
  needs_human_review?: boolean;
  unanswered_questions?: {
    question_id: string;
    reason: "clarification_limit" | "no_response";
    clarification_attempts: number;
  }[];
  transcript?: {
    speaker: "assistant" | "patient" | "system";
    text: string;
    question_id: string | null;
    recorded_at: string;
  }[];
}

export interface CallStart {
  request_id: string;
  to_number: string;
  condition_category?: ConditionCategory | null;
}

export interface WalkingEvent {
  event_id: string;
  call_id: string;
  attempt_id: string;
  sequence: number;
  event: WalkEventName;
  error_code?: WalkError | null;
}

export interface WalkingState {
  status: WalkingStatus;
  last_sequence: number;
  last_event: WalkEventName | null;
  session_id: string | null;
  events: (WalkingEvent & { received_at: string })[];
}

export interface WalkingView extends Omit<WalkingState, "events"> {
  call_id: string;
  attempt_id: string;
  version: number;
  survey_status: SurveyStatus;
}

export interface CallRecord {
  call_id: string;
  patient_id: string;
  request_id: string;
  attempt_id: string;
  condition_category: ConditionCategory;
  call_status: CallStatus;
  survey_status: SurveyStatus;
  survey_id: string | null;
  needs_human_review?: boolean;
  sms_status: SMSStatus;
  sms_attempt: number;
  sms_retries: { request_id: string; sms_attempt: number; created_at: string }[];
  provider_call_id: string | null;
  phone_session_id: string | null;
  message_id: string | null;
  error_code: PhoneError | null;
  version: number;
  phone_version: number;
  created_at: string;
  updated_at: string;
  walking: WalkingState;
}

export const unresolvedCall = (call: CallRecord) =>
  ["dispatching", "unknown", "dialing", "in_progress"].includes(call.call_status);

export function mergeCalls(current: CallRecord[], incoming: CallRecord[]): CallRecord[] {
  const calls = new Map(current.map((call) => [call.call_id, call]));
  for (const call of incoming) {
    const previous = calls.get(call.call_id);
    if (!previous || call.version >= previous.version) calls.set(call.call_id, call);
  }
  return [...calls.values()].sort((a, b) => a.created_at.localeCompare(b.created_at));
}

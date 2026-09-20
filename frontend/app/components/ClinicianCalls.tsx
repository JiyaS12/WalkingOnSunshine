"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchPatientCalls, refreshPatientCall, retryPatientSMS, setPatientCondition, startPatientCall, type PatientRecord } from "../lib/api";
import { mergeCalls, unresolvedCall, type CallRecord, type CallStart, type ConditionCategory } from "../lib/integration";

interface Props {
  patient: PatientRecord;
  onAuthFailure: (error: unknown) => boolean;
}

const controlClass = "min-w-0 max-w-full rounded-2xl border-0 bg-muted px-3 py-2 text-sm text-foreground shadow-pillow-sm disabled:opacity-50";

export default function ClinicianCalls({ patient, onAuthFailure }: Props) {
  const [calls, setCalls] = useState(patient.calls ?? []);
  const [condition, setCondition] = useState<ConditionCategory | "">(patient.condition_category ?? "");
  const [confirmedCondition, setConfirmedCondition] = useState(patient.condition_category ?? null);
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingCall, setPendingCall] = useState(false);
  const [pendingSMS, setPendingSMS] = useState<{ callId: string; requestId: string } | null>(null);
  const callBody = useRef<CallStart | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const actionRef = useRef(false);
  const alive = useRef(false);
  const authFailureRef = useRef(onAuthFailure);
  authFailureRef.current = onAuthFailure;

  const acceptCalls = useCallback((incoming: CallRecord[]) => {
    const scoped = incoming.filter((call) => call.patient_id === patient.patient_id);
    setCalls((current) => mergeCalls(current, scoped));
    if (callBody.current && scoped.some((call) => call.request_id === callBody.current?.request_id)) {
      callBody.current = null;
      setPendingCall(false);
    }
    setPendingSMS((pending) => pending && scoped.some((call) =>
      call.call_id === pending.callId && call.sms_retries.some((retry) => retry.request_id === pending.requestId)
    ) ? null : pending);
  }, [patient.patient_id]);

  useEffect(() => {
    acceptCalls(patient.calls ?? []);
  }, [acceptCalls, patient.calls]);

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    controllerRef.current = controller;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await fetchPatientCalls(patient.patient_id, controller.signal);
        if (controller.signal.aborted) return;
        acceptCalls(result.calls);
      } catch (err) {
        if (controller.signal.aborted) return;
        if (authFailureRef.current(err)) return;
        setNotice("Call status could not be updated. Last known statuses are shown.");
      }
      if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 5000);
    };
    void poll();
    return () => {
      alive.current = false;
      controller.abort();
      clearTimeout(timer);
      callBody.current = null;
    };
  }, [acceptCalls, patient.patient_id]);

  const run = async (operation: (signal: AbortSignal) => Promise<void>) => {
    if (actionRef.current || !controllerRef.current) return;
    actionRef.current = true;
    setBusy(true);
    setError(null);
    setNotice(null);
    const signal = controllerRef.current.signal;
    try {
      await operation(signal);
    } catch (err) {
      if (signal.aborted || !alive.current) return;
      if (!onAuthFailure(err)) {
        setError(err instanceof ApiError && err.status === 503
          ? "Call service is unavailable. Patient walking and saved records remain available."
          : err instanceof ApiError && err.status === 409
            ? "This action conflicts with the current record. Reconcile status before another action."
            : "The result is not confirmed. Check status or retry the same request; do not start another call.");
      }
    } finally {
      actionRef.current = false;
      if (!signal.aborted && alive.current) setBusy(false);
    }
  };

  const sendCall = () => void run(async (signal) => {
    const body = callBody.current;
    if (!body) return;
    const result = await startPatientCall(patient.patient_id, body, signal);
    if (signal.aborted) return;
    acceptCalls([result.call]);
    setConfirmedCondition(result.call.condition_category);
    callBody.current = null;
    setPendingCall(false);
  });

  const startCall = (event: FormEvent) => {
    event.preventDefault();
    if (actionRef.current || callBody.current || !condition || !/^\+[1-9]\d{7,14}$/.test(phone.trim())) return;
    callBody.current = { request_id: crypto.randomUUID(), to_number: phone.trim(), condition_category: condition };
    setPendingCall(true);
    setPhone("");
    sendCall();
  };

  const sendSMS = (call: CallRecord) => {
    const receipt = pendingSMS ?? { callId: call.call_id, requestId: crypto.randomUUID() };
    setPendingSMS(receipt);
    void run(async (signal) => {
      const result = await retryPatientSMS(patient.patient_id, receipt.callId, receipt.requestId, signal);
      if (signal.aborted) return;
      acceptCalls([result.call]);
      setPendingSMS(null);
    });
  };

  const latestCall = calls.at(-1);
  const blocked = calls.some(unresolvedCall) || pendingCall;

  return (
    <section aria-label="Clinician call controls" className="mb-4 space-y-3 rounded-3xl bg-card p-4 shadow-pillow-sm">
      <h3 className="font-semibold">Voice follow-up</h3>
      <p className="text-xs text-muted-foreground">Select the patient&apos;s confirmed condition. Do not infer it from complaints or demographics.</p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="grid min-w-0 max-w-full gap-1 text-xs">
          Condition
          <select aria-label="Condition" className={controlClass} value={condition} disabled={busy || blocked}
            onChange={(event) => setCondition(event.target.value as ConditionCategory | "")}>
            <option value="">Clinician selection required</option>
            <option value="orthopedic">Orthopedic — HOOS JR</option>
            <option value="stroke">Stroke mobility</option>
          </select>
        </label>
        <button className={controlClass} disabled={!condition || condition === confirmedCondition || busy || blocked}
          onClick={() => void run(async (signal) => {
            if (!condition) return;
            const result = await setPatientCondition(patient.patient_id, condition, signal);
            if (!signal.aborted) {
              setConfirmedCondition(result.condition_category);
              setNotice("Condition saved by clinician.");
            }
          })}>Save condition</button>
      </div>
      <form onSubmit={startCall} className="flex flex-wrap items-end gap-2">
        <label className="grid min-w-0 max-w-full gap-1 text-xs">
          Destination phone (E.164)
          <input aria-label="Destination phone (E.164)" type="tel" autoComplete="off" value={phone}
            onChange={(event) => setPhone(event.target.value)} required pattern="\+[1-9][0-9]{7,14}" maxLength={16}
            disabled={busy || blocked} className={controlClass} placeholder="+15555550123" />
        </label>
        <button className={controlClass} disabled={busy || blocked || !condition || !/^\+[1-9]\d{7,14}$/.test(phone.trim())}>
          Start call
        </button>
      </form>
      {pendingCall && <button className={controlClass} disabled={busy} onClick={sendCall}>Retry same call request</button>}
      {blocked && <p className="text-xs text-foreground">An unresolved request blocks another call. Reconcile status; an unknown outcome does not mean the call failed.</p>}
      {error && <p role="alert" className="text-sm text-foreground">{error}</p>}
      {notice && <p role="status" className="text-xs text-foreground">{notice}</p>}
      {!calls.length && <p className="text-xs text-muted-foreground">No calls on file.</p>}
      <ol className="space-y-3">
        {[...calls].reverse().map((call) => {
          const canRetrySMS = call.call_id === latestCall?.call_id && call.survey_status === "stored" &&
            call.sms_status === "failed" && call.sms_attempt < 5;
          return (
            <li key={call.call_id} className="space-y-2 break-words rounded-2xl bg-muted p-3 text-xs shadow-pillow-inset">
              <p>{call.created_at} · {call.condition_category} · Call {call.call_id} · Attempt {call.attempt_id}</p>
              <p>Call: {call.call_status} · Survey: {call.survey_status}{call.survey_skipped?.length ? ` (needs review: ${call.survey_skipped.length} unanswered)` : ""} · SMS: {call.sms_status} (attempt {call.sms_attempt}) · Walk: {call.walking.status}</p>
              {call.needs_human_review && <p className="font-semibold text-amber-800">Survey needs human review — unanswered items were not scored.</p>}
              {call.error_code && <p>Service reason: {call.error_code}</p>}
              <p>Survey: {call.survey_id ?? "Not stored"} · Gait session: {call.walking.session_id ?? "Not saved"}</p>
              <button className={controlClass} disabled={busy} onClick={() => void run(async (signal) => {
                const result = await refreshPatientCall(patient.patient_id, call.call_id, signal);
                if (signal.aborted) return;
                acceptCalls([result.call]);
                if (!result.phone_available) setNotice("Phone service unavailable; last known status retained. No call or SMS was retried.");
              })}>Reconcile call status</button>
              {(canRetrySMS || pendingSMS?.callId === call.call_id) && (
                <button className={`${controlClass} ml-2`} disabled={busy || (pendingSMS !== null && pendingSMS.callId !== call.call_id)}
                  onClick={() => sendSMS(call)}>{pendingSMS?.callId === call.call_id ? "Retry same SMS request" : "Retry failed SMS"}</button>
              )}
              {call.sms_retries.length > 0 && <p>SMS retry receipts: {call.sms_retries.map((receipt) => `#${receipt.sms_attempt} ${receipt.created_at}`).join("; ")}</p>}
              {call.walking.events.length > 0 && (
                <details>
                  <summary>Walking events ({call.walking.events.length})</summary>
                  <ol>{call.walking.events.map((event) => <li key={event.event_id}>
                    {event.received_at} · {event.event}{event.error_code ? ` · ${event.error_code}` : ""}
                  </li>)}</ol>
                </details>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

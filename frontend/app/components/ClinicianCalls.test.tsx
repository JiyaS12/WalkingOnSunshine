import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ClinicianCalls from "./ClinicianCalls";
import { ApiError, fetchPatientCalls, refreshPatientCall, retryPatientSMS, setPatientCondition, startPatientCall } from "../lib/api";
import { callRecord, deferred, patientRecord } from "../lib/integration.test-support";

vi.mock("../lib/api", async () => ({
  ...await vi.importActual<typeof import("../lib/api")>("../lib/api"),
  fetchPatientCalls: vi.fn(), refreshPatientCall: vi.fn(), retryPatientSMS: vi.fn(),
  setPatientCondition: vi.fn(), startPatientCall: vi.fn(),
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(fetchPatientCalls).mockResolvedValue({ calls: [] });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("clinician call controls", () => {
  it("requires an explicit condition, clears the phone, and reuses the entire ambiguous request", async () => {
    vi.mocked(startPatientCall).mockRejectedValueOnce(new TypeError("lost response"))
      .mockResolvedValueOnce({ call: callRecord({ call_status: "unknown" }), replayed: true });
    vi.mocked(setPatientCondition).mockResolvedValue({ patient_id: "patient-1", condition_category: "stroke", condition_source: "clinician" });
    render(<ClinicianCalls patient={patientRecord()} onAuthFailure={() => false} />);
    fireEvent.change(screen.getByLabelText("Destination phone (E.164)"), { target: { value: "+15555550123" } });
    expect(screen.getByRole("button", { name: "Start call" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Condition"), { target: { value: "stroke" } });
    fireEvent.click(screen.getByRole("button", { name: "Save condition" }));
    await screen.findByText("Condition saved by clinician.");
    expect(setPatientCondition).toHaveBeenCalledWith("patient-1", "stroke", expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: "Start call" }));
    expect(screen.getByLabelText("Destination phone (E.164)")).toHaveValue("");
    await screen.findByRole("alert");
    const first = vi.mocked(startPatientCall).mock.calls[0][1];
    expect(first).toEqual({ to_number: "+15555550123", condition_category: "stroke", request_id: expect.stringMatching(/^[\w-]{16,}$/) });
    fireEvent.click(screen.getByRole("button", { name: "Retry same call request" }));
    await waitFor(() => expect(startPatientCall).toHaveBeenCalledTimes(2));
    expect(vi.mocked(startPatientCall).mock.calls[1][1]).toEqual(first);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Retry same call request" })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Start call" })).toBeDisabled();
  });

  it("reconciles unknown status without redialing or regressing a newer record", async () => {
    const newer = callRecord({ version: 7, call_status: "unknown", sms_status: "unknown" });
    vi.mocked(fetchPatientCalls).mockResolvedValue({ calls: [callRecord({ version: 6, call_status: "dialing" })] });
    vi.mocked(refreshPatientCall).mockResolvedValue({ call: newer, phone_available: false });
    render(<ClinicianCalls patient={patientRecord({ condition_category: "orthopedic", calls: [newer] })} onAuthFailure={() => false} />);
    await waitFor(() => expect(fetchPatientCalls).toHaveBeenCalled());
    expect(screen.getByText(/Call: unknown/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry.*SMS/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reconcile call status" }));
    await screen.findByText(/Phone service unavailable/);
    expect(refreshPatientCall).toHaveBeenCalledWith("patient-1", "call-1", expect.any(AbortSignal));
    expect(startPatientCall).not.toHaveBeenCalled();
    expect(retryPatientSMS).not.toHaveBeenCalled();
  });

  it.each(["unknown", "sending", "sent", "delivered", "not_requested"] as const)("does not offer a new SMS retry for %s", async (sms_status) => {
    render(<ClinicianCalls patient={patientRecord({ calls: [callRecord({ sms_status })] })} onAuthFailure={() => false} />);
    await act(async () => {});
    expect(screen.queryByRole("button", { name: /Retry.*SMS/ })).not.toBeInTheDocument();
  });

  it("retries failed SMS with a stable receipt and clears it when polling confirms acceptance", async () => {
    vi.useFakeTimers();
    vi.mocked(retryPatientSMS).mockRejectedValue(new TypeError("lost receipt"));
    render(<ClinicianCalls patient={patientRecord({ calls: [callRecord({ sms_status: "failed" })] })} onAuthFailure={() => false} />);
    await act(async () => {});
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Retry failed SMS" })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Retry same SMS request" })));
    const requestId = vi.mocked(retryPatientSMS).mock.calls[0][2];
    expect(vi.mocked(retryPatientSMS).mock.calls[1][2]).toBe(requestId);
    vi.mocked(fetchPatientCalls).mockResolvedValue({ calls: [callRecord({ version: 4, sms_status: "unknown", sms_attempt: 2,
      sms_retries: [{ request_id: requestId, sms_attempt: 2, created_at: "2026-09-20T00:01:00Z" }] })] });
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(screen.queryByRole("button", { name: /Retry.*SMS/ })).not.toBeInTheDocument();
    expect(retryPatientSMS).toHaveBeenCalledTimes(2);
  });

  it("stops polling on authentication expiry and aborts on unmount", async () => {
    vi.useFakeTimers();
    const error = new ApiError("Expired", 401, "session_expired");
    vi.mocked(fetchPatientCalls).mockRejectedValue(error);
    const onAuthFailure = vi.fn(() => true);
    const view = render(<ClinicianCalls patient={patientRecord()} onAuthFailure={onAuthFailure} />);
    await act(async () => {});
    expect(onAuthFailure).toHaveBeenCalledWith(error);
    await act(async () => vi.advanceTimersByTimeAsync(15_000));
    expect(fetchPatientCalls).toHaveBeenCalledTimes(1);
    const signal = vi.mocked(fetchPatientCalls).mock.calls[0][1];
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("ignores late responses and auth failures from an unmounted patient", async () => {
    const response = deferred<Awaited<ReturnType<typeof fetchPatientCalls>>>();
    vi.mocked(fetchPatientCalls).mockReturnValueOnce(response.promise);
    const onAuthFailure = vi.fn(() => true);
    const view = render(<ClinicianCalls key="one" patient={patientRecord()} onAuthFailure={onAuthFailure} />);
    view.rerender(<ClinicianCalls key="two" patient={patientRecord({ patient_id: "patient-2" })} onAuthFailure={onAuthFailure} />);
    await act(async () => response.reject(new ApiError("Expired old request", 401)));
    expect(onAuthFailure).not.toHaveBeenCalled();
    expect(screen.getByText("No calls on file.")).toBeInTheDocument();
  });
});

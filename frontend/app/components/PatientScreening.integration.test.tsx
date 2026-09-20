import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PatientScreening from "./PatientScreening";
import type WebcamFeed from "./WebcamFeed";
import { addPatientAccessSession, ApiError, fetchPatientAccess, fetchPatientWalking, publishWalkingEvent, type PatientAccessRecord } from "../lib/api";
import { deferred, metrics, patientRecord, walkingView } from "../lib/integration.test-support";

const harness = vi.hoisted(() => ({ props: null as ComponentProps<typeof WebcamFeed> | null }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(window.location.search) }));
vi.mock("./WebcamFeed", () => ({
  default: (props: ComponentProps<typeof WebcamFeed>) => { harness.props = props; return <div data-testid="capture" />; },
}));
vi.mock("./TrendGraph", () => ({ default: () => null }));
vi.mock("../lib/api", async () => ({
  ...await vi.importActual<typeof import("../lib/api")>("../lib/api"),
  fetchPatientAccess: vi.fn(), fetchPatientWalking: vi.fn(), publishWalkingEvent: vi.fn(), addPatientAccessSession: vi.fn(),
}));

beforeEach(() => {
  vi.resetAllMocks();
  harness.props = null;
  window.history.replaceState({}, "", "/patient/patient-1?token=signed.payload");
  vi.mocked(fetchPatientAccess).mockResolvedValue(patientRecord());
  vi.mocked(fetchPatientWalking).mockResolvedValue({ walking: walkingView() });
  vi.mocked(publishWalkingEvent).mockImplementation(async (_pid, _token, event) => ({
    walking: walkingView({ call_id: event.call_id, attempt_id: event.attempt_id, version: event.sequence + 3, last_sequence: event.sequence, last_event: event.event, status: event.event === "stopped" ? "stopped" : event.event === "capture_completed" ? "captured" : "page_ready" }),
  }));
  vi.mocked(addPatientAccessSession).mockImplementation(async (_pid, _token, body) => ({
    ...patientRecord(),
    gait_sessions: [{ ...body, session_id: "session-1" }],
  }));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  window.history.replaceState({}, "", "/");
});

describe("walking lifecycle integration", () => {
  it.each(["live", "upload"] as const)("correlates %s capture and only confirms completion after session persistence", async (source) => {
    const saved = deferred<PatientAccessRecord>();
    vi.mocked(addPatientAccessSession).mockReturnValue(saved.promise);
    render(<PatientScreening patientId="patient-1" />);
    await screen.findByTestId("capture");
    expect(window.location.search).toBe("");
    expect(vi.mocked(publishWalkingEvent).mock.calls.map((call) => call[2].event)).toEqual(["page_ready"]);
    act(() => {
      harness.props?.onLifecycle?.("calibration_started");
      harness.props?.onLifecycle?.("calibration_completed");
      harness.props?.onLifecycle?.("capture_started");
      harness.props?.onLifecycle?.("capture_completed");
      harness.props?.onMetrics(metrics, source, []);
    });
    if (source === "live") fireEvent.click(screen.getByRole("button", { name: "Save this walk" }));
    await waitFor(() => expect(addPatientAccessSession).toHaveBeenCalledOnce());
    expect(screen.queryByText("Walk saved successfully.")).not.toBeInTheDocument();
    const body = vi.mocked(addPatientAccessSession).mock.calls[0][2];
    expect(body).toMatchObject({ call_id: "call-1", attempt_id: "attempt-1", source, metrics });
    await act(async () => saved.resolve({ ...patientRecord(), gait_sessions: [{ ...body, session_id: "session-1" }] }));
    expect(await screen.findByText("Walk saved successfully.")).toBeInTheDocument();
    expect(screen.getByText(/Walking status: Saved to your care team/)).toBeInTheDocument();
    expect(screen.queryByTestId("capture")).not.toBeInTheDocument();
    expect(vi.mocked(publishWalkingEvent).mock.calls.some((call) => call[2].event === "capture_completed")).toBe(true);
  });

  it("preserves the exact save body across ambiguous retries and rejects uncorrelated receipts", async () => {
    vi.mocked(addPatientAccessSession).mockRejectedValueOnce(new TypeError("lost response"))
      .mockResolvedValueOnce(patientRecord());
    render(<PatientScreening patientId="patient-1" />);
    await screen.findByTestId("capture");
    act(() => harness.props?.onMetrics(metrics, "live", []));
    fireEvent.click(screen.getByRole("button", { name: "Save this walk" }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry save" }));
    await waitFor(() => expect(addPatientAccessSession).toHaveBeenCalledTimes(2));
    expect(vi.mocked(addPatientAccessSession).mock.calls[1][2]).toEqual(vi.mocked(addPatientAccessSession).mock.calls[0][2]);
    expect(screen.queryByText("Walk saved successfully.")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Retry save" })).toBeEnabled();
  });

  it("requires a stored survey without publishing camera progress", async () => {
    vi.mocked(fetchPatientWalking).mockResolvedValue({ walking: walkingView({ survey_status: "in_progress" }) });
    render(<PatientScreening patientId="patient-1" />);
    await screen.findByText(/Your survey must finish/);
    expect(screen.queryByTestId("capture")).not.toBeInTheDocument();
    expect(publishWalkingEvent).not.toHaveBeenCalled();
  });

  it("keeps upload analysis and legacy saving usable when walking status is unavailable", async () => {
    vi.mocked(fetchPatientWalking).mockRejectedValue(new ApiError("Unavailable", 503));
    render(<PatientScreening patientId="patient-1" />);
    await screen.findByTestId("capture");
    act(() => harness.props?.onMetrics(metrics, "upload", []));
    await screen.findByText("Walk saved successfully.");
    expect(vi.mocked(addPatientAccessSession).mock.calls[0][2].call_id).toBeUndefined();
    expect(publishWalkingEvent).not.toHaveBeenCalled();
  });

  it("handles an expired token from progress publishing without a clinician fallback", async () => {
    vi.mocked(publishWalkingEvent).mockRejectedValue(new ApiError("Expired", 401));
    render(<PatientScreening patientId="patient-1" />);
    expect(await screen.findByRole("heading", { name: "This link is no longer valid" })).toBeInTheDocument();
    expect(screen.queryByTestId("capture")).not.toBeInTheDocument();
    expect(addPatientAccessSession).not.toHaveBeenCalled();
  });

  it("rejects old callbacks and save errors after the patient route changes", async () => {
    const saved = deferred<PatientAccessRecord>();
    vi.mocked(addPatientAccessSession).mockReturnValue(saved.promise);
    const view = render(<PatientScreening patientId="patient-1" />);
    await screen.findByTestId("capture");
    const oldCapture = harness.props;
    act(() => oldCapture?.onMetrics(metrics, "upload", []));
    await waitFor(() => expect(addPatientAccessSession).toHaveBeenCalledOnce());
    vi.mocked(fetchPatientAccess).mockResolvedValue(patientRecord({ patient_id: "patient-2", name: "Patient Two" }));
    vi.mocked(fetchPatientWalking).mockResolvedValue({ walking: walkingView({ call_id: "call-2", attempt_id: "attempt-2" }) });
    window.history.replaceState({}, "", "/patient/patient-2?token=second.payload");
    view.rerender(<PatientScreening patientId="patient-2" />);
    await screen.findByText("Patient Two");
    const eventsBefore = vi.mocked(publishWalkingEvent).mock.calls.length;
    await act(async () => {
      oldCapture?.onLifecycle?.("recoverable_error", "camera_unavailable");
      oldCapture?.onMetrics(metrics, "upload", []);
      saved.reject(new ApiError("Old expired token", 401));
    });
    expect(screen.getByText("Patient Two")).toBeInTheDocument();
    expect(publishWalkingEvent).toHaveBeenCalledTimes(eventsBefore);
    expect(addPatientAccessSession).toHaveBeenCalledOnce();
  });

  it("does not reuse captured data after a new attempt and ignores stale callbacks after explicit reload", async () => {
    render(<PatientScreening patientId="patient-1" />);
    await screen.findByTestId("capture");
    const oldCapture = harness.props;
    act(() => oldCapture?.onMetrics(metrics, "live", []));
    vi.mocked(fetchPatientWalking).mockResolvedValue({ walking: walkingView({ call_id: "call-2", attempt_id: "attempt-2" }) });
    fireEvent.click(screen.getByRole("button", { name: "Save this walk" }));
    fireEvent.click(await screen.findByRole("button", { name: "Load current assessment" }));
    await screen.findByTestId("capture");
    const count = vi.mocked(publishWalkingEvent).mock.calls.length;
    act(() => {
      oldCapture?.onLifecycle?.("recoverable_error", "camera_unavailable");
      oldCapture?.onMetrics(metrics, "upload", []);
    });
    expect(publishWalkingEvent).toHaveBeenCalledTimes(count);
    expect(addPatientAccessSession).not.toHaveBeenCalled();
  });

  it("reports an explicit stop and disables further capture without marking it saved", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<PatientScreening patientId="patient-1" />);
    await screen.findByTestId("capture");
    fireEvent.click(screen.getByRole("button", { name: "Stop assessment" }));
    await waitFor(() => expect(screen.queryByTestId("capture")).not.toBeInTheDocument());
    expect(vi.mocked(publishWalkingEvent).mock.calls.at(-1)?.[2].event).toBe("stopped");
    expect(screen.queryByText("Walk saved successfully.")).not.toBeInTheDocument();
    expect(addPatientAccessSession).not.toHaveBeenCalled();
  });
});

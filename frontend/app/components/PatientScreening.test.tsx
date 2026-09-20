import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PatientScreening from "./PatientScreening";
import type { GaitMetrics, JointFrame, PatientAccessRecord } from "../lib/api";
import { ApiError, addPatientAccessSession, fetchPatientAccess } from "../lib/api";

const apiMocks = vi.hoisted(() => ({
  fetchPatientAccess: vi.fn(),
  addPatientAccessSession: vi.fn(),
}));

const webcamHarness = vi.hoisted(() => ({ props: null as unknown }));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

vi.mock("./WebcamFeed", () => ({
  default: (props: unknown) => {
    webcamHarness.props = props;
    return <div data-testid="webcam-feed" />;
  },
}));

vi.mock("./TrendGraph", () => ({
  default: ({ sessions }: { sessions: Array<{ label: string }> }) => (
    <div data-testid="trend-graph">{sessions.map((session) => session.label).join(",")}</div>
  ),
}));

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    fetchPatientAccess: apiMocks.fetchPatientAccess,
    addPatientAccessSession: apiMocks.addPatientAccessSession,
  };
});

interface WebcamProps {
  onMetrics: (
    metrics: GaitMetrics,
    source: "live" | "upload",
    frames?: JointFrame[]
  ) => void;
}

const TOKEN = "signed.payload";

const walkingMetrics: GaitMetrics = {
  stride_length_m: 1.05,
  asymmetry_pct: 4.2,
  velocity_degradation_pct: 2.1,
  fall_risk_score: 0.22,
  cadence_steps_per_min: 102,
  frame_count: 90,
  leg_length_m: 0.9,
  stride_ratio: 1.17,
  knee_flexion_rom_deg: 42,
  peak_ankle_speed_mps: 3.1,
  gait_detected: true,
  dropped_frame_pct: 0,
};

const noWalkingMetrics: GaitMetrics = {
  ...walkingMetrics,
  cadence_steps_per_min: 0,
  gait_detected: false,
};

function patientRecord(
  patientId = "RGN-0417",
  name = "Demo Patient",
  gaitSessions: PatientAccessRecord["gait_sessions"] = []
): PatientAccessRecord {
  return { patient_id: patientId, name, gait_sessions: gaitSessions };
}

function openPatient(patientId: string, token: string | null = TOKEN) {
  const query = token === null ? "" : `?token=${encodeURIComponent(token)}`;
  window.history.replaceState({}, "", `/patient/${patientId}${query}`);
}

function webcamProps(): WebcamProps {
  return webcamHarness.props as WebcamProps;
}

beforeEach(() => {
  vi.clearAllMocks();
  webcamHarness.props = null;
  Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
  openPatient("RGN-0417");
  apiMocks.fetchPatientAccess.mockResolvedValue(patientRecord());
  apiMocks.addPatientAccessSession.mockResolvedValue(patientRecord());
});

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});

describe("signed patient screening", () => {
  it.each(["live", "upload"] as const)("excludes a rejected %s trial from saving and the trend", async (source) => {
    render(<PatientScreening patientId="RGN-0417" />);
    await screen.findByText("Demo Patient");
    act(() => webcamProps().onMetrics({
      ...walkingMetrics, cv_fall_risk_status: "not_scorable", cv_fall_risk_index: null,
    }, source, []));
    expect(screen.getByTestId("trend-graph")).toBeEmptyDOMElement();
    expect(screen.getByText("Retake needed")).toBeInTheDocument();
    expect(addPatientAccessSession).not.toHaveBeenCalled();
    if (source === "live") expect(screen.getByRole("button", { name: "Save this walk" })).toBeDisabled();
  });

  it("preserves historical metrics without experimental fields", async () => {
    render(<PatientScreening patientId="RGN-0417" />);
    await screen.findByText("Demo Patient");
    act(() => webcamProps().onMetrics(walkingMetrics, "live", []));
    expect(screen.getByText("Original Fall Risk — heuristic")).toBeInTheDocument();
    expect(screen.getByText("0.220")).toBeInTheDocument();
    expect(screen.getByText("Model unavailable")).toBeInTheDocument();
  });

  it("preserves experimental rejection reasons and learned contributions", async () => {
    render(<PatientScreening patientId="RGN-0417" />);
    await screen.findByText("Demo Patient");
    act(() => webcamProps().onMetrics({
      ...walkingMetrics, experimental_cv_risk_index: null,
      experimental_cv_risk_status: "not_scorable",
      experimental_cv_risk_warnings: ["lower-body landmark visibility is too low"],
    }, "live", []));
    expect(screen.getByText("Not scorable")).toBeInTheDocument();
    expect(screen.getByText("lower-body landmark visibility is too low")).toBeInTheDocument();
    act(() => webcamProps().onMetrics({
      ...walkingMetrics, experimental_cv_risk_index: 70.5,
      experimental_cv_risk_status: "scored_with_warning",
      experimental_cv_risk_warnings: ["outside reference range"],
      experimental_cv_risk_contributors: { median_step_time_s: 0.123 },
    }, "live", []));
    expect(screen.getByText("70.5 / 100")).toBeInTheDocument();
    expect(screen.getByText("outside reference range")).toBeInTheDocument();
    expect(screen.getByText("median step time s: 0.123")).toBeInTheDocument();
    expect(screen.queryByText("HIGH")).not.toBeInTheDocument();
  });

  it("requires the token query parameter and never falls back to another API", async () => {
    openPatient("RGN-0417", null);
    render(<PatientScreening patientId="RGN-0417" />);

    expect(await screen.findByRole("heading", { name: "Secure link required" })).toBeInTheDocument();
    expect(fetchPatientAccess).not.toHaveBeenCalled();
    expect(addPatientAccessSession).not.toHaveBeenCalled();
  });

  it("captures the token in memory, scrubs it from the URL, and loads only its patient", async () => {
    window.history.replaceState(
      { unsafeCopy: `/patient/RGN-0417?token=${TOKEN}` },
      "",
      `/patient/RGN-0417?keep=yes&token=${TOKEN}`
    );
    const localStorageSpy = vi.spyOn(Storage.prototype, "setItem");

    render(<PatientScreening patientId="RGN-0417" />);

    expect(await screen.findByText("Demo Patient")).toBeInTheDocument();
    expect(fetchPatientAccess).toHaveBeenCalledWith(
      "RGN-0417",
      TOKEN,
      expect.any(AbortSignal)
    );
    expect(window.location.search).toBe("?keep=yes");
    expect(window.history.state).toBeNull();
    expect(localStorageSpy).not.toHaveBeenCalled();
    expect(screen.queryByText(/Doctor's Portal/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/phone survey/i)).not.toBeInTheDocument();
  });

  it.each([401, 404])(
    "uses the same non-leaking invalid-link state for status %s",
    async (status) => {
      apiMocks.fetchPatientAccess.mockRejectedValue(
        new ApiError(status === 401 ? "invalid token" : "missing patient", status)
      );
      render(<PatientScreening patientId="RGN-0417" />);

      expect(
        await screen.findByRole("heading", { name: "This link is no longer valid" })
      ).toBeInTheDocument();
      expect(screen.queryByText(/missing patient|invalid token/i)).not.toBeInTheDocument();
    }
  );

  it("treats a mismatched response as an invalid link", async () => {
    apiMocks.fetchPatientAccess.mockResolvedValue(patientRecord("OTHER-1", "Other Patient"));
    render(<PatientScreening patientId="RGN-0417" />);

    expect(
      await screen.findByRole("heading", { name: "This link is no longer valid" })
    ).toBeInTheDocument();
    expect(screen.queryByText("Other Patient")).not.toBeInTheDocument();
  });

  it("offers a retry after an offline load", async () => {
    apiMocks.fetchPatientAccess
      .mockRejectedValueOnce(new TypeError("network unavailable"))
      .mockResolvedValueOnce(patientRecord());
    render(<PatientScreening patientId="RGN-0417" />);

    expect(
      await screen.findByRole("heading", { name: "You appear to be offline" })
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Demo Patient")).toBeInTheDocument();
    expect(fetchPatientAccess).toHaveBeenCalledTimes(2);
  });

  it("ignores late responses from a previous patient route", async () => {
    let resolveFirst!: (record: PatientAccessRecord) => void;
    let resolveSecond!: (record: PatientAccessRecord) => void;
    apiMocks.fetchPatientAccess
      .mockImplementationOnce(
        () => new Promise<PatientAccessRecord>((resolve) => (resolveFirst = resolve))
      )
      .mockImplementationOnce(
        () => new Promise<PatientAccessRecord>((resolve) => (resolveSecond = resolve))
      );

    const view = render(<PatientScreening patientId="RGN-0417" />);
    await waitFor(() => expect(fetchPatientAccess).toHaveBeenCalledTimes(1));

    openPatient("RGN-9999", "other.payload");
    view.rerender(<PatientScreening patientId="RGN-9999" />);
    await waitFor(() => expect(fetchPatientAccess).toHaveBeenCalledTimes(2));

    await act(async () => resolveSecond(patientRecord("RGN-9999", "Current Patient")));
    expect(await screen.findByText("Current Patient")).toBeInTheDocument();

    await act(async () => resolveFirst(patientRecord("RGN-0417", "Stale Patient")));
    expect(screen.queryByText("Stale Patient")).not.toBeInTheDocument();
    expect(screen.getByText("Current Patient")).toBeInTheDocument();
  });

  it("auto-saves one time per upload analysis and refreshes the timeline from the response", async () => {
    const savedSession: PatientAccessRecord["gait_sessions"][number] = {
      label: "Upload 12:00:00",
      source: "upload",
      metrics: walkingMetrics,
    };
    let resolveSave!: (record: PatientAccessRecord) => void;
    apiMocks.addPatientAccessSession.mockImplementation(
      () => new Promise<PatientAccessRecord>((resolve) => (resolveSave = resolve))
    );
    render(<PatientScreening patientId="RGN-0417" />);
    await screen.findByText("Demo Patient");

    act(() => {
      webcamProps().onMetrics(walkingMetrics, "upload", []);
      webcamProps().onMetrics(walkingMetrics, "upload", []);
    });
    await waitFor(() => expect(addPatientAccessSession).toHaveBeenCalledTimes(1));
    expect(addPatientAccessSession).toHaveBeenCalledWith(
      "RGN-0417",
      TOKEN,
      expect.objectContaining({ source: "upload", metrics: walkingMetrics }),
      expect.any(AbortSignal)
    );

    await act(async () => resolveSave(patientRecord("RGN-0417", "Demo Patient", [savedSession])));
    expect(await screen.findByText("Walk saved successfully.")).toBeInTheDocument();
    expect(screen.getByTestId("trend-graph")).toHaveTextContent("Upload 12:00:00");
  });

  it("prevents invalid and duplicate live submissions", async () => {
    let resolveSave!: (record: PatientAccessRecord) => void;
    apiMocks.addPatientAccessSession.mockImplementation(
      () => new Promise<PatientAccessRecord>((resolve) => (resolveSave = resolve))
    );
    render(<PatientScreening patientId="RGN-0417" />);
    await screen.findByText("Demo Patient");

    act(() => webcamProps().onMetrics(noWalkingMetrics, "live", []));
    expect(screen.getByRole("button", { name: "Save this walk" })).toBeDisabled();
    expect(addPatientAccessSession).not.toHaveBeenCalled();

    act(() => webcamProps().onMetrics(walkingMetrics, "live", []));
    const saveButton = screen.getByRole("button", { name: "Save this walk" });
    fireEvent.click(saveButton);
    fireEvent.click(saveButton);
    await waitFor(() => expect(addPatientAccessSession).toHaveBeenCalledTimes(1));

    await act(async () => resolveSave(patientRecord()));
    expect(await screen.findByRole("button", { name: "Saved" })).toBeDisabled();
  });

  it("reuses the same idempotency key after an ambiguous save failure", async () => {
    apiMocks.addPatientAccessSession
      .mockRejectedValueOnce(new TypeError("response lost"))
      .mockResolvedValueOnce(patientRecord());
    render(<PatientScreening patientId="RGN-0417" />);
    await screen.findByText("Demo Patient");

    act(() => webcamProps().onMetrics(walkingMetrics, "live", []));
    fireEvent.click(screen.getByRole("button", { name: "Save this walk" }));
    expect(await screen.findByRole("button", { name: "Retry save" })).toBeInTheDocument();

    const firstBody = apiMocks.addPatientAccessSession.mock.calls[0][2];
    fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
    await waitFor(() => expect(addPatientAccessSession).toHaveBeenCalledTimes(2));
    const secondBody = apiMocks.addPatientAccessSession.mock.calls[1][2];

    expect(firstBody.idempotency_key).toMatch(/^live-[A-Za-z0-9_-]{16,}$/);
    expect(secondBody.idempotency_key).toBe(firstBody.idempotency_key);
  });

  it("does not replace a newer timeline with an older concurrent response", async () => {
    const resolvers: Array<(record: PatientAccessRecord) => void> = [];
    apiMocks.addPatientAccessSession.mockImplementation(
      () => new Promise<PatientAccessRecord>((resolve) => resolvers.push(resolve))
    );
    render(<PatientScreening patientId="RGN-0417" />);
    await screen.findByText("Demo Patient");

    const firstMetrics = { ...walkingMetrics, stride_length_m: 1.01 };
    const secondMetrics = { ...walkingMetrics, stride_length_m: 1.02 };
    act(() => webcamProps().onMetrics(firstMetrics, "live", []));
    fireEvent.click(screen.getByRole("button", { name: "Save this walk" }));
    act(() => webcamProps().onMetrics(secondMetrics, "live", []));
    fireEvent.click(screen.getByRole("button", { name: "Save this walk" }));
    await waitFor(() => expect(addPatientAccessSession).toHaveBeenCalledTimes(2));

    const firstSession: PatientAccessRecord["gait_sessions"][number] = {
      label: "First",
      source: "live",
      metrics: firstMetrics,
    };
    const secondSession: PatientAccessRecord["gait_sessions"][number] = {
      label: "Second",
      source: "live",
      metrics: secondMetrics,
    };
    await act(async () => resolvers[1](patientRecord("RGN-0417", "Demo Patient", [firstSession, secondSession])));
    expect(screen.getByTestId("trend-graph")).toHaveTextContent("First,Second");

    await act(async () => resolvers[0](patientRecord("RGN-0417", "Demo Patient", [firstSession])));
    expect(screen.getByTestId("trend-graph")).toHaveTextContent("First,Second");
  });
});

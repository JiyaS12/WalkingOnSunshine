import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ClinicianDatabase from "./ClinicianDatabase";
import {
  ApiError,
  fetchDatabaseSnapshot,
  type DatabaseSnapshot,
} from "../lib/api";
import { callRecord, deferred, metrics } from "../lib/integration.test-support";

vi.mock("../lib/api", async () => ({
  ...(await vi.importActual("../lib/api")),
  fetchDatabaseSnapshot: vi.fn(),
}));
const data: DatabaseSnapshot = {
  source: "supabase",
  read_only: true,
  read_at: "2026-09-20T12:00:00Z",
  offset: 0,
  limit: 25,
  total: 1,
  totals: { patients: 1, calls: 1, surveys: 1, walking: 2 },
  patients: [
    {
      patient_id: "patient-1",
      name: "Demo Patient - Synthetic",
      calls: [{ ...callRecord(), destination_phone: "+15555550123" }],
      surveys: [{ patient_id: "patient-1", call_id: "call-1", pain_scale: 0 }],
      gait_sessions: [
        {
          session_id: "matching-walk",
          call_id: "call-1",
          attempt_id: "attempt-1",
          source: "live",
          label: "Test",
          metrics: { ...metrics, fall_risk_score: 0 },
          stored_frame_count: 0,
        },
        {
          session_id: "wrong-attempt-walk",
          call_id: "call-1",
          attempt_id: "attempt-other",
          source: "live",
          label: "Different",
          metrics: {},
          stored_frame_count: 0,
        },
      ],
    },
  ],
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchDatabaseSnapshot).mockResolvedValue(data);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("clinician database", () => {
  it("shows linked records, excludes other attempts and preserves zero values", async () => {
    render(<ClinicianDatabase onAuthFailure={() => false} />);
    await screen.findByRole("heading", { name: "Demo Patient - Synthetic" });
    expect(screen.getByText(/Supabase · integrated/)).toBeInTheDocument();
    expect(screen.getByText("Linked survey answers (1)")).toBeInTheDocument();
    expect(screen.getByText("Linked walking results (1)")).toBeInTheDocument();
    expect(screen.getByText(/Session matching-walk/)).toBeInTheDocument();
    expect(
      screen.queryByText(/Session wrong-attempt-walk/),
    ).not.toBeInTheDocument();
    expect(screen.getByText("0.00")).toBeInTheDocument();
    expect(screen.getByText("Pain: 0/10")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Walking (2)" }));
    expect(screen.getByText(/Session wrong-attempt-walk/)).toBeInTheDocument();
    expect(screen.getAllByText("Not recorded").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Record JSON" }));
    expect(screen.getByLabelText("Sanitized patient JSON")).toHaveTextContent(
      "patient-1",
    );
  });

  it("searches and paginates without conflating no matches with failures", async () => {
    vi.mocked(fetchDatabaseSnapshot)
      .mockResolvedValueOnce({ ...data, total: 30 })
      .mockResolvedValueOnce({ ...data, offset: 25 })
      .mockResolvedValue({ ...data, total: 0, patients: [] });
    render(<ClinicianDatabase onAuthFailure={() => false} />);
    await screen.findByRole("heading", { name: "Demo Patient - Synthetic" });
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() =>
      expect(fetchDatabaseSnapshot).toHaveBeenLastCalledWith(
        "",
        25,
        expect.any(AbortSignal),
      ),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Search database" }), {
      target: { value: "missing" },
    });
    await screen.findByText("No patients match your search.");
    expect(fetchDatabaseSnapshot).toHaveBeenLastCalledWith(
      "missing",
      0,
      expect.any(AbortSignal),
    );
  });

  it("clears records on an outage and recovers on refresh", async () => {
    vi.mocked(fetchDatabaseSnapshot)
      .mockResolvedValueOnce(data)
      .mockRejectedValueOnce(new ApiError("Outage", 503))
      .mockResolvedValue(data);
    render(<ClinicianDatabase onAuthFailure={() => false} />);
    await screen.findByRole("heading", { name: "Demo Patient - Synthetic" });
    fireEvent.click(screen.getByRole("button", { name: "Refresh records" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "could not be loaded",
    );
    expect(
      screen.queryByText("Demo Patient - Synthetic"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh records" }));
    await screen.findByRole("heading", { name: "Demo Patient - Synthetic" });
  });

  it("clears protected data and stops polling after session expiry", async () => {
    vi.useFakeTimers();
    const expired = new ApiError("Expired", 401, "session_expired");
    vi.mocked(fetchDatabaseSnapshot)
      .mockResolvedValueOnce(data)
      .mockRejectedValue(expired);
    const onAuthFailure = vi.fn(() => true);
    render(<ClinicianDatabase onAuthFailure={onAuthFailure} />);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(
      screen.getByRole("heading", { name: "Demo Patient - Synthetic" }),
    ).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(onAuthFailure).toHaveBeenCalledWith(expired);
    expect(
      screen.queryByText("Demo Patient - Synthetic"),
    ).not.toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(fetchDatabaseSnapshot).toHaveBeenCalledTimes(2);
  });

  it("aborts on unmount and ignores late responses", async () => {
    const pending = deferred<DatabaseSnapshot>();
    vi.mocked(fetchDatabaseSnapshot).mockReturnValue(pending.promise);
    const onAuthFailure = vi.fn(() => false);
    const view = render(<ClinicianDatabase onAuthFailure={onAuthFailure} />);
    await waitFor(() => expect(fetchDatabaseSnapshot).toHaveBeenCalledOnce());
    const signal = vi.mocked(fetchDatabaseSnapshot).mock.calls[0][2];
    view.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () =>
      pending.reject(new ApiError("Late auth failure", 401)),
    );
    expect(onAuthFailure).not.toHaveBeenCalled();
  });

  it("labels JSON mode honestly", async () => {
    vi.mocked(fetchDatabaseSnapshot).mockResolvedValue({
      ...data,
      source: "json",
    });
    render(<ClinicianDatabase onAuthFailure={() => false} />);
    expect(
      await screen.findByText(/Local JSON demo store · not Supabase/),
    ).toBeInTheDocument();
  });
});

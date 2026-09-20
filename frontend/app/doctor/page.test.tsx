import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DoctorPortal from "./page";
import {
  ApiError,
  fetchPatient,
  fetchPatients,
  generateSynthesis,
  getClinicianSession,
  signInClinician,
  signOutClinician,
} from "../lib/api";

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: ComponentProps<"a">) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("../components/SkeletonReplay", () => ({
  default: () => <div data-testid="skeleton-replay" />,
}));

vi.mock("../components/TrendGraph", () => ({
  default: () => <div data-testid="trend-graph" />,
}));

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    fetchPatient: vi.fn(),
    fetchPatients: vi.fn(),
    generateSynthesis: vi.fn(),
    getClinicianSession: vi.fn(),
    signInClinician: vi.fn(),
    signOutClinician: vi.fn(),
  };
});

const activeSession = {
  authenticated: true as const,
  username: "dr-demo",
  expires_at: Math.floor(Date.now() / 1000) + 3600,
};

afterEach(cleanup);

describe("doctor route authentication", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchPatients).mockResolvedValue([]);
    vi.mocked(fetchPatient).mockRejectedValue(new Error("not expected"));
    vi.mocked(generateSynthesis).mockRejectedValue(new Error("not expected"));
    vi.mocked(signInClinician).mockResolvedValue(activeSession);
    vi.mocked(signOutClinician).mockResolvedValue();
  });

  it("denies the portal when there is no clinician session", async () => {
    vi.mocked(getClinicianSession).mockRejectedValue(
      new ApiError("Clinician authentication required", 401, "session_required")
    );
    render(<DoctorPortal />);

    expect(await screen.findByRole("heading", { name: "Clinician sign-in" })).toBeInTheDocument();
    expect(fetchPatients).not.toHaveBeenCalled();
  });

  it("loads the protected portal for an active session", async () => {
    vi.mocked(getClinicianSession).mockResolvedValue(activeSession);
    render(<DoctorPortal />);

    expect(await screen.findByRole("heading", { name: "Doctor's Portal" })).toBeInTheDocument();
    expect(screen.getByText("dr-demo")).toBeInTheDocument();
    await waitFor(() => expect(fetchPatients).toHaveBeenCalled());
  });

  it("shows an explicit expired-session flow", async () => {
    vi.mocked(getClinicianSession).mockRejectedValue(
      new ApiError("Clinician session expired", 401, "session_expired")
    );
    render(<DoctorPortal />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your clinician session expired. Sign in again."
    );
    expect(fetchPatients).not.toHaveBeenCalled();
  });

  it("replaces the expired-session notice with a failed sign-in error", async () => {
    vi.mocked(getClinicianSession).mockRejectedValue(
      new ApiError("Clinician session expired", 401, "session_expired")
    );
    vi.mocked(signInClinician).mockRejectedValue(
      new Error("Invalid clinician credentials")
    );
    render(<DoctorPortal />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your clinician session expired. Sign in again."
    );
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "dr-demo" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "wrong-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Invalid clinician credentials"
      )
    );
  });

  it("signs in without persisting the entered password", async () => {
    vi.mocked(getClinicianSession).mockRejectedValue(
      new ApiError("Clinician authentication required", 401, "session_required")
    );
    render(<DoctorPortal />);

    fireEvent.change(await screen.findByLabelText("Username"), {
      target: { value: "dr-demo" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "entered-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() =>
      expect(signInClinician).toHaveBeenCalledWith("dr-demo", "entered-secret")
    );
    expect(await screen.findByRole("heading", { name: "Doctor's Portal" })).toBeInTheDocument();
  });

  it("logs out and returns to the sign-in boundary", async () => {
    vi.mocked(getClinicianSession).mockResolvedValue(activeSession);
    render(<DoctorPortal />);

    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(signOutClinician).toHaveBeenCalled());
    expect(await screen.findByRole("heading", { name: "Clinician sign-in" })).toBeInTheDocument();
  });

  it("ignores an in-flight patient list that completes after sign-out", async () => {
    let resolveStaleList!: (
      rows: Awaited<ReturnType<typeof fetchPatients>>
    ) => void;
    const staleList = new Promise<Awaited<ReturnType<typeof fetchPatients>>>((resolve) => {
      resolveStaleList = resolve;
    });
    const freshList = new Promise<Awaited<ReturnType<typeof fetchPatients>>>(() => {});
    vi.mocked(fetchPatients)
      .mockImplementationOnce(() => staleList)
      .mockImplementation(() => freshList);
    vi.mocked(getClinicianSession).mockResolvedValue(activeSession);
    render(<DoctorPortal />);

    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Clinician sign-in" })).toBeInTheDocument();

    await act(async () => {
      resolveStaleList([
        {
          patient_id: "stale-patient",
          name: "Stale Patient",
          age: 70,
          latest_fall_risk: 0.8,
          pain_scale: 5,
          dizziness: true,
          falls_last_6_months: 1,
          latest_survey_at: "2026-09-19T00:00:00Z",
          primary_complaints: ["stale data"],
        },
      ]);
      await staleList;
    });

    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "dr-demo" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Doctor's Portal" })).toBeInTheDocument();
    expect(screen.queryByText("Stale Patient")).not.toBeInTheDocument();
  });
});

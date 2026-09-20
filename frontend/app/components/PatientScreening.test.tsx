import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import type WebcamFeed from "./WebcamFeed";
import PatientScreening from "./PatientScreening";
import { fetchPatientView, type GaitMetrics } from "../lib/api";

let trialMetrics: GaitMetrics;
vi.mock("./WebcamFeed", () => ({
  default: ({ onMetrics }: ComponentProps<typeof WebcamFeed>) => (
    <button onClick={() => onMetrics(trialMetrics, "live")}>Deliver trial</button>
  ),
}));
vi.mock("./TrendGraph", () => ({ default: () => <div>Historical trend</div> }));
vi.mock("../lib/api", async () => ({
  ...await vi.importActual<typeof import("../lib/api")>("../lib/api"),
  fetchPatientView: vi.fn(),
}));

const legacy: GaitMetrics = {
  stride_length_m: 1, asymmetry_pct: 2, velocity_degradation_pct: 0,
  fall_risk_score: 0.2, cadence_steps_per_min: 100, frame_count: 300,
  leg_length_m: 0.9, stride_ratio: 1.1, knee_flexion_rom_deg: 40,
  peak_ankle_speed_mps: 1, gait_detected: true, dropped_frame_pct: 0,
};

afterEach(cleanup);
beforeEach(() => {
  trialMetrics = { ...legacy };
  vi.mocked(fetchPatientView).mockResolvedValue({
    patient_id: "TEST", name: "Test Patient",
    gait_sessions: [{ label: "Old session", source: "live", metrics: legacy }],
  });
});

async function deliver() {
  render(<PatientScreening patientId="TEST" />);
  fireEvent.click(await screen.findByText("Deliver trial"));
}

describe("experimental score display", () => {
  it("supports historical metrics without the experimental fields", async () => {
    await deliver();
    expect(screen.getByText("Original Fall Risk — heuristic")).toBeInTheDocument();
    expect(screen.getByText("0.200")).toBeInTheDocument();
    expect(screen.getByText("Model unavailable")).toBeInTheDocument();
  });

  it("shows quality rejection and its reason without inventing a number", async () => {
    trialMetrics = { ...legacy, experimental_cv_risk_index: null,
      experimental_cv_risk_status: "not_scorable",
      experimental_cv_risk_warnings: ["lower-body landmark visibility is too low"] };
    await deliver();
    expect(screen.getByText("Not scorable")).toBeInTheDocument();
    expect(screen.getByText("lower-body landmark visibility is too low")).toBeInTheDocument();
  });

  it("shows the learned percentile separately from the heuristic band", async () => {
    trialMetrics = { ...legacy, experimental_cv_risk_index: 70.5,
      experimental_cv_risk_status: "scored_with_warning", experimental_cv_risk_model_version: "test-v1",
      experimental_cv_risk_warnings: ["outside reference range"],
      experimental_cv_risk_contributors: { median_step_time_s: 0.123 } };
    await deliver();
    expect(screen.getByText("70.5 / 100")).toBeInTheDocument();
    expect(screen.getByText("outside reference range")).toBeInTheDocument();
    expect(screen.queryByText("HIGH")).not.toBeInTheDocument();
    expect(screen.getByText("LOW")).toBeInTheDocument();
    expect(screen.getByText("median step time s: 0.123")).toBeInTheDocument();
  });
});

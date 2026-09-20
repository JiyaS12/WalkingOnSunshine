import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import FallRiskIndexCard from "./FallRiskIndexCard";
import { metrics } from "../lib/integration.test-support";

afterEach(cleanup);

describe("fall-risk index provenance", () => {
  it("clearly labels fallback without changing its value or inventing severity bands", () => {
    render(<FallRiskIndexCard metrics={{
      ...metrics, cv_fall_risk_index: 72, cv_fall_risk_status: "fallback",
      cv_fall_risk_method: "heuristic_fallback", cv_fall_risk_model_version: "heuristic-display-v1",
    }} />);
    expect(screen.getByText("72 / 100")).toBeInTheDocument();
    expect(screen.getByText("Heuristic fallback")).toBeInTheDocument();
    expect(screen.getByText(/Predictive accuracy is unvalidated/)).toBeInTheDocument();
    expect(screen.queryByText(/HIGH|MODERATE|LOW/)).not.toBeInTheDocument();
  });

  it("explains the learned model's retrospective target rather than a future-fall percentage", () => {
    render(<FallRiskIndexCard metrics={{
      ...metrics, cv_fall_risk_index: 70, cv_fall_risk_status: "scored",
      cv_fall_risk_method: "learned_fall_history",
    }} />);
    expect(screen.getByText("70 / 100")).toBeInTheDocument();
    expect(screen.getByText("Learned prior-fall association")).toBeInTheDocument();
    expect(screen.getByText(/not a probability of future falling/)).toBeInTheDocument();
    expect(screen.queryByText("70%")).not.toBeInTheDocument();
  });

  it("requests a retake without turning a bad recording into a low fallback score", () => {
    render(<FallRiskIndexCard metrics={{
      ...metrics, cv_fall_risk_index: null, cv_fall_risk_status: "not_scorable",
      cv_fall_risk_method: "none",
    }} />);
    expect(screen.getByText("Retake needed")).toBeInTheDocument();
    expect(screen.queryByText(/\/ 100/)).not.toBeInTheDocument();
  });

  it("does not silently rescore historical sessions", () => {
    render(<FallRiskIndexCard metrics={metrics} />);
    expect(screen.getByText("Not recorded")).toBeInTheDocument();
    expect(screen.queryByText(/\/ 100/)).not.toBeInTheDocument();
  });
});

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import SurveyDetails from "./SurveyDetails";

afterEach(cleanup);

it("keeps unknown generic intake independent of confirmed Likert answers", () => {
  render(<SurveyDetails survey={{
    patient_id: "patient-1", call_id: "call-1", recorded_at: "2026-09-20T00:00:00Z",
    condition_survey: { instrument: "hoos_jr", version: "1", condition_category: "orthopedic",
      answers: [{ question_id: "hoos_stairs", normalized_value: "extreme", confirmed: true, acceptance_method: "confirmation" }] },
  }} />);
  const generic = within(screen.getByRole("region", { name: "Original generic intake" }));
  expect(generic.getByText("Pain: Unknown")).toBeInTheDocument();
  expect(generic.getByText("Falls (6 mo): Unknown")).toBeInTheDocument();
  expect(generic.getByText("Fall injury: Unknown")).toBeInTheDocument();
  expect(generic.getByText("Dizziness: Unknown")).toBeInTheDocument();
  expect(screen.getByText("Going up or down stairs: extreme")).toBeInTheDocument();
  expect(screen.getByText(/No validated HOOS JR interval score/)).toBeInTheDocument();
});

it("displays explicit false/zero values and independently unknown fall injury", () => {
  render(<SurveyDetails survey={{ patient_id: "patient-1", pain_scale: 1, fall_history: { falls_last_6_months: 0, injured: null },
    dizziness: false, dizziness_notes: "No symptoms today", primary_complaints: [] }} />);
  expect(screen.getByText("Pain: 1/10")).toBeInTheDocument();
  expect(screen.getByText("Falls (6 mo): 0")).toBeInTheDocument();
  expect(screen.getByText("Fall injury: Unknown")).toBeInTheDocument();
  expect(screen.getByText("Dizziness: No")).toBeInTheDocument();
});

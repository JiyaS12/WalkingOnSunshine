import { describe, expect, it } from "vitest";
import type { GaitMetrics } from "./api";
import { isScorableWalk } from "./gait";
import { metrics } from "./integration.test-support";

describe("walking acceptance", () => {
  it.each<{ change: Partial<GaitMetrics>; accepted: boolean }>([
    { change: {}, accepted: true },
    { change: { cv_fall_risk_status: null, cv_fall_risk_index: null }, accepted: true },
    { change: { gait_detected: false }, accepted: false },
    { change: { cv_fall_risk_status: "not_scorable", cv_fall_risk_index: null }, accepted: false },
    { change: { cv_fall_risk_status: "not_scorable", cv_fall_risk_index: 20 }, accepted: false },
    { change: { cv_fall_risk_status: "scored", cv_fall_risk_index: 1 }, accepted: true },
    { change: { cv_fall_risk_status: "fallback", cv_fall_risk_index: 100 }, accepted: true },
    { change: { cv_fall_risk_status: "fallback", cv_fall_risk_index: null }, accepted: false },
    { change: { cv_fall_risk_status: "scored", cv_fall_risk_index: 0 }, accepted: false },
    { change: { cv_fall_risk_status: "scored", cv_fall_risk_index: 101 }, accepted: false },
    { change: { cv_fall_risk_status: "fallback", cv_fall_risk_index: 2.5 }, accepted: false },
    { change: { cv_fall_risk_status: "fallback", cv_fall_risk_index: NaN }, accepted: false },
    { change: { cv_fall_risk_index: 20 }, accepted: false },
    { change: { gait_detected: false, cv_fall_risk_status: "scored", cv_fall_risk_index: 20 }, accepted: false },
  ])("accepts=$accepted for $change", ({ change, accepted }) => {
    expect(isScorableWalk({ ...metrics, ...change })).toBe(accepted);
  });
});

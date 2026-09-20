import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addPatientAccessSession,
  fetchPatientAccess,
  type GaitMetrics,
} from "./api";

const metrics: GaitMetrics = {
  stride_length_m: 1,
  asymmetry_pct: 5,
  velocity_degradation_pct: 2,
  fall_risk_score: 0.3,
  cadence_steps_per_min: 100,
  frame_count: 90,
  leg_length_m: 0.9,
  stride_ratio: 1.1,
  knee_flexion_rom_deg: 40,
  peak_ankle_speed_mps: 3,
  gait_detected: true,
  dropped_frame_pct: 0,
};

afterEach(() => vi.unstubAllGlobals());

describe("patient access API client", () => {
  it("sends the signed credential only as a bearer header", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ patient_id: "RGN 1", gait_sessions: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchPatientAccess("RGN 1", "signed.payload");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8000/api/patient-access/RGN%201");
    expect(url).not.toContain("signed.payload");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer signed.payload");
    expect(init).toMatchObject({
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
  });

  it("posts sessions only to the patient-scoped endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ patient_id: "RGN-0417", gait_sessions: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await addPatientAccessSession("RGN-0417", "signed.payload", {
      label: "Upload 12:00:00",
      source: "upload",
      idempotency_key: "upload_0123456789abcdef",
      metrics,
      frames: null,
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8000/api/patient-access/RGN-0417/sessions");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer signed.payload");
    expect(JSON.parse(String(init.body))).toMatchObject({
      source: "upload",
      idempotency_key: "upload_0123456789abcdef",
      metrics,
    });
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { processFrames, processVideo } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("authorized gait processing", () => {
  it.each(["frames", "video"] as const)("scopes patient %s processing without cookies or URL tokens", async (kind) => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({}),
    });
    vi.stubGlobal("fetch", fetchMock);
    const signal = new AbortController().signal;
    const access = { patientId: "RGN 1", token: "signed.payload" };
    const file = new File(["video"], "walk.mp4", { type: "video/mp4" });
    if (kind === "frames") await processFrames([], 30, 0.9, signal, access);
    else await processVideo(file, signal, access);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`http://localhost:8000/api/patient-access/RGN%201/process-${kind === "frames" ? "frame" : "video"}`);
    expect(url).not.toContain(access.token);
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer signed.payload");
    expect(init).toMatchObject({
      method: "POST", cache: "no-store", credentials: "omit",
      referrerPolicy: "no-referrer", signal,
    });
    if (kind === "frames") {
      expect(JSON.parse(String(init.body))).toEqual({ frames: [], fps: 30, leg_length_m: 0.9 });
    } else {
      expect((init.body as FormData).get("file")).toBe(file);
      expect(new Headers(init.headers).has("Content-Type")).toBe(false);
    }
  });

  it.each(["frames", "video"] as const)("uses the clinician cookie for unscoped %s processing", async (kind) => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({}),
    });
    vi.stubGlobal("fetch", fetchMock);
    if (kind === "frames") await processFrames([], 30);
    else await processVideo(new File(["video"], "walk.mp4"));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`http://localhost:8000/api/process-${kind === "frames" ? "frame" : "video"}`);
    expect(init.credentials).toBe("include");
    expect(init.cache).toBe("no-store");
  });

  it("does not fall back to clinician processing after patient access expires", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false, status: 401, json: async () => ({ detail: "invalid or expired patient access" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(processFrames([], 30, undefined, undefined, {
      patientId: "RGN-0417", token: "expired.token",
    })).rejects.toMatchObject({
      name: "Error", message: "invalid or expired patient access", status: 401,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

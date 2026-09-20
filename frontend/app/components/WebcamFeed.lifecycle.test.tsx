import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WebcamFeed from "./WebcamFeed";
import { processFrames, processVideo } from "../lib/api";
import { deferred, metrics } from "../lib/integration.test-support";
import type { PoseResults } from "../types/mediapipe";

vi.mock("../lib/api", async () => ({
  ...await vi.importActual<typeof import("../lib/api")>("../lib/api"),
  processFrames: vi.fn(), processVideo: vi.fn(),
}));

let result: (results: PoseResults) => void;
let cameraResult: PoseResults | undefined;
const gum = vi.fn<() => Promise<MediaStream>>();
const stop = vi.fn();
class FakePose {
  setOptions() {}
  onResults(callback: (results: PoseResults) => void) { result = callback; }
  async send() { if (cameraResult) result(cameraResult); }
  async initialize() {}
  async close() {}
}

async function settle() {
  await act(async () => {
    document.querySelector<HTMLScriptElement>('script[src*="@mediapipe/pose"]')?.dispatchEvent(new Event("load"));
    await vi.advanceTimersByTimeAsync(0);
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  cameraResult = undefined;
  window.Pose = FakePose;
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia: gum } });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
    setTimeout(() => callback(performance.now()), 16));
  vi.stubGlobal("cancelAnimationFrame", (timer: ReturnType<typeof setTimeout>) => clearTimeout(timer));
  gum.mockResolvedValue({ getTracks: () => [{ stop }], getVideoTracks: () => [] } as unknown as MediaStream);
  vi.mocked(processFrames).mockResolvedValue(metrics);
  vi.mocked(processVideo).mockResolvedValue({ metrics, frames: [], fps: 30, frames_processed: 90, frames_total: 90, filename: "walk.mp4" });
});
afterEach(() => { cleanup(); delete window.Pose; vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("real webcam lifecycle wiring", () => {
  it("waits for observed landmarks and calibration before reporting ready/capture, and ignores late callbacks after pause", async () => {
    const lifecycle = vi.fn();
    const onMetrics = vi.fn();
    render(<WebcamFeed onMetrics={onMetrics} onLifecycle={lifecycle} />);
    await settle();
    Object.defineProperty(document.querySelector("video"), "readyState", { value: 4 });
    expect(lifecycle).not.toHaveBeenCalled();
    const landmarks = Array.from({ length: 33 }, (_, i) => ({ x: i % 2 ? 0.1 : -0.1, y: i >= 27 ? 1 : i >= 25 ? 0.5 : 0, z: 0 }));
    cameraResult = {
      poseWorldLandmarks: landmarks,
      poseLandmarks: landmarks.map(() => ({ x: 0.5, y: 0.5, z: 0, visibility: 1 })),
    };
    act(() => result(cameraResult!));
    expect(lifecycle.mock.calls.map((call) => call[0])).toEqual(["calibration_started"]);
    act(() => { for (let i = 1; i < 60; i++) result(cameraResult!); });
    expect(lifecycle.mock.calls.map((call) => call[0])).toEqual(["calibration_started", "calibration_completed"]);
    expect(processFrames).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Start 10-second assessment" }));
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(lifecycle).toHaveBeenCalledWith("capture_started", undefined);
    expect(processFrames).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(lifecycle).toHaveBeenCalledWith("capture_completed", undefined);
    expect(onMetrics).toHaveBeenCalledWith(metrics, "live", expect.any(Array));
    fireEvent.click(screen.getByRole("button", { name: "Pause camera" }));
    const count = lifecycle.mock.calls.length;
    act(() => result({ poseWorldLandmarks: landmarks }));
    expect(lifecycle).toHaveBeenCalledTimes(count);
    expect(stop).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Start 10-second assessment" })).toBeDisabled();
  });

  it("publishes actual permission denial and retry without claiming readiness", async () => {
    gum.mockRejectedValue(new DOMException("Denied", "NotAllowedError"));
    const lifecycle = vi.fn();
    render(<WebcamFeed onMetrics={vi.fn()} onLifecycle={lifecycle} />);
    await settle();
    expect(lifecycle).toHaveBeenCalledWith("permission_denied", "permission_denied");
    expect(lifecycle).not.toHaveBeenCalledWith("calibration_completed", undefined);
    fireEvent.click(screen.getByRole("button", { name: "Retry camera" }));
    await settle();
    expect(lifecycle).toHaveBeenCalledWith("page_ready");
    expect(gum).toHaveBeenCalledTimes(2);
  });

  it("reports a failed request and a fresh lifecycle when retrying a guided trial", async () => {
    const lifecycle = vi.fn();
    vi.mocked(processFrames).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<WebcamFeed onMetrics={vi.fn()} onLifecycle={lifecycle} />);
    await settle();
    Object.defineProperty(document.querySelector("video"), "readyState", { value: 4 });
    const landmarks = Array.from({ length: 33 }, (_, i) => ({
      x: i % 2 ? 0.1 : -0.1, y: i >= 27 ? 1 : i >= 25 ? 0.5 : 0, z: 0,
    }));
    cameraResult = {
      poseWorldLandmarks: landmarks,
      poseLandmarks: landmarks.map(() => ({ x: 0.5, y: 0.5, z: 0, visibility: 1 })),
    };
    act(() => { for (let i = 0; i < 60; i++) result(cameraResult!); });
    fireEvent.click(screen.getByRole("button", { name: "Start 10-second assessment" }));
    await act(async () => vi.advanceTimersByTimeAsync(13_000));
    expect(lifecycle).toHaveBeenCalledWith("recoverable_error", "network_error");
    expect(lifecycle).not.toHaveBeenCalledWith("capture_completed", undefined);
    fireEvent.click(screen.getByRole("button", { name: "Start 10-second assessment" }));
    await act(async () => vi.advanceTimersByTimeAsync(13_000));
    expect(lifecycle.mock.calls.filter(([event]) => event === "capture_started")).toHaveLength(2);
    expect(lifecycle).toHaveBeenCalledWith("capture_completed", undefined);
  });

  it("supports upload while camera permission is pending and suppresses the old camera error", async () => {
    const permission = deferred<MediaStream>();
    gum.mockReturnValue(permission.promise);
    const lifecycle = vi.fn();
    const onMetrics = vi.fn();
    const view = render(<WebcamFeed onMetrics={onMetrics} onLifecycle={lifecycle} />);
    await settle();
    fireEvent.click(screen.getByRole("radio", { name: "Upload Video" }));
    const input = view.container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    await act(async () => fireEvent.change(input!, { target: { files: [new File(["fake"], "walk.mp4", { type: "video/mp4" })] } }));
    expect(lifecycle.mock.calls.map((call) => call[0])).toEqual(["capture_started", "capture_completed"]);
    expect(onMetrics).toHaveBeenCalledWith(metrics, "upload", []);
    await act(async () => permission.reject(new DOMException("Late denial", "NotAllowedError")));
    expect(lifecycle).not.toHaveBeenCalledWith("permission_denied", "permission_denied");
    expect(screen.queryByRole("button", { name: "Retry camera" })).not.toBeInTheDocument();
  });
});

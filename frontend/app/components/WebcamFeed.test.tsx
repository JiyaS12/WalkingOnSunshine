import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import WebcamFeed from "./WebcamFeed";
import type { PoseResults } from "../types/mediapipe";
import { processFrames } from "../lib/api";

vi.mock("../lib/api", async () => ({
  ...await vi.importActual<typeof import("../lib/api")>("../lib/api"),
  processFrames: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("guided camera trial", () => {
  it("counts down, records only a complete trial, and rejects missing tracking", async () => {
    vi.useFakeTimers();
    let receive: (results: PoseResults) => void = () => undefined;
    class FakePose {
      setOptions() {}
      onResults(callback: (results: PoseResults) => void) { receive = callback; }
      initialize() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
      send() { return Promise.resolve(); }
    }
    window.Pose = FakePose;
    vi.stubGlobal("requestAnimationFrame", () => 1);
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue({
      getTracks: () => [], getVideoTracks: () => [],
    }) } });
    const reset = vi.fn();
    render(<WebcamFeed onMetrics={vi.fn()} onInputReset={reset} />);
    await act(async () => {
      document.querySelector<HTMLScriptElement>('script[src*="@mediapipe/pose"]')?.dispatchEvent(new Event("load"));
    });
    await act(async () => {
      receive({ poseLandmarks: Array.from({ length: 33 }, () => ({ x: 0.5, y: 0.5, z: 0, visibility: 1 })) });
    });
    const start = screen.getByRole("button", { name: "Start 10-second assessment" });
    expect(start).toBeEnabled();
    fireEvent.click(start);
    expect(screen.getByText("Get into position — 3")).toBeInTheDocument();
    expect(start).toBeDisabled();
    await act(async () => { vi.advanceTimersByTime(3000); });
    expect(screen.getByText("Walk now — 10 seconds remaining")).toBeInTheDocument();
    expect(processFrames).not.toHaveBeenCalled();
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(screen.getByText(/Not scorable: too few tracked frames/)).toBeInTheDocument();
    expect(processFrames).not.toHaveBeenCalled();
    expect(start).toBeEnabled();
    expect(reset).toHaveBeenCalled();
    fireEvent.click(start);
    fireEvent.click(screen.getByRole("radio", { name: /Upload Video/i }));
    await act(async () => { vi.advanceTimersByTime(13_000); });
    expect(processFrames).not.toHaveBeenCalled();
  });
});

import { describe, expect, it } from "vitest";
import { prepareTrial } from "./trial";

function samples(fps: number, omit: (time: number) => boolean = () => false) {
  const callbackTimes = Array.from({ length: fps * 10 }, (_, i) => i * 1000 / fps);
  const times = callbackTimes.filter((time) => !omit(time));
  return {
    frames: times.map((time) => ({ ankle: [time / 1000, Math.sin(time / 1000), 0] })),
    visibility: times.map(() => ({ ankle: 0.9 })),
    times,
    callbackTimes,
  };
}

describe("guided trial sampling", () => {
  it("keeps 30 FPS frames unchanged and decimates 60 FPS without shortening time", () => {
    for (const fps of [30, 60]) {
      const s = samples(fps);
      const trial = prepareTrial(s.frames, s.visibility, s.times, s.callbackTimes);
      expect(trial.fps).toBe(30);
      expect(trial.frames).toHaveLength(300);
      expect(trial.frames[150].ankle[0]).toBeCloseTo(5, 6);
      expect(trial.missingPct).toBe(0);
    }
  });

  it("preserves elapsed time and reports a long lost-tracking gap", () => {
    const s = samples(30, (time) => time >= 4000 && time < 6000);
    const trial = prepareTrial(s.frames, s.visibility, s.times, s.callbackTimes);
    expect(trial.frames[210].ankle[0]).toBeCloseTo(7, 6);
    expect(trial.missingPct).toBeCloseTo(20, 1);
  });

  it("counts missing edges and isolated frames without creating a low FPS", () => {
    const s = samples(30, (time) => time < 100 || time >= 9900 || time === 5000);
    const trial = prepareTrial(s.frames, s.visibility, s.times, s.callbackTimes);
    expect(trial.fps).toBe(30);
    expect(trial.missingPct).toBeGreaterThan(2);
    expect(trial.missingPct).toBeLessThan(10);
  });

  it("does not turn a slow camera into a 30 FPS recording", () => {
    const s = samples(10);
    const trial = prepareTrial(s.frames, s.visibility, s.times, s.callbackTimes);
    expect(trial.fps).toBe(10);
  });

  it("rejects an empty recording rather than submitting an old trial", () => {
    expect(() => prepareTrial([], [], [], [])).toThrow("Not scorable");
  });
});

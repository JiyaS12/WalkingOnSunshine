import type { JointFrame, VisibilityFrame } from "./api";

export function prepareTrial(
  frames: JointFrame[],
  visibility: VisibilityFrame[],
  times: number[],
  callbackTimes: number[],
  durationMs = 10_000
) {
  if (frames.length < 2 || frames.length !== times.length ||
      visibility.length !== frames.length || callbackTimes.length < 2) {
    throw new Error("Not scorable: too few tracked frames. Keep your full body visible.");
  }
  const intervals = callbackTimes.slice(1).map((time, i) => time - callbackTimes[i]);
  const sorted = intervals.filter((interval) => interval > 0).sort((a, b) => a - b);
  const interval = sorted[Math.floor(sorted.length / 2)];
  if (!interval) throw new Error("Not scorable: invalid camera timing.");
  const count = Math.min(300, Math.max(2, Math.round(durationMs / Math.max(interval, 1000 / 30))));
  const fps = count * 1000 / durationMs;
  const sampled: JointFrame[] = [];
  const sampledVisibility: VisibilityFrame[] = [];
  let right = 0;
  let missing = 0;
  for (let slot = 0; slot < count; slot++) {
    const time = slot * 1000 / fps;
    while (right < times.length - 1 && times[right] < time) right++;
    const left = Math.max(0, right - 1);
    const span = times[right] - times[left];
    const weight = span > 0 ? Math.max(0, Math.min(1, (time - times[left]) / span)) : 0;
    if (Math.min(Math.abs(time - times[left]), Math.abs(time - times[right])) > interval * 0.75) {
      missing++;
    }
    const frame: JointFrame = {};
    const confidence: VisibilityFrame = {};
    for (const joint of Object.keys(frames[0])) {
      frame[joint] = frames[left][joint].map(
        (coordinate, axis) => coordinate + weight * (frames[right][joint][axis] - coordinate)
      );
      confidence[joint] = Math.min(visibility[left][joint] ?? 0, visibility[right][joint] ?? 0);
    }
    sampled.push(frame);
    sampledVisibility.push(confidence);
  }
  return {
    frames: sampled,
    visibility: sampledVisibility,
    fps,
    missingPct: 100 * missing / count,
  };
}

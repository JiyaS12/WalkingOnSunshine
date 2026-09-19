import type { NormalizedLandmark } from "../types/mediapipe";

export interface LiveGaitMetrics {
  leftKneeAngle: number;
  rightKneeAngle: number;
  kneeAsymmetryPct: number;
  strideAngleDeg: number;
  leftAnkleFootAngle: number;
  rightAnkleFootAngle: number;
}

function angleBetweenDeg(
  ax: number,
  ay: number,
  az: number,
  bx: number,
  by: number,
  bz: number
): number {
  const dot = ax * bx + ay * by + az * bz;
  const magA = Math.hypot(ax, ay, az);
  const magB = Math.hypot(bx, by, bz);
  if (magA < 1e-9 || magB < 1e-9) return 0;
  const cos = Math.min(1, Math.max(-1, dot / (magA * magB)));
  return (Math.acos(cos) * 180) / Math.PI;
}

// angle at `b` between vectors b->a and b->c
function jointAngleDeg(
  a: NormalizedLandmark,
  b: NormalizedLandmark,
  c: NormalizedLandmark
): number {
  return angleBetweenDeg(
    a.x - b.x,
    a.y - b.y,
    a.z - b.z,
    c.x - b.x,
    c.y - b.y,
    c.z - b.z
  );
}

/**
 * Real-time gait metrics from MediaPipe poseWorldLandmarks.
 * Indices: 23/24 hips, 25/26 knees, 27/28 ankles, 31/32 foot indices.
 */
export function computeLiveGait(
  world: NormalizedLandmark[]
): LiveGaitMetrics | null {
  if (world.length < 33) return null;
  const [lHip, rHip, lKnee, rKnee, lAnkle, rAnkle, lFoot, rFoot] = [
    world[23],
    world[24],
    world[25],
    world[26],
    world[27],
    world[28],
    world[31],
    world[32],
  ];
  if (!lHip || !rHip || !lKnee || !rKnee || !lAnkle || !rAnkle || !lFoot || !rFoot)
    return null;

  const leftKneeAngle = jointAngleDeg(lHip, lKnee, lAnkle);
  const rightKneeAngle = jointAngleDeg(rHip, rKnee, rAnkle);
  const mean = (leftKneeAngle + rightKneeAngle) / 2;
  const kneeAsymmetryPct =
    (Math.abs(leftKneeAngle - rightKneeAngle) / Math.max(mean, 1)) * 100;

  const hipMid = {
    x: (lHip.x + rHip.x) / 2,
    y: (lHip.y + rHip.y) / 2,
    z: (lHip.z + rHip.z) / 2,
  };
  const strideAngleDeg = angleBetweenDeg(
    lAnkle.x - hipMid.x,
    lAnkle.y - hipMid.y,
    lAnkle.z - hipMid.z,
    rAnkle.x - hipMid.x,
    rAnkle.y - hipMid.y,
    rAnkle.z - hipMid.z
  );

  const leftAnkleFootAngle = jointAngleDeg(lKnee, lAnkle, lFoot);
  const rightAnkleFootAngle = jointAngleDeg(rKnee, rAnkle, rFoot);

  return {
    leftKneeAngle,
    rightKneeAngle,
    kneeAsymmetryPct,
    strideAngleDeg,
    leftAnkleFootAngle,
    rightAnkleFootAngle,
  };
}

import type { NormalizedLandmark } from "../types/mediapipe";

export interface LiveGaitMetrics {
  leftKneeFlexion: number;
  rightKneeFlexion: number;
  kneeAsymmetryPct: number;
  strideAngleDeg: number;
  leftAnkleFootAngle: number;
  rightAnkleFootAngle: number;
  leftAnkleSpeed: number;
  rightAnkleSpeed: number;
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

/** Knee flexion: 180 - interior hip-knee-ankle angle (straight leg = 0). */
export function kneeFlexionDeg(
  hip: NormalizedLandmark,
  knee: NormalizedLandmark,
  ankle: NormalizedLandmark
): number {
  return 180 - jointAngleDeg(hip, knee, ankle);
}

/** Estimated leg length: mean over sides of |hip-knee| + |knee-ankle|. */
export function legLengthFrom(world: NormalizedLandmark[]): number {
  if (world.length < 29) return 0;
  const sides: [number, number, number][] = [
    [23, 25, 27],
    [24, 26, 28],
  ];
  const lengths = sides.map(([h, k, a]) => {
    const hip = world[h];
    const knee = world[k];
    const ankle = world[a];
    return (
      Math.hypot(hip.x - knee.x, hip.y - knee.y, hip.z - knee.z) +
      Math.hypot(knee.x - ankle.x, knee.y - ankle.y, knee.z - ankle.z)
    );
  });
  return (lengths[0] + lengths[1]) / 2;
}

/** Ankle speeds (m/s) between two successive world-landmark frames. */
export function ankleSpeeds(
  prevWorld: NormalizedLandmark[] | null,
  world: NormalizedLandmark[],
  dtSec: number
): { left: number; right: number } {
  if (!prevWorld || prevWorld.length < 28 || world.length < 28 || dtSec <= 0) {
    return { left: 0, right: 0 };
  }
  const speed = (idx: number) =>
    Math.hypot(
      world[idx].x - prevWorld[idx].x,
      world[idx].y - prevWorld[idx].y,
      world[idx].z - prevWorld[idx].z
    ) / dtSec;
  return { left: speed(27), right: speed(28) };
}

/**
 * Real-time gait metrics from MediaPipe poseWorldLandmarks.
 * Indices: 23/24 hips, 25/26 knees, 27/28 ankles, 31/32 foot indices.
 */
export function computeLiveGait(
  world: NormalizedLandmark[],
  prevWorld: NormalizedLandmark[] | null = null,
  dtSec = 0
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

  const leftKneeFlexion = kneeFlexionDeg(lHip, lKnee, lAnkle);
  const rightKneeFlexion = kneeFlexionDeg(rHip, rKnee, rAnkle);
  const mean = (leftKneeFlexion + rightKneeFlexion) / 2;
  const kneeAsymmetryPct =
    (Math.abs(leftKneeFlexion - rightKneeFlexion) / Math.max(mean, 20)) * 100;

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
  const speeds = ankleSpeeds(prevWorld, world, dtSec);

  return {
    leftKneeFlexion,
    rightKneeFlexion,
    kneeAsymmetryPct,
    strideAngleDeg,
    leftAnkleFootAngle,
    rightAnkleFootAngle,
    leftAnkleSpeed: speeds.left,
    rightAnkleSpeed: speeds.right,
  };
}

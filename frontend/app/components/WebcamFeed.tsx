"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Camera, FlaskConical, AlertTriangle } from "lucide-react";
import {
  fetchSimulation,
  processFrames,
  JointFrame,
  SimulationSession,
} from "../lib/api";
import type { MediaPipeCamera, Pose, PoseResults } from "../types/mediapipe";

// MediaPipe pose landmark indices -> backend joint names
const LANDMARK_JOINTS: Record<number, keyof JointFrame> = {
  23: "left_hip",
  24: "right_hip",
  25: "left_knee",
  26: "right_knee",
  27: "left_ankle",
  28: "right_ankle",
};

const LEG_JOINT_INDICES = new Set(
  Object.keys(LANDMARK_JOINTS).map(Number)
);

// POSE_CONNECTIONS-like pairs (upper body + legs) for skeleton drawing
const SKELETON_PAIRS: [number, number][] = [
  [11, 12],
  [11, 23],
  [12, 24],
  [23, 24],
  [11, 13],
  [13, 15],
  [12, 14],
  [14, 16],
  [23, 25],
  [25, 27],
  [24, 26],
  [26, 28],
];

// joint key -> indices for the simulated skeleton (order in JointFrame)
const SIM_PAIRS: [string, string][] = [
  ["left_hip", "right_hip"],
  ["left_hip", "left_knee"],
  ["left_knee", "left_ankle"],
  ["right_hip", "right_knee"],
  ["right_knee", "right_ankle"],
];

const POSE_CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/pose/pose.js";
const CAMERA_CDN =
  "https://cdn.jsdelivr.net/npm/@mediapipe/camera_utils/camera_utils.js";
const POSE_FILES = "https://cdn.jsdelivr.net/npm/@mediapipe/pose";

const BUFFER_MAX = 300;
const SYNC_INTERVAL_MS = 2000;
const SYNC_MIN_FRAMES = 30;
const SYNC_BATCH = 90;

const scriptPromises = new Map<string, Promise<void>>();

function loadScript(src: string): Promise<void> {
  const cached = scriptPromises.get(src);
  if (cached) return cached;
  const promise = new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      scriptPromises.delete(src);
      document.querySelector(`script[src="${src}"]`)?.remove();
    };
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () => {
        cleanup();
        reject(new Error(`failed to load ${src}`));
      });
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => {
      cleanup();
      reject(new Error(`failed to load ${src}`));
    };
    document.head.appendChild(script);
  });
  scriptPromises.set(src, promise);
  return promise;
}

function withTimeout<T>(
  promise: Promise<T>,
  ms: number,
  label: string
): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`${label} timed out`)), ms)
    ),
  ]);
}

type TrackingStatus =
  | "idle"
  | "loading-scripts"
  | "requesting-camera"
  | "starting-model"
  | "tracking"
  | "no-person";

interface Props {
  onMetrics: (
    metrics: import("../lib/api").GaitMetrics,
    source: "live" | "simulated"
  ) => void;
}

export default function WebcamFeed({ onMetrics }: Props) {
  const [simulated, setSimulated] = useState(true);
  const [day, setDay] = useState<1 | 14>(1);
  const [error, setError] = useState<string | null>(null);
  const [trackingStatus, setTrackingStatus] = useState<TrackingStatus>("idle");
  const [lastSyncAt, setLastSyncAt] = useState<Date | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const poseRef = useRef<Pose | null>(null);
  const cameraRef = useRef<MediaPipeCamera | null>(null);
  const rafRef = useRef<number>(0);
  const syncIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const resultsTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bufferRef = useRef<JointFrame[]>([]);
  const timesRef = useRef<number[]>([]);
  const sendingRef = useRef(false);
  const generationRef = useRef(0);
  const onMetricsRef = useRef(onMetrics);
  onMetricsRef.current = onMetrics;

  const stopAll = useCallback(() => {
    generationRef.current += 1;
    cancelAnimationFrame(rafRef.current);
    if (syncIntervalRef.current !== null) {
      clearInterval(syncIntervalRef.current);
      syncIntervalRef.current = null;
    }
    if (resultsTimerRef.current !== null) {
      clearTimeout(resultsTimerRef.current);
      resultsTimerRef.current = null;
    }
    try {
      cameraRef.current?.stop();
    } catch {
      // camera_utils stop may throw if never started
    }
    cameraRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    void poseRef.current?.close().catch(() => undefined);
    poseRef.current = null;
    bufferRef.current = [];
    timesRef.current = [];
    sendingRef.current = false;
  }, []);

  const failToSimulated = useCallback(
    (message: string) => {
      setError(message);
      stopAll();
      setSimulated(true);
    },
    [stopAll]
  );

  const drawPoseLandmarks = useCallback((results: PoseResults) => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const lm = results.poseLandmarks;
    if (!lm) return;
    ctx.strokeStyle = "#34d399";
    ctx.lineWidth = 2;
    for (const [a, b] of SKELETON_PAIRS) {
      const pa = lm[a];
      const pb = lm[b];
      if (!pa || !pb) continue;
      ctx.beginPath();
      ctx.moveTo(pa.x * canvas.width, pa.y * canvas.height);
      ctx.lineTo(pb.x * canvas.width, pb.y * canvas.height);
      ctx.stroke();
    }
    const drawn = new Set<number>();
    for (const [a, b] of SKELETON_PAIRS) {
      drawn.add(a);
      drawn.add(b);
    }
    drawn.forEach((idx) => {
      const p = lm[idx];
      if (!p) return;
      const isLeg = LEG_JOINT_INDICES.has(idx);
      ctx.fillStyle = isLeg ? "#f472b6" : "#f8fafc";
      ctx.beginPath();
      ctx.arc(
        p.x * canvas.width,
        p.y * canvas.height,
        isLeg ? 4 : 2.5,
        0,
        2 * Math.PI
      );
      ctx.fill();
    });
  }, []);

  const handleResults = useCallback(
    (results: PoseResults) => {
      if (resultsTimerRef.current !== null) {
        clearTimeout(resultsTimerRef.current);
        resultsTimerRef.current = null;
      }
      drawPoseLandmarks(results);
      setTrackingStatus(results.poseLandmarks ? "tracking" : "no-person");
      const world = results.poseWorldLandmarks;
      if (!world || world.length < 29) return;
      const frame: JointFrame = {};
      for (const [idx, joint] of Object.entries(LANDMARK_JOINTS)) {
        const p = world[Number(idx)];
        // flip y so up is positive, matching backend coords
        frame[joint] = [p.x, -p.y, p.z];
      }
      const buf = bufferRef.current;
      buf.push(frame);
      timesRef.current.push(performance.now());
      if (buf.length > BUFFER_MAX) buf.splice(0, buf.length - BUFFER_MAX);
      if (timesRef.current.length > BUFFER_MAX)
        timesRef.current.splice(0, timesRef.current.length - BUFFER_MAX);
    },
    [drawPoseLandmarks]
  );

  const startLive = useCallback(async () => {
    const gen = ++generationRef.current;
    const isStale = () => gen !== generationRef.current;
    setTrackingStatus("loading-scripts");
    try {
      if (
        !navigator.mediaDevices ||
        !navigator.mediaDevices.getUserMedia
      ) {
        throw new Error(
          `Camera API unavailable (needs HTTPS or localhost, and a top-level tab — embedded previews block camera access)${
            window.isSecureContext ? "" : "; page is not a secure context"
          }`
        );
      }
      await withTimeout(
        Promise.all([loadScript(POSE_CDN), loadScript(CAMERA_CDN)]),
        15_000,
        "MediaPipe script load"
      );
      if (isStale()) return;
      if (!window.Pose) throw new Error("MediaPipe Pose unavailable");
      if (!window.Camera)
        throw new Error("MediaPipe camera_utils unavailable");

      setTrackingStatus("requesting-camera");
      const stream = await withTimeout(
        navigator.mediaDevices.getUserMedia({ video: true }),
        30_000,
        "Camera permission"
      );
      if (isStale()) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      streamRef.current = stream;
      const video = videoRef.current;
      if (!video) throw new Error("video element missing");
      video.srcObject = stream;
      await withTimeout(video.play(), 10_000, "Video playback");
      if (isStale()) return;

      setTrackingStatus("starting-model");

      const pose = new window.Pose({
        locateFile: (file) => `${POSE_FILES}/${file}`,
      });
      pose.setOptions({
        modelComplexity: 1,
        smoothLandmarks: true,
        minDetectionConfidence: 0.5,
        minTrackingConfidence: 0.5,
      });
      pose.onResults(handleResults);
      if (isStale()) {
        void pose.close().catch(() => undefined);
        return;
      }
      poseRef.current = pose;

      const canvas = canvasRef.current;
      video.onloadedmetadata = () => {
        if (canvas && video.videoWidth && video.videoHeight) {
          canvas.width = video.videoWidth;
          canvas.height = video.videoHeight;
        }
      };
      if (canvas && video.videoWidth && video.videoHeight) {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
      }

      const camera = new window.Camera(video, {
        onFrame: async () => {
          await pose.send({ image: video });
        },
        width: 640,
        height: 480,
      });
      await withTimeout(camera.start(), 20_000, "Camera start");
      if (isStale()) {
        try {
          camera.stop();
        } catch {
          // not started
        }
        return;
      }
      cameraRef.current = camera;

      resultsTimerRef.current = setTimeout(() => {
        if (isStale()) return;
        failToSimulated(
          "MediaPipe model did not start — check network access to cdn.jsdelivr.net"
        );
      }, 20_000);

      syncIntervalRef.current = setInterval(() => {
        const buf = bufferRef.current;
        if (buf.length < SYNC_MIN_FRAMES || sendingRef.current) return;
        sendingRef.current = true;
        const batch = buf.slice(-SYNC_BATCH);
        const times = timesRef.current.slice(-SYNC_BATCH);
        let fps = 30;
        if (times.length >= 2) {
          const span =
            (times[times.length - 1] - times[0]) / 1000;
          const measured = (times.length - 1) / span;
          if (Number.isFinite(measured) && measured > 0) {
            fps = Math.min(60, Math.max(5, measured));
          }
        }
        processFrames(batch, fps)
          .then((m) => {
            onMetricsRef.current(m, "live");
            setLastSyncAt(new Date());
          })
          .catch(() => undefined)
          .finally(() => {
            sendingRef.current = false;
          });
      }, SYNC_INTERVAL_MS);
    } catch (err) {
      console.error("[GaitGuard live]", err);
      failToSimulated(
        `Live camera unavailable: ${
          err instanceof Error ? err.message : String(err)
        }. Switched to Simulated Trial Mode.`
      );
    }
  }, [failToSimulated, handleResults]);

  const startSimulated = useCallback(async () => {
    const gen = generationRef.current;
    const session = (await fetchSimulation(day)) as SimulationSession;
    if (gen !== generationRef.current) return;
    onMetricsRef.current(session.metrics, "simulated");
    const canvas = canvasRef.current;
    if (canvas) {
      canvas.width = 640;
      canvas.height = 360;
    }
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const frames = session.frames;
    // bounds for vertical scaling; x is centered on each frame's hip midpoint
    const ys = frames.flatMap((f) =>
      Object.values(f).map((v) => v[1])
    );
    const yMin = Math.min(...ys);
    const yMax = Math.max(...ys);
    const pad = 20;
    const scale =
      ((canvas.height - 2 * pad) / Math.max(yMax - yMin, 0.01)) * 0.9;
    const toY = (y: number) => canvas.height - pad - (y - yMin) * scale;

    let i = 0;
    let last = 0;
    const frameMs = 1000 / session.fps;
    const loop = (ts: number) => {
      if (ts - last >= frameMs) {
        last = ts;
        const f = frames[i % frames.length];
        i += 1;
        const hipMidX = (f.left_hip[0] + f.right_hip[0]) / 2;
        const toX = (x: number) =>
          canvas.width / 2 + (x - hipMidX) * scale;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.strokeStyle = "#60a5fa";
        ctx.lineWidth = 2;
        for (const [a, b] of SIM_PAIRS) {
          const pa = f[a];
          const pb = f[b];
          if (!pa || !pb) continue;
          ctx.beginPath();
          ctx.moveTo(toX(pa[0]), toY(pa[1]));
          ctx.lineTo(toX(pb[0]), toY(pb[1]));
          ctx.stroke();
        }
        ctx.fillStyle = "#facc15";
        for (const joint of Object.values(f)) {
          ctx.beginPath();
          ctx.arc(toX(joint[0]), toY(joint[1]), 4, 0, 2 * Math.PI);
          ctx.fill();
        }
      }
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
  }, [day]);

  useEffect(() => {
    stopAll();
    if (simulated) {
      setTrackingStatus("idle");
      startSimulated().catch((err) =>
        setError(
          `Simulation failed: ${
            err instanceof Error ? err.message : String(err)
          }`
        )
      );
    } else {
      void startLive();
    }
    return stopAll;
  }, [simulated, day, startLive, startSimulated, stopAll]);

  const statusDot =
    trackingStatus === "tracking"
      ? "bg-emerald-400"
      : trackingStatus === "no-person"
        ? "bg-amber-400"
        : "bg-slate-500";
  const statusText =
    trackingStatus === "tracking"
      ? "Tracking pose"
      : trackingStatus === "no-person"
        ? "No person detected"
        : trackingStatus === "loading-scripts"
          ? "Loading MediaPipe…"
          : trackingStatus === "requesting-camera"
            ? "Requesting camera permission…"
            : trackingStatus === "starting-model"
              ? "Starting pose model…"
              : "Starting camera…";

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900 p-4">
      <div className="mb-3">
        <div
          role="radiogroup"
          aria-label="Input mode"
          className="grid grid-cols-2 gap-2"
        >
          <button
            role="radio"
            aria-checked={!simulated}
            onClick={() => {
              setError(null);
              setSimulated(false);
            }}
            className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-sm font-medium transition-colors ${
              !simulated
                ? "border-emerald-500 bg-emerald-600 text-white"
                : "border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700"
            }`}
          >
            <Camera className="h-4 w-4" />
            Live Camera (MediaPipe Pose)
          </button>
          <button
            role="radio"
            aria-checked={simulated}
            onClick={() => {
              setError(null);
              setSimulated(true);
            }}
            className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-sm font-medium transition-colors ${
              simulated
                ? "border-emerald-500 bg-emerald-600 text-white"
                : "border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700"
            }`}
          >
            <FlaskConical className="h-4 w-4" />
            Simulated Trial Mode
          </button>
        </div>
        <p className="mt-2 text-xs text-slate-400">
          {simulated
            ? "Pre-computed geriatric trial telemetry — patient RGN-0417, Regeneron mobility arm"
            : "Client-side pose tracking; nothing leaves the browser except joint coordinates"}
        </p>
        {!simulated && (
          <div className="mt-2 flex items-center justify-between text-xs">
            <span className="flex items-center gap-1.5 text-slate-300">
              <span className={`h-2 w-2 rounded-full ${statusDot}`} />
              {statusText}
            </span>
            <span className="text-slate-500">
              {lastSyncAt
                ? `Last sync: ${lastSyncAt.toLocaleTimeString("en-GB", { hour12: false })}`
                : "waiting for frames"}
            </span>
          </div>
        )}
      </div>
      <div className="mb-3 flex items-center justify-end gap-4">
        {simulated && (
          <div className="flex gap-1 rounded-lg bg-slate-800 p-1 text-xs">
            {([1, 14] as const).map((d) => (
              <button
                key={d}
                onClick={() => {
                  setError(null);
                  setDay(d);
                }}
                className={`rounded px-2 py-1 ${
                  day === d
                    ? "bg-emerald-600 text-white"
                    : "text-slate-300 hover:bg-slate-700"
                }`}
              >
                Day {d}
              </button>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-600/50 bg-amber-900/30 px-3 py-2 text-xs text-amber-200">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      <div className="relative aspect-video overflow-hidden rounded-lg bg-slate-950">
        <video
          ref={videoRef}
          className={`absolute inset-0 h-full w-full -scale-x-100 object-cover ${
            simulated ? "hidden" : ""
          }`}
          muted
          playsInline
        />
        <canvas
          ref={canvasRef}
          width={640}
          height={360}
          className={`h-full w-full ${
            simulated ? "" : "absolute inset-0 -scale-x-100 object-cover"
          }`}
        />
        {simulated ? (
          <span className="absolute right-2 top-2 rounded bg-emerald-600/90 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-white">
            SIMULATED
          </span>
        ) : (
          <span className="absolute right-2 top-2 rounded bg-rose-600/90 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-white">
            LIVE
          </span>
        )}
      </div>
    </div>
  );
}

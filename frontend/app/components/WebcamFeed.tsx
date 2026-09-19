"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Camera,
  FlaskConical,
  AlertTriangle,
  RefreshCw,
  ExternalLink,
} from "lucide-react";
import {
  fetchSimulation,
  processFrames,
  JointFrame,
  SimulationSession,
} from "../lib/api";
import {
  computeLiveGait,
  legLengthFrom,
  LiveGaitMetrics,
} from "../lib/gait";
import type {
  NormalizedLandmark,
  Pose,
  PoseResults,
} from "../types/mediapipe";

type VideoWithRVFC = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: () => void) => number;
};

// MediaPipe pose landmark indices -> backend joint names
const LANDMARK_JOINTS: Record<number, keyof JointFrame> = {
  23: "left_hip",
  24: "right_hip",
  25: "left_knee",
  26: "right_knee",
  27: "left_ankle",
  28: "right_ankle",
};

// lower-body landmarks including heels (29/30) and foot indices (31/32)
const LOWER_BODY_INDICES = new Set(
  [23, 24, 25, 26, 27, 28, 29, 30, 31, 32]
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
  [27, 29],
  [29, 31],
  [27, 31],
  [28, 30],
  [30, 32],
  [28, 32],
];

// joint key -> indices for the simulated skeleton (order in JointFrame)
const SIM_PAIRS: [string, string][] = [
  ["left_hip", "right_hip"],
  ["left_hip", "left_knee"],
  ["left_knee", "left_ankle"],
  ["right_hip", "right_knee"],
  ["right_knee", "right_ankle"],
];

const POSE_VERSION = "0.5.1675469404";
const POSE_CDN = `https://cdn.jsdelivr.net/npm/@mediapipe/pose@${POSE_VERSION}/pose.js`;
const POSE_FILES = `https://cdn.jsdelivr.net/npm/@mediapipe/pose@${POSE_VERSION}`;

const EMBEDDED_BLOCKED_MSG =
  "Camera access is blocked inside the embedded preview. Open the dashboard in its own browser tab to use Live Camera.";

const BUFFER_MAX = 300;
const SYNC_INTERVAL_MS = 2000;
const SYNC_MIN_FRAMES = 30;
const SYNC_BATCH = 90;

const scriptPromises = new Map<string, Promise<void>>();

function loadScript(src: string): Promise<void> {
  const cached = scriptPromises.get(src);
  if (cached) return cached;
  const promise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () =>
        reject(new Error(`failed to load ${src}`))
      );
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`failed to load ${src}`));
    document.head.appendChild(script);
  });
  const timed = withTimeout(promise, 15_000, `script load ${src}`).catch(
    (err) => {
      scriptPromises.delete(src);
      document.querySelector(`script[src="${src}"]`)?.remove();
      throw err;
    }
  );
  scriptPromises.set(src, timed);
  return timed;
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
  | "loading-model"
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
  const [cameraBlocked, setCameraBlocked] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const [liveGait, setLiveGait] = useState<LiveGaitMetrics | null>(null);
  const [embedded, setEmbedded] = useState(false);
  const [legLengthM, setLegLengthM] = useState<number | null>(null);
  const [calibrationCount, setCalibrationCount] = useState(0);

  useEffect(() => {
    setEmbedded(window.self !== window.top);
  }, []);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const poseRef = useRef<Pose | null>(null);
  const rafRef = useRef<number>(0);
  const inFlightRef = useRef(false);
  const loopActiveRef = useRef(false);
  const sendFailedRef = useRef(false);
  const syncIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const resultsTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bufferRef = useRef<JointFrame[]>([]);
  const timesRef = useRef<number[]>([]);
  const sendingRef = useRef(false);
  const generationRef = useRef(0);
  const gaitEmaRef = useRef<LiveGaitMetrics | null>(null);
  const lastGaitUpdateRef = useRef(0);
  const prevWorldRef = useRef<NormalizedLandmark[] | null>(null);
  const prevTimeRef = useRef(0);
  const calibrationRef = useRef<number[]>([]);
  const legLengthRef = useRef<number | null>(null);
  const onMetricsRef = useRef(onMetrics);
  onMetricsRef.current = onMetrics;

  const stopAll = useCallback(() => {
    generationRef.current += 1;
    cancelAnimationFrame(rafRef.current);
    abortRef.current?.abort();
    abortRef.current = null;
    if (syncIntervalRef.current !== null) {
      clearInterval(syncIntervalRef.current);
      syncIntervalRef.current = null;
    }
    if (resultsTimerRef.current !== null) {
      clearTimeout(resultsTimerRef.current);
      resultsTimerRef.current = null;
    }
    loopActiveRef.current = false;
    inFlightRef.current = false;
    sendFailedRef.current = false;
    const canvas = canvasRef.current;
    canvas?.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    void poseRef.current?.close().catch(() => undefined);
    poseRef.current = null;
    bufferRef.current = [];
    timesRef.current = [];
    sendingRef.current = false;
    gaitEmaRef.current = null;
    lastGaitUpdateRef.current = 0;
    prevWorldRef.current = null;
    prevTimeRef.current = 0;
    calibrationRef.current = [];
    legLengthRef.current = null;
    setLegLengthM(null);
    setCalibrationCount(0);
    setLiveGait(null);
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
      const isLeg = LOWER_BODY_INDICES.has(idx);
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

      const nowMs = performance.now();
      const dt =
        prevTimeRef.current > 0 ? (nowMs - prevTimeRef.current) / 1000 : 0;
      const raw = computeLiveGait(world, prevWorldRef.current, dt);
      prevWorldRef.current = world;
      prevTimeRef.current = nowMs;

      // calibration: collect leg length over the first 60 world frames
      if (legLengthRef.current === null) {
        const len = legLengthFrom(world);
        if (len > 0) {
          calibrationRef.current.push(len);
          const n = calibrationRef.current.length;
          setCalibrationCount(n);
          if (n >= 60) {
            const sorted = [...calibrationRef.current].sort((a, b) => a - b);
            const median = sorted[Math.floor(sorted.length / 2)];
            legLengthRef.current = median;
            setLegLengthM(median);
          }
        }
      }

      if (raw) {
        const ema = gaitEmaRef.current;
        const alpha = 0.2;
        gaitEmaRef.current = ema
          ? (Object.fromEntries(
              Object.entries(raw).map(([k, v]) => [
                k,
                (ema as unknown as Record<string, number>)[k] +
                  alpha * (v - (ema as unknown as Record<string, number>)[k]),
              ])
            ) as unknown as LiveGaitMetrics)
          : raw;
        if (nowMs - lastGaitUpdateRef.current >= 250) {
          lastGaitUpdateRef.current = nowMs;
          setLiveGait({ ...gaitEmaRef.current });
        }
      }

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
      await loadScript(POSE_CDN);
      if (isStale()) return;
      if (!window.Pose) throw new Error("MediaPipe Pose unavailable");

      const canvasEl = canvasRef.current;
      canvasEl
        ?.getContext("2d")
        ?.clearRect(0, 0, canvasEl.width, canvasEl.height);

      setTrackingStatus("requesting-camera");
      let stream: MediaStream;
      try {
        const gumPromise = navigator.mediaDevices.getUserMedia({
          video: {
            width: { ideal: 1280 },
            height: { ideal: 720 },
            facingMode: "user",
          },
        });
        // stop the tracks if the grant resolves after the timeout/mode change
        gumPromise
          .then((s) => {
            if (streamRef.current !== s) {
              s.getTracks().forEach((t) => t.stop());
            }
          })
          .catch(() => undefined);
        stream = await withTimeout(gumPromise, 30_000, "Camera permission");
      } catch (err) {
        if (isStale()) return;
        const name = err instanceof DOMException ? err.name : "";
        let msg: string;
        if (name === "NotAllowedError") {
          msg = embedded
            ? EMBEDDED_BLOCKED_MSG
            : "Camera permission was denied. Click the camera icon in the address bar to allow access, then Retry.";
        } else if (name === "NotFoundError") {
          msg = "No camera detected on this device.";
        } else if (name === "NotReadableError" || name === "AbortError") {
          msg = "Camera is in use by another app.";
        } else if (name === "SecurityError" || !window.isSecureContext) {
          msg =
            "Camera requires HTTPS or localhost in a top-level tab (embedded previews block it).";
        } else {
          msg = err instanceof Error ? err.message : String(err);
        }
        stopAll();
        setCameraBlocked(msg);
        setTrackingStatus("idle");
        return;
      }
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
      abortRef.current = new AbortController();

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

      setTrackingStatus("loading-model");
      try {
        await withTimeout(pose.initialize(), 90_000, "Model download");
      } catch (err) {
        void pose.close().catch(() => undefined);
        throw new Error(
          `Model download: ${err instanceof Error ? err.message : String(err)}`
        );
      }
      if (isStale()) return;
      setTrackingStatus("starting-model");

      const canvas = canvasRef.current;
      if (canvas && video.videoWidth && video.videoHeight) {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
      }

      // own inference loop: rVFC when available, else rAF
      const startLoop = () => {
        if (isStale()) return;
        loopActiveRef.current = true;
        const v = video as VideoWithRVFC;
        const schedule = () => {
          if (v.requestVideoFrameCallback) {
            v.requestVideoFrameCallback(tick);
          } else {
            rafRef.current = requestAnimationFrame(tick);
          }
        };
        const tick = async () => {
          if (!loopActiveRef.current || isStale()) return;
          if (video.readyState >= 2 && !inFlightRef.current) {
            inFlightRef.current = true;
            try {
              await pose.send({ image: video });
            } catch (err) {
              if (!sendFailedRef.current && !isStale()) {
                sendFailedRef.current = true;
                loopActiveRef.current = false;
                console.error("[GaitGuard] pose.send failed", err);
                failToSimulated(
                  `MediaPipe inference failed: ${
                    err instanceof Error ? err.message : String(err)
                  }`
                );
                return;
              }
            } finally {
              inFlightRef.current = false;
            }
          }
          schedule();
        };
        schedule();
      };
      if (video.readyState >= 1) {
        startLoop();
      } else {
        video.onloadedmetadata = () => {
          if (canvas && video.videoWidth && video.videoHeight) {
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
          }
          startLoop();
        };
      }

      // arm watchdog only after the model is loaded and the loop is running
      resultsTimerRef.current = setTimeout(() => {
        if (isStale()) return;
        failToSimulated(
          "Pose model loaded but produced no results — try reloading the page"
        );
      }, 15_000);

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
        const sendGen = generationRef.current;
        processFrames(
          batch,
          fps,
          legLengthRef.current ?? undefined,
          abortRef.current?.signal
        )
          .then((m) => {
            if (sendGen !== generationRef.current) return;
            onMetricsRef.current(m, "live");
            setLastSyncAt(new Date());
          })
          .catch((err) => {
            if (err instanceof DOMException && err.name === "AbortError")
              return;
          })
          .finally(() => {
            if (sendGen === generationRef.current)
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
  }, [failToSimulated, handleResults, stopAll, embedded]);

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
    setCameraBlocked(null);
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
  }, [simulated, day, retryNonce, startLive, startSimulated, stopAll]);

  const statusDot =
    trackingStatus === "tracking"
      ? "bg-emerald-400"
      : trackingStatus === "no-person" || (trackingStatus === "idle" && cameraBlocked)
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
            : trackingStatus === "loading-model"
              ? "Downloading pose model (≈10 MB, first run only)…"
            : trackingStatus === "starting-model"
              ? "Starting pose model…"
              : trackingStatus === "idle" && cameraBlocked
                ? "Camera blocked"
                : "Starting camera…";

  const statusDetail =
    trackingStatus === "tracking" || trackingStatus === "no-person"
      ? legLengthM
        ? `Calibrated · leg ${legLengthM.toFixed(2)} m · ` +
          (lastSyncAt
            ? `Last sync: ${lastSyncAt.toLocaleTimeString("en-GB", { hour12: false })}`
            : "waiting for frames")
        : `Calibrating baseline… ${calibrationCount}/60`
      : lastSyncAt
        ? `Last sync: ${lastSyncAt.toLocaleTimeString("en-GB", { hour12: false })}`
        : "waiting for frames";

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
            <span className="text-slate-500">{statusDetail}</span>
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
          autoPlay
          muted
          playsInline
          className="absolute inset-0 h-full w-full -scale-x-100 object-cover"
          style={{ display: simulated ? "none" : "block" }}
        />
        {!simulated && cameraBlocked && (
          <div className="absolute inset-0 z-10 flex items-center justify-center p-4">
            <div className="max-w-sm rounded-lg border border-amber-600/60 bg-amber-900/40 p-4 text-center">
              <AlertTriangle className="mx-auto mb-2 h-6 w-6 text-amber-300" />
              <p className="text-xs text-amber-100">{cameraBlocked}</p>
              <div className="mt-3 flex flex-wrap justify-center gap-2">
                {embedded && (
                  <button
                    onClick={() =>
                      window.open(window.location.href, "_blank", "noopener")
                    }
                    className="flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-500"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    Open in new tab
                  </button>
                )}
                <button
                  onClick={() => {
                    setCameraBlocked(null);
                    setRetryNonce((n) => n + 1);
                  }}
                  className="flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  Retry camera
                </button>
                <button
                  onClick={() => setSimulated(true)}
                  className="rounded-md border border-slate-600 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700"
                >
                  Use Simulated Trial Mode
                </button>
              </div>
            </div>
          </div>
        )}
        <canvas
          ref={canvasRef}
          width={640}
          height={360}
          className={`pointer-events-none h-full w-full ${
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

      {!simulated && (
        <div className="mt-3 grid grid-cols-4 gap-2 text-center">
          <div className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5">
            <p className="text-[10px] uppercase tracking-wide text-slate-500">
              Knee flex L/R °
            </p>
            <p className="text-sm font-semibold text-slate-100">
              {liveGait
                ? `${liveGait.leftKneeFlexion.toFixed(0)}/${liveGait.rightKneeFlexion.toFixed(0)}`
                : "—"}
            </p>
          </div>
          <div className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5">
            <p className="text-[10px] uppercase tracking-wide text-slate-500">
              Knee asym %
            </p>
            <p className="text-sm font-semibold text-slate-100">
              {liveGait ? liveGait.kneeAsymmetryPct.toFixed(1) : "—"}
            </p>
          </div>
          <div className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5">
            <p className="text-[10px] uppercase tracking-wide text-slate-500">
              Stride angle °
            </p>
            <p className="text-sm font-semibold text-slate-100">
              {liveGait ? liveGait.strideAngleDeg.toFixed(1) : "—"}
            </p>
          </div>
          <div className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5">
            <p className="text-[10px] uppercase tracking-wide text-slate-500">
              Ankle speed L/R m/s
            </p>
            <p className="text-sm font-semibold text-slate-100">
              {liveGait
                ? `${liveGait.leftAnkleSpeed.toFixed(1)}/${liveGait.rightAnkleSpeed.toFixed(1)}`
                : "—"}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

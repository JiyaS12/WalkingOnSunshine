"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Camera, FlaskConical, AlertTriangle } from "lucide-react";
import {
  fetchSimulation,
  processFrames,
  JointFrame,
  SimulationSession,
} from "../lib/api";
import type { Pose, PoseResults } from "../types/mediapipe";

// MediaPipe pose landmark indices -> backend joint names
const LANDMARK_JOINTS: Record<number, keyof JointFrame> = {
  23: "left_hip",
  24: "right_hip",
  25: "left_knee",
  26: "right_knee",
  27: "left_ankle",
  28: "right_ankle",
};

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
const POSE_FILES = "https://cdn.jsdelivr.net/npm/@mediapipe/pose";

const BUFFER_FLUSH = 90;
const BUFFER_MAX = 300;

let poseScriptPromise: Promise<void> | null = null;

function loadPoseScript(): Promise<void> {
  if (typeof window !== "undefined" && window.Pose) {
    return Promise.resolve();
  }
  if (!poseScriptPromise) {
    poseScriptPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = POSE_CDN;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("failed to load MediaPipe Pose"));
      document.head.appendChild(script);
    });
  }
  return poseScriptPromise;
}

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

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const poseRef = useRef<Pose | null>(null);
  const rafRef = useRef<number>(0);
  const bufferRef = useRef<JointFrame[]>([]);
  const sendingRef = useRef(false);
  const onMetricsRef = useRef(onMetrics);
  onMetricsRef.current = onMetrics;

  const stopAll = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    void poseRef.current?.close().catch(() => undefined);
    poseRef.current = null;
    bufferRef.current = [];
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
    ctx.fillStyle = "#f472b6";
    for (const idx of Object.keys(LANDMARK_JOINTS).map(Number)) {
      const p = lm[idx];
      if (!p) continue;
      ctx.beginPath();
      ctx.arc(p.x * canvas.width, p.y * canvas.height, 4, 0, 2 * Math.PI);
      ctx.fill();
    }
  }, []);

  const handleResults = useCallback(
    (results: PoseResults) => {
      drawPoseLandmarks(results);
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
      if (buf.length > BUFFER_MAX) buf.splice(0, buf.length - BUFFER_MAX);
      if (buf.length >= BUFFER_FLUSH && !sendingRef.current) {
        sendingRef.current = true;
        const batch = buf.slice(-BUFFER_FLUSH);
        processFrames(batch, 30)
          .then((m) => onMetricsRef.current(m, "live"))
          .catch(() => undefined)
          .finally(() => {
            sendingRef.current = false;
          });
      }
    },
    [drawPoseLandmarks]
  );

  const startLive = useCallback(async () => {
    try {
      await loadPoseScript();
      if (!window.Pose) throw new Error("MediaPipe Pose unavailable");
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480 },
      });
      streamRef.current = stream;
      const video = videoRef.current;
      if (!video) throw new Error("video element missing");
      video.srcObject = stream;
      await video.play();

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
      poseRef.current = pose;

      const loop = async () => {
        if (poseRef.current && video.readyState >= 2) {
          await poseRef.current.send({ image: video }).catch(() => undefined);
        }
        rafRef.current = requestAnimationFrame(loop);
      };
      rafRef.current = requestAnimationFrame(loop);
    } catch (err) {
      failToSimulated(
        `Live camera unavailable: ${
          err instanceof Error ? err.message : String(err)
        }. Switched to Simulated Trial Mode.`
      );
    }
  }, [failToSimulated, handleResults]);

  const startSimulated = useCallback(async () => {
    const session = (await fetchSimulation(day)) as SimulationSession;
    onMetricsRef.current(session.metrics, "simulated");
    const canvas = canvasRef.current;
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

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900 p-4">
      <div className="mb-3 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <button
            role="switch"
            aria-checked={simulated}
            aria-label="Toggle simulated trial mode"
            onClick={() => {
              setError(null);
              setSimulated((s) => !s);
            }}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
              simulated ? "bg-emerald-500" : "bg-slate-600"
            }`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                simulated ? "translate-x-6" : "translate-x-1"
              }`}
            />
          </button>
          <span className="flex items-center gap-2 text-sm text-slate-200">
            {simulated ? (
              <>
                <FlaskConical className="h-4 w-4 text-emerald-400" />
                Simulated Trial Mode
              </>
            ) : (
              <>
                <Camera className="h-4 w-4 text-sky-400" />
                Live Camera
              </>
            )}
          </span>
        </div>
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
          className={`absolute inset-0 h-full w-full object-cover ${
            simulated ? "hidden" : ""
          }`}
          muted
          playsInline
        />
        <canvas ref={canvasRef} width={640} height={360} className="h-full w-full" />
        {simulated && (
          <span className="absolute right-2 top-2 rounded bg-emerald-600/90 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-white">
            SIMULATED
          </span>
        )}
      </div>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Camera,
  AlertTriangle,
  RefreshCw,
  ExternalLink,
  Loader2,
  Upload,
} from "lucide-react";
import {
  ApiError,
  GaitMetrics,
  processFrames,
  processVideo,
  JointFrame,
  VideoAnalysis,
} from "../lib/api";
import {
  computeLiveGait,
  legLengthFrom,
  LiveGaitMetrics,
} from "../lib/gait";
import type {
  NormalizedLandmark,
  PoseResults,
} from "../types/mediapipe";

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

// joint key -> pairs for replaying backend joint frames on the canvas
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
    metrics: GaitMetrics,
    source: "live" | "upload",
    frames?: JointFrame[]
  ) => void;
  onProcessingChange?: (busy: boolean) => void;
}

type Mode = "live" | "upload";

export default function WebcamFeed({
  onMetrics,
  onProcessingChange,
}: Props) {
  const [mode, setMode] = useState<Mode>("live");
  const [error, setError] = useState<string | null>(null);
  const [trackingStatus, setTrackingStatus] = useState<TrackingStatus>("idle");
  const [lastSyncAt, setLastSyncAt] = useState<Date | null>(null);
  const [cameraBlocked, setCameraBlocked] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const [liveGait, setLiveGait] = useState<LiveGaitMetrics | null>(null);
  const [embedded, setEmbedded] = useState(false);
  const [legLengthM, setLegLengthM] = useState<number | null>(null);
  const [calibrationCount, setCalibrationCount] = useState(0);
  const [videoDiag, setVideoDiag] = useState("");

  useEffect(() => {
    setEmbedded(window.self !== window.top);
  }, []);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const closePoseRef = useRef<(() => void) | null>(null);
  const rafRef = useRef<number>(0);
  const inFlightRef = useRef(false);
  const loopActiveRef = useRef(false);
  const sendFailedRef = useRef(false);
  const sendStartRef = useRef(0);
  const lastResultsRef = useRef<PoseResults | null>(null);
  const mutedSinceRef = useRef(0);
  const syncIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const resultsTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const diagIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
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
  const uploadAbortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadName, setUploadName] = useState<string | null>(null);
  const [uploadCaption, setUploadCaption] = useState<string | null>(null);
  const onMetricsRef = useRef(onMetrics);
  onMetricsRef.current = onMetrics;
  const onProcessingChangeRef = useRef(onProcessingChange);
  onProcessingChangeRef.current = onProcessingChange;
  const setBusy = useCallback((v: boolean) => {
    setUploading(v);
    onProcessingChangeRef.current?.(v);
  }, []);

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
    sendStartRef.current = 0;
    lastResultsRef.current = null;
    mutedSinceRef.current = 0;
    if (diagIntervalRef.current !== null) {
      clearInterval(diagIntervalRef.current);
      diagIntervalRef.current = null;
    }
    setVideoDiag("");
    const canvas = canvasRef.current;
    canvas?.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
    uploadAbortRef.current?.abort();
    uploadAbortRef.current = null;
    setBusy(false);
    setUploadName(null);
    setUploadCaption(null);
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    closePoseRef.current?.();
    closePoseRef.current = null;
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
  }, [setBusy]);

  // live startup/inference failures stay in live mode: the blocked panel
  // offers Retry camera and Use Upload Video instead
  const failLive = useCallback(
    (message: string) => {
      setError(message);
      stopAll();
      setCameraBlocked(message);
      setTrackingStatus("idle");
    },
    [stopAll]
  );

  const drawSkeleton = useCallback(
    (
      ctx: CanvasRenderingContext2D,
      canvas: HTMLCanvasElement,
      lm: PoseResults["poseLandmarks"]
    ) => {
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
    },
    []
  );

  const handleResults = useCallback(
    (results: PoseResults) => {
      if (resultsTimerRef.current !== null) {
        clearTimeout(resultsTimerRef.current);
        resultsTimerRef.current = null;
      }
      lastResultsRef.current = results;
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
    []
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
            if (isStale()) {
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
      setTrackingStatus("loading-model");
      const initPromise = pose.initialize();
      // single close path: defer until initialization has settled so
      // late-created WASM/WebGL resources are actually released
      const closeAfterInit = () => {
        void initPromise
          .catch(() => undefined)
          .then(() => pose.close())
          .catch(() => undefined);
      };
      closePoseRef.current = closeAfterInit;
      try {
        await withTimeout(initPromise, 90_000, "Model download");
      } catch (err) {
        // stopAll (via failLive) runs closePoseRef
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

      // diagnostics: report live video/track state, and flag a track that
      // stays muted >5s while we are not tracking
      const track = stream.getVideoTracks()[0];
      if (track) {
        track.onended = () => {
          setCameraBlocked("Camera stream ended — retry.");
        };
        track.onmute = () => {
          mutedSinceRef.current = performance.now();
        };
        track.onunmute = () => {
          mutedSinceRef.current = 0;
        };
      }
      const diagSnapshot = () => {
        const base = `video ${video.videoWidth}×${video.videoHeight} · readyState ${video.readyState} · track ${track?.readyState ?? "none"}${track?.muted ? " (muted: no frames from camera)" : ""}`;
        const silentFrames =
          mutedSinceRef.current > 0 &&
          performance.now() - mutedSinceRef.current > 5_000;
        return silentFrames
          ? `${base} — camera is delivering no frames (in use by another app or a virtual camera?)`
          : base;
      };
      diagIntervalRef.current = setInterval(() => {
        setVideoDiag(diagSnapshot());
      }, 500);

      // own render+inference loop at display rate via rAF; we paint the video
      // frame ourselves so rendering never depends on <video> compositing
      loopActiveRef.current = true;
      const tick = () => {
        if (!loopActiveRef.current || isStale()) return;
        if (canvas) {
          const cctx = canvas.getContext("2d");
          if (cctx) {
            if (video.videoWidth && canvas.width !== video.videoWidth) {
              canvas.width = video.videoWidth;
              canvas.height = video.videoHeight;
            }
            cctx.clearRect(0, 0, canvas.width, canvas.height);
            if (video.readyState >= 2) {
              cctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            }
            drawSkeleton(cctx, canvas, lastResultsRef.current?.poseLandmarks);
          }
        }
        if (video.readyState >= 2 && !inFlightRef.current) {
          inFlightRef.current = true;
          sendStartRef.current = performance.now();
          pose
            .send({ image: video })
            .catch((err) => {
              if (!sendFailedRef.current && !isStale()) {
                sendFailedRef.current = true;
                loopActiveRef.current = false;
                console.error("[GaitGuard] pose.send failed", err);
                failLive(
                  `MediaPipe inference failed: ${
                    err instanceof Error ? err.message : String(err)
                  }`
                );
              }
            })
            .finally(() => {
              inFlightRef.current = false;
              sendStartRef.current = 0;
            });
        }
        // hung send: in-flight for >20s
        if (
          sendStartRef.current > 0 &&
          performance.now() - sendStartRef.current > 20_000 &&
          !sendFailedRef.current
        ) {
          sendFailedRef.current = true;
          loopActiveRef.current = false;
          console.error("[GaitGuard] pose.send hung");
          failLive(
            "MediaPipe inference hung: pose.send did not resolve within 20s"
          );
          return;
        }
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);

      // arm watchdog only after the model is loaded and the loop is running
      resultsTimerRef.current = setTimeout(() => {
        if (isStale()) return;
        failLive(
          `Pose model loaded but produced no results in 15 s — ${diagSnapshot()}`
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
            onMetricsRef.current(m, "live", batch.slice(-300));
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
      failLive(
        `Live camera unavailable: ${
          err instanceof Error ? err.message : String(err)
        }`
      );
    }
  }, [failLive, handleResults, stopAll, embedded, drawSkeleton]);

  // skeleton replay for the joint frames returned by /api/process-video
  const playFrames = useCallback(
    (
      canvas: HTMLCanvasElement,
      ctx: CanvasRenderingContext2D,
      frames: JointFrame[],
      fps: number
    ) => {
      // bounds for vertical scaling; x is centered on each frame's hip midpoint
      // (accumulated in a loop: spreading thousands of frames into Math.min
      // overflows the argument list)
      let yMin = Infinity;
      let yMax = -Infinity;
      for (const f of frames) {
        for (const joint of Object.values(f)) {
          if (joint[1] < yMin) yMin = joint[1];
          if (joint[1] > yMax) yMax = joint[1];
        }
      }
      const pad = 20;
      const scale =
        ((canvas.height - 2 * pad) / Math.max(yMax - yMin, 0.01)) * 0.9;
      const toY = (y: number) =>
        canvas.height - pad - (y - yMin) * scale;

      let i = 0;
      let last = 0;
      const frameMs = 1000 / fps;
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
    },
    []
  );

  const analyzeVideo = useCallback(
    async (file: File) => {
      if (!/\.(mp4|mov|webm)$/i.test(file.name)) {
        setError("Unsupported format — upload a .mp4, .mov, or .webm video");
        return;
      }
      const gen = generationRef.current;
      uploadAbortRef.current?.abort();
      const controller = new AbortController();
      uploadAbortRef.current = controller;
      cancelAnimationFrame(rafRef.current);
      setBusy(true);
      setUploadName(file.name);
      setUploadCaption(null);
      setError(null);
      try {
        const analysis: VideoAnalysis = await processVideo(
          file,
          controller.signal
        );
        if (
          gen !== generationRef.current ||
          uploadAbortRef.current !== controller
        )
          return;
        const canvas = canvasRef.current;
        if (canvas) {
          canvas.width = 640;
          canvas.height = 360;
        }
        const ctx = canvas?.getContext("2d");
        if (canvas && ctx) {
          playFrames(canvas, ctx, analysis.frames, analysis.fps);
        }
        if (analysis.metrics.gait_detected) {
          setUploadCaption(
            `Analyzed ${analysis.filename} · ${analysis.frames_processed} frames · fall risk ${analysis.metrics.fall_risk_score.toFixed(2)}`
          );
        } else {
          setUploadCaption(
            `No walking detected in ${analysis.filename} — upload a clip of the patient walking`
          );
        }
        onMetricsRef.current(
          analysis.metrics,
          "upload",
          analysis.frames.slice(-300)
        );
      } catch (err) {
        if (
          (err instanceof DOMException && err.name === "AbortError") ||
          gen !== generationRef.current ||
          uploadAbortRef.current !== controller
        )
          return;
        const status = err instanceof ApiError ? err.status : 0;
        let msg =
          err instanceof Error ? err.message : String(err);
        if (status === 415) {
          msg =
            "Unsupported format — please upload .mp4, .mov or .webm";
        } else if (status === 413) {
          msg = "Video is too large — the limit is 100 MB";
        } else if (status === 503) {
          msg = "Video analysis is unavailable on this server";
        }
        setError(`Video analysis failed: ${msg}`);
      } finally {
        if (uploadAbortRef.current === controller) {
          uploadAbortRef.current = null;
          if (gen === generationRef.current) setBusy(false);
        }
      }
    },
    [playFrames, setBusy]
  );

  useEffect(() => {
    stopAll();
    setCameraBlocked(null);
    if (mode === "live") {
      void startLive();
    } else {
      setTrackingStatus("idle");
    }
    return stopAll;
  }, [mode, retryNonce, startLive, stopAll]);

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
      ? (videoDiag ? `${videoDiag} · ` : "") + (legLengthM
        ? `Calibrated · leg ${legLengthM.toFixed(2)} m · ` +
          (lastSyncAt
            ? `Last sync: ${lastSyncAt.toLocaleTimeString("en-GB", { hour12: false })}`
            : "waiting for frames")
        : `Calibrating baseline… ${calibrationCount}/60`)
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
            aria-checked={mode === "live"}
            onClick={() => {
              setError(null);
              setCameraBlocked(null);
              setMode("live");
            }}
            className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-sm font-medium transition-colors ${
              mode === "live"
                ? "border-emerald-500 bg-emerald-600 text-white"
                : "border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700"
            }`}
          >
            <Camera className="h-4 w-4" />
            Live Camera (MediaPipe Pose)
          </button>
          <button
            role="radio"
            aria-checked={mode === "upload"}
            onClick={() => {
              setError(null);
              setMode("upload");
            }}
            className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2.5 text-sm font-medium transition-colors ${
              mode === "upload"
                ? "border-emerald-500 bg-emerald-600 text-white"
                : "border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700"
            }`}
          >
            <Upload className="h-4 w-4" />
            Upload Video
          </button>
        </div>
        <p className="mt-2 text-xs text-slate-400">
          {mode === "upload"
            ? "Upload a walking video (.mp4/.mov/.webm, ≤100 MB) for server-side pose analysis"
            : "Client-side pose tracking; nothing leaves the browser except joint coordinates"}
        </p>
        {mode === "live" && (
          <div className="mt-2 flex items-center justify-between text-xs">
            <span className="flex items-center gap-1.5 text-slate-300">
              <span className={`h-2 w-2 rounded-full ${statusDot}`} />
              {statusText}
            </span>
            <span className="text-slate-500">{statusDetail}</span>
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
          // hidden from view but still decoding: the canvas paints the frames
          className="pointer-events-none absolute inset-0 h-full w-full -scale-x-100 object-cover opacity-0"
          style={{ display: mode === "live" ? "block" : "none" }}
        />
        {mode === "live" && cameraBlocked && (
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
                  onClick={() => {
                    setCameraBlocked(null);
                    setError(null);
                    setMode("upload");
                  }}
                  className="rounded-md border border-slate-600 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700"
                >
                  Use Upload Video instead
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
            mode === "live" ? "absolute inset-0 -scale-x-100 object-cover" : ""
          }`}
        />
        {mode === "upload" && !uploading && !uploadCaption && (
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const f = e.dataTransfer.files?.[0];
              if (f) void analyzeVideo(f);
            }}
            className="absolute inset-0 flex w-full flex-col items-center justify-center gap-2 border-2 border-dashed border-slate-600 text-slate-400 hover:border-emerald-500 hover:text-slate-200"
          >
            <Upload className="h-8 w-8" />
            <span className="text-sm">
              {uploadName
                ? uploadName
                : "Drag a video here or choose a file"}
            </span>
            <span className="text-xs text-slate-500">
              .mp4, .mov, or .webm — up to 100 MB
            </span>
          </button>
        )}
        {uploading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-slate-300">
            <Loader2 className="h-8 w-8 animate-spin" />
            <span className="text-sm">
              Analysing video… extracting pose landmarks
            </span>
          </div>
        )}
        {mode === "upload" && uploadCaption && (
          <div className="absolute bottom-2 left-2 right-2 flex items-center justify-between gap-2">
            <span className="rounded bg-slate-800/90 px-2 py-0.5 text-[10px] text-slate-200">
              {uploadCaption}
            </span>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="rounded bg-slate-800/90 px-2 py-0.5 text-[10px] font-medium text-emerald-300 hover:bg-slate-700"
            >
              Analyze another video
            </button>
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
          disabled={uploading}
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void analyzeVideo(f);
            e.target.value = "";
          }}
        />
        {mode === "upload" ? (
          <span className="absolute right-2 top-2 rounded bg-sky-600/90 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-white">
            UPLOAD
          </span>
        ) : (
          <span className="absolute right-2 top-2 rounded bg-rose-600/90 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-white">
            LIVE
          </span>
        )}
      </div>

      {mode === "live" && (
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

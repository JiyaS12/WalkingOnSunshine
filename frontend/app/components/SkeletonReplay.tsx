"use client";

import { useEffect, useRef } from "react";
import { JointFrame } from "../lib/api";

const SIM_PAIRS: [string, string][] = [
  ["left_hip", "right_hip"],
  ["left_hip", "left_knee"],
  ["left_knee", "left_ankle"],
  ["right_hip", "right_knee"],
  ["right_knee", "right_ankle"],
];

interface Props {
  frames: JointFrame[];
  fps: number;
  width?: number;
  height?: number;
}

// Same hip-centred scaling / drawing as the dashboard's skeleton replay.
export default function SkeletonReplay({
  frames,
  fps,
  width = 480,
  height = 270,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || frames.length === 0) return;

    canvas.width = width;
    canvas.height = height;
    const ys = frames.flatMap((f) => Object.values(f).map((v) => v[1]));
    const yMin = Math.min(...ys);
    const yMax = Math.max(...ys);
    const pad = 20;
    const scale = ((height - 2 * pad) / Math.max(yMax - yMin, 0.01)) * 0.9;
    const toY = (y: number) => height - pad - (y - yMin) * scale;

    let i = 0;
    let last = 0;
    let raf = 0;
    const frameMs = 1000 / Math.max(fps, 1);
    const loop = (ts: number) => {
      if (ts - last >= frameMs) {
        last = ts;
        const f = frames[i % frames.length];
        i += 1;
        const hipMidX = (f.left_hip[0] + f.right_hip[0]) / 2;
        const toX = (x: number) => width / 2 + (x - hipMidX) * scale;
        ctx.clearRect(0, 0, width, height);
        ctx.strokeStyle = "#8FB4D2";
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
        ctx.fillStyle = "#B3A8DC";
        for (const joint of Object.values(f)) {
          ctx.beginPath();
          ctx.arc(toX(joint[0]), toY(joint[1]), 4, 0, 2 * Math.PI);
          ctx.fill();
        }
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [frames, fps, width, height]);

  return (
    <canvas
      ref={canvasRef}
      width={width}
      height={height}
      className="h-auto w-full rounded-2xl bg-muted shadow-pillow-inset"
    />
  );
}

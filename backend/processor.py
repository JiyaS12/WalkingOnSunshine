"""Gait telemetry processing for GaitGuard AI.

Consumes MediaPipe-style world-coordinate joint positions (meters, y vertical
with larger y = higher) and produces clinical gait metrics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel

JOINTS = (
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)

_EPS = 1e-9


class GaitMetrics(BaseModel):
    stride_length_m: float
    asymmetry_pct: float
    velocity_degradation_pct: float
    fall_risk_score: float
    cadence_steps_per_min: float
    frame_count: int


class GaitProcessor:
    """Computes gait metrics from a sequence of joint-position frames."""

    def __init__(self, frames: list[dict[str, list[float]]], fps: float = 30.0):
        if len(frames) < 2:
            raise ValueError("at least 2 frames are required")
        if fps <= 0:
            raise ValueError("fps must be positive")
        for i, frame in enumerate(frames):
            for joint in JOINTS:
                coords = frame.get(joint)
                if coords is None or len(coords) != 3:
                    raise ValueError(
                        f"frame {i}: joint '{joint}' must have exactly 3 coordinates"
                    )
        self.frames = frames
        self.fps = float(fps)
        self.frame_count = len(frames)

    def _joint_array(self, joint: str) -> np.ndarray:
        return np.array(
            [frame[joint] for frame in self.frames], dtype=float
        )  # (n, 3)

    def _ankle_separation(self) -> np.ndarray:
        left = self._joint_array("left_ankle")
        right = self._joint_array("right_ankle")
        return np.linalg.norm(left - right, axis=1)

    def _find_peaks(self, signal: np.ndarray) -> np.ndarray:
        """Local maxima via neighbor comparison, min spacing ~0.3s."""
        min_spacing = max(1, int(round(0.3 * self.fps)))
        candidates = []
        for i in range(1, len(signal) - 1):
            if signal[i] >= signal[i - 1] and signal[i] > signal[i + 1]:
                candidates.append(i)
        # enforce min spacing: keep strongest candidate within each window
        peaks: list[int] = []
        for idx in candidates:
            if peaks and idx - peaks[-1] < min_spacing:
                if signal[idx] > signal[peaks[-1]]:
                    peaks[-1] = idx
            else:
                peaks.append(idx)
        return np.array(peaks, dtype=int)

    def _stride_length(self, separation: np.ndarray) -> tuple[float, np.ndarray]:
        peaks = self._find_peaks(separation)
        if len(peaks) < 2:
            stride = 2.0 * float(np.max(separation))
        else:
            stride = 2.0 * float(np.mean(separation[peaks]))
        return stride, peaks

    def _asymmetry_pct(self) -> float:
        fractions = []
        for joint in ("left_ankle", "right_ankle"):
            y = self._joint_array(joint)[:, 1]
            ymin, ymax = float(np.min(y)), float(np.max(y))
            rng = ymax - ymin
            threshold = ymin + 0.15 * rng
            stance = np.sum(y <= threshold) / len(y)
            fractions.append(stance)
        left, right = fractions
        mean = (left + right) / 2.0
        return abs(left - right) / max(mean, _EPS) * 100.0

    def _velocity_degradation_pct(self) -> float:
        left_hip = self._joint_array("left_hip")
        right_hip = self._joint_array("right_hip")
        mid = (left_hip + right_hip) / 2.0  # (n, 3)
        dx = np.diff(mid[:, 0])
        dz = np.diff(mid[:, 2])
        speed = np.sqrt(dx**2 + dz**2) * self.fps
        smoothed = (
            pd.Series(speed).rolling(window=5, min_periods=1).mean().to_numpy()
        )
        half = len(smoothed) // 2
        if half == 0:
            return 0.0
        first = float(np.mean(smoothed[:half]))
        second = float(np.mean(smoothed[half:]))
        if first <= _EPS:
            return 0.0
        return max(0.0, (first - second) / first * 100.0)

    def compute(self) -> GaitMetrics:
        separation = self._ankle_separation()
        stride, peaks = self._stride_length(separation)
        asymmetry = self._asymmetry_pct()
        vel_deg = self._velocity_degradation_pct()

        duration_min = self.frame_count / self.fps / 60.0
        cadence = len(peaks) / duration_min if duration_min > 0 else 0.0

        z = (
            1.5 * (asymmetry / 20.0)
            + 1.2 * (vel_deg / 15.0)
            + 1.0 * ((1.2 - stride) / 0.4)
            - 2.0
        )
        score = 1.0 / (1.0 + np.exp(-z))
        score = round(float(np.clip(score, 0.0, 1.0)), 3)

        return GaitMetrics(
            stride_length_m=round(float(stride), 4),
            asymmetry_pct=round(float(asymmetry), 4),
            velocity_degradation_pct=round(float(vel_deg), 4),
            fall_risk_score=score,
            cadence_steps_per_min=round(float(cadence), 2),
            frame_count=self.frame_count,
        )

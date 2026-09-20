"""Gait telemetry processing for Sana.

Consumes MediaPipe-style world-coordinate joint positions (meters, y vertical
with larger y = higher) and produces clinical gait metrics.

Fall risk is normalized by estimated leg length rather than absolute
geriatric thresholds: normal symmetric gait (stride ~= 1.4-1.6x leg length,
knee flexion ROM ~= 50-65 deg) should score < 0.15, and standing still is not
flagged as gait. MediaPipe world coordinates are hip-centred, so velocity
degradation is estimated from ankle swing speed for live input (hip
translation only shows for the translating mock-cohort sessions).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from experimental_risk import EXTENDED_JOINTS, extract_features, score_features

JOINTS = (
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)

_EPS = 1e-9

# above this share of frames missing a joint the clip is too sparse to score
_MAX_DROPPED_PCT = 50.0


class GaitMetrics(BaseModel):
    stride_length_m: float
    asymmetry_pct: float
    velocity_degradation_pct: float
    fall_risk_score: float
    cadence_steps_per_min: float
    frame_count: int
    leg_length_m: float
    stride_ratio: float
    knee_flexion_rom_deg: float
    peak_ankle_speed_mps: float
    gait_detected: bool
    dropped_frame_pct: float = 0.0
    experimental_cv_risk_index: float | None = None
    experimental_cv_risk_status: Literal[
        "scored", "scored_with_warning", "not_scorable", "model_unavailable"
    ] = "model_unavailable"
    experimental_cv_risk_model_version: str = "unavailable"
    experimental_cv_risk_contributors: dict[str, float] = Field(default_factory=dict)
    experimental_cv_risk_warnings: list[str] = Field(default_factory=list)


def validate_frames(frames: list[dict[str, list[float]]]) -> None:
    """Raises ValueError unless every frame has all six JOINTS with exactly
    3 finite coordinates."""
    for i, frame in enumerate(frames):
        for joint in JOINTS:
            coords = frame.get(joint)
            if (
                coords is None
                or len(coords) != 3
                or not all(np.isfinite(c) for c in coords)
            ):
                raise ValueError(
                    f"frame {i}: joint '{joint}' must have exactly 3 "
                    "finite coordinates"
                )


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + np.exp(-z))


class GaitProcessor:
    """Computes gait metrics from a sequence of joint-position frames."""

    def __init__(
        self,
        frames: list[dict[str, list[float]]],
        fps: float = 30.0,
        leg_length_m: float | None = None,
        visibility_frames: list[dict[str, float]] | None = None,
        capture_missing_pct: float = 0.0,
    ):
        if len(frames) < 2:
            raise ValueError("at least 2 frames are required")
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.frames = frames
        self.fps = float(fps)
        self.frame_count = len(frames)
        self.visibility_frames = visibility_frames
        if not np.isfinite(capture_missing_pct) or not 0 <= capture_missing_pct <= 100:
            raise ValueError("capture_missing_pct must be between 0 and 100")
        self.capture_missing_pct = float(capture_missing_pct)
        for i, frame in enumerate(frames):
            for joint in JOINTS:
                coords = frame.get(joint)
                if not isinstance(coords, (list, tuple)) or len(coords) != 3:
                    raise ValueError(
                        f"frame {i}: joint '{joint}' must have exactly 3 coordinates"
                    )
        self._joints, repaired_pct = self._repair_dropped_landmarks()
        self.dropped_frame_pct = max(repaired_pct, self.capture_missing_pct)
        if leg_length_m is not None:
            if not np.isfinite(leg_length_m) or leg_length_m <= 0:
                raise ValueError("leg_length_m must be finite and positive")
            self.leg_length_m = float(leg_length_m)
        else:
            self.leg_length_m = self._leg_length()

    def _repair_dropped_landmarks(self) -> tuple[dict[str, np.ndarray], float]:
        """Interpolate landmarks the tracker dropped, per joint, over time.

        MediaPipe reports NaN (and callers send nulls) for joints it loses
        during a clip. Feeding those straight through poisons every metric: the
        comparisons behind gait_detected all go false, each deficit term zeroes
        out and the risk score collapses to the healthy baseline, so a dropped
        frame reads as a reassuring result. Repair the gaps instead, and report
        how much of the clip needed it.
        """
        joints: dict[str, np.ndarray] = {}
        dropped = np.zeros(self.frame_count, dtype=bool)
        index = np.arange(self.frame_count)
        available_joints = JOINTS + tuple(
            joint
            for joint in EXTENDED_JOINTS
            if all(joint in frame for frame in self.frames)
        )
        for joint in available_joints:
            # None and NaN both arrive as nan here; a non-numeric coordinate
            # raises, which is a malformed request rather than lost tracking
            coords = np.array(
                [frame[joint] for frame in self.frames], dtype=float
            )  # (n, 3)
            valid = np.isfinite(coords).all(axis=1)
            if not valid.any():
                raise ValueError(
                    f"joint '{joint}' has no usable coordinates in any frame"
                )
            if not valid.all():
                dropped |= ~valid
                for axis in range(3):
                    coords[:, axis] = np.interp(
                        index, index[valid], coords[valid, axis]
                    )
            joints[joint] = coords

        dropped_pct = float(dropped.sum()) / self.frame_count * 100.0
        if dropped_pct > _MAX_DROPPED_PCT:
            raise ValueError(
                f"{dropped_pct:.0f}% of frames are missing at least one joint "
                f"(limit {_MAX_DROPPED_PCT:.0f}%) — tracking was too sparse to score"
            )
        return joints, dropped_pct

    def _joint_array(self, joint: str) -> np.ndarray:
        return self._joints[joint]  # (n, 3), dropped landmarks interpolated

    def _leg_length(self) -> float:
        """Mean over frames and both sides of |hip-knee| + |knee-ankle|."""
        lengths = []
        for side in ("left", "right"):
            hip = self._joint_array(f"{side}_hip")
            knee = self._joint_array(f"{side}_knee")
            ankle = self._joint_array(f"{side}_ankle")
            seg = np.linalg.norm(hip - knee, axis=1) + np.linalg.norm(
                knee - ankle, axis=1
            )
            lengths.append(float(np.mean(seg)))
        length = float(np.mean(lengths))
        if length <= _EPS:
            raise ValueError("could not estimate leg length from frames")
        return length

    def _knee_flexion(self, side: str) -> np.ndarray:
        """Per-frame knee flexion (deg): 180 - interior angle at the knee."""
        hip = self._joint_array(f"{side}_hip")
        knee = self._joint_array(f"{side}_knee")
        ankle = self._joint_array(f"{side}_ankle")
        v1 = hip - knee
        v2 = ankle - knee
        dot = np.sum(v1 * v2, axis=1)
        mags = np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1)
        cos = np.clip(dot / np.maximum(mags, _EPS), -1.0, 1.0)
        interior = np.degrees(np.arccos(cos))
        return 180.0 - interior

    def _knee_rom(self, side: str) -> float:
        flex = self._knee_flexion(side)
        return float(np.percentile(flex, 95) - np.percentile(flex, 5))

    def _ankle_speed(self, side: str) -> np.ndarray:
        """Per-frame |Δankle| * fps (m/s)."""
        ankle = self._joint_array(f"{side}_ankle")
        return np.linalg.norm(np.diff(ankle, axis=0), axis=1) * self.fps

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

    def _stance_asymmetry(self) -> float:
        """Difference in stance-phase fraction between the two feet.

        The stance threshold is derived once from both ankles together, so a
        foot with reduced swing height is measured against the same floor as
        the healthy foot. Thresholding each foot against its own range would
        rescale the impaired side and report zero asymmetry.
        """
        left_y = self._joint_array("left_ankle")[:, 1]
        right_y = self._joint_array("right_ankle")[:, 1]
        both = np.concatenate((left_y, right_y))
        floor = float(np.min(both))
        threshold = floor + 0.15 * (float(np.max(both)) - floor)

        left = float(np.sum(left_y <= threshold)) / len(left_y)
        right = float(np.sum(right_y <= threshold)) / len(right_y)
        mean = (left + right) / 2.0
        return abs(left - right) / max(mean, _EPS) * 100.0

    def _velocity_degradation_pct(self) -> float:
        left_hip = self._joint_array("left_hip")
        right_hip = self._joint_array("right_hip")
        mid = (left_hip + right_hip) / 2.0  # (n, 3)
        dx = np.diff(mid[:, 0])
        dz = np.diff(mid[:, 2])
        speed = np.sqrt(dx**2 + dz**2) * self.fps
        # hip-centred inputs (live MediaPipe world coords) barely translate;
        # fall back to mean ankle swing speed as the velocity proxy
        path = float(np.sum(np.linalg.norm(np.diff(mid, axis=0), axis=1)))
        if path < 0.05 * self.leg_length_m:
            speed = (
                self._ankle_speed("left") + self._ankle_speed("right")
            ) / 2.0
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

    def experimental_feature_result(self):
        """Extract the public-data model inputs for runtime and research jobs."""
        return extract_features(
            self._joints,
            fps=self.fps,
            leg_length_m=self.leg_length_m,
            dropped_frame_pct=self.dropped_frame_pct,
            visibility_frames=self.visibility_frames,
        )

    def compute(self) -> GaitMetrics:
        separation = self._ankle_separation()
        peaks = self._find_peaks(separation)
        if len(peaks) >= 2:
            stride = 2.0 * float(np.mean(separation[peaks]))
        else:
            stride = 2.0 * float(np.max(separation))

        rom_l = self._knee_rom("left")
        rom_r = self._knee_rom("right")
        knee_rom = (rom_l + rom_r) / 2.0

        peak_ankle_speed = float(
            np.mean(
                [
                    np.percentile(self._ankle_speed("left"), 95),
                    np.percentile(self._ankle_speed("right"), 95),
                ]
            )
        )

        # gait detected: >=2 stride peaks and ankles actually lift off
        ankle_y_range = float(
            np.mean(
                [
                    np.ptp(self._joint_array(f"{s}_ankle")[:, 1])
                    for s in ("left", "right")
                ]
            )
        )
        gait_detected = bool(
            len(peaks) >= 2 and ankle_y_range > 0.03 * self.leg_length_m
        )

        stance_asym = self._stance_asymmetry()
        rom_asym = abs(rom_l - rom_r) / max((rom_l + rom_r) / 2.0, _EPS) * 100.0
        asymmetry = (stance_asym + rom_asym) / 2.0 if gait_detected else 0.0

        vel_deg = self._velocity_degradation_pct() if gait_detected else 0.0

        duration_min = self.frame_count / self.fps / 60.0
        cadence = len(peaks) / duration_min if duration_min > 0 else 0.0

        stride_ratio = stride / self.leg_length_m
        stride_deficit = float(np.clip((1.4 - stride_ratio) / 0.4, 0.0, 2.0))
        knee_deficit = float(np.clip((40.0 - knee_rom) / 30.0, 0.0, 1.0))
        if not gait_detected:
            asymmetry = 0.0
            stride = 0.0
            stride_ratio = 0.0
            cadence = 0.0
            stride_deficit = 0.0
            knee_deficit = 0.0

        z = (
            1.5 * (asymmetry / 20.0)
            + 1.2 * float(np.clip(vel_deg / 15.0, 0.0, 1.5))
            + 1.5 * stride_deficit
            + 1.0 * knee_deficit
            - 3.0
        )
        score = round(float(np.clip(_sigmoid(z), 0.0, 1.0)), 3)

        experimental_features = self.experimental_feature_result()
        experimental = score_features(experimental_features)

        return GaitMetrics(
            stride_length_m=round(float(stride), 4),
            asymmetry_pct=round(float(asymmetry), 4),
            velocity_degradation_pct=round(float(vel_deg), 4),
            fall_risk_score=score,
            cadence_steps_per_min=round(float(cadence), 2),
            frame_count=self.frame_count,
            leg_length_m=round(self.leg_length_m, 4),
            stride_ratio=round(float(stride_ratio), 4),
            knee_flexion_rom_deg=round(float(knee_rom), 2),
            peak_ankle_speed_mps=round(peak_ankle_speed, 4),
            gait_detected=gait_detected,
            dropped_frame_pct=round(self.dropped_frame_pct, 2),
            experimental_cv_risk_index=experimental.index,
            experimental_cv_risk_status=experimental.status,
            experimental_cv_risk_model_version=experimental.model_version,
            experimental_cv_risk_contributors=experimental.contributors,
            experimental_cv_risk_warnings=experimental.warnings,
        )

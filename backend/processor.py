"""Gait telemetry processing for Sana.

Consumes MediaPipe-style world-coordinate joint positions (meters, y vertical
with larger y = higher) and produces clinical gait metrics.

Fall risk is a logistic regression over biomechanical features:

    ln(p / (1 - p)) = b0 + b1*V_COM + b2*theta_knee + b3*M_knee + b4*omega_knee + ...

where V_COM is centre-of-mass (hip midpoint) velocity, theta_knee the knee
flexion range of motion, M_knee a mass-normalised knee-moment proxy
(shank inertia x knee angular acceleration, since a camera gives no ground
reaction force) and omega_knee the peak knee angular velocity. The trailing
terms carry the gait-quality features the clinician report already shows
(stance/ROM asymmetry, within-walk velocity degradation). Normal symmetric
gait (V ~= 1.2 m/s, ROM ~= 50-65 deg) scores < 0.15; standing still is not
flagged as gait and gets the intercept-only baseline. MediaPipe world
coordinates are hip-centred, so V_COM falls back to ankle swing speed for
live input (hip translation only shows for the translating mock-cohort
sessions).
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

# above this share of frames missing a joint the clip is too sparse to score
_MAX_DROPPED_PCT = 50.0

# Logistic fall-risk coefficients (feature units in comments). Calibrated so
# healthy reference gait (1.2 m/s, 55 deg ROM, 250 deg/s, low moment proxy)
# scores ~0.1 and the impaired mock cohort scores > 0.6.
FALL_RISK_BETA = {
    "intercept": 3.8,
    "v_com": -2.4,  # per m/s
    "theta_knee": -0.05,  # per deg of knee flexion ROM
    "m_knee": 0.9,  # per unit of mass-normalised moment proxy (m^2/s^2)
    "omega_knee": -0.002,  # per deg/s peak knee angular velocity
    "asymmetry": 0.06,  # per % stance/ROM asymmetry
    "velocity_degradation": 0.05,  # per % within-walk slowdown
}
# shank+foot ~ 6% of body mass, radius of gyration ~ 0.3 x shank length
_SHANK_INERTIA_FRACTION = 0.06 * 0.3**2


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
    # None when the metrics came from a client that predates the model
    com_velocity_mps: float | None = None
    knee_angular_velocity_dps: float | None = None
    knee_moment_proxy: float | None = None


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


def fall_risk_logit(
    v_com: float,
    theta_knee: float,
    m_knee: float,
    omega_knee: float,
    asymmetry_pct: float = 0.0,
    velocity_degradation_pct: float = 0.0,
) -> float:
    """ln(p/(1-p)) for the fall-risk logistic model."""
    b = FALL_RISK_BETA
    return (
        b["intercept"]
        + b["v_com"] * v_com
        + b["theta_knee"] * theta_knee
        + b["m_knee"] * m_knee
        + b["omega_knee"] * omega_knee
        + b["asymmetry"] * asymmetry_pct
        + b["velocity_degradation"] * velocity_degradation_pct
    )


class GaitProcessor:
    """Computes gait metrics from a sequence of joint-position frames."""

    def __init__(
        self,
        frames: list[dict[str, list[float]]],
        fps: float = 30.0,
        leg_length_m: float | None = None,
    ):
        if len(frames) < 2:
            raise ValueError("at least 2 frames are required")
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.frames = frames
        self.fps = float(fps)
        self.frame_count = len(frames)
        for i, frame in enumerate(frames):
            for joint in JOINTS:
                coords = frame.get(joint)
                if not isinstance(coords, (list, tuple)) or len(coords) != 3:
                    raise ValueError(
                        f"frame {i}: joint '{joint}' must have exactly 3 coordinates"
                    )
        self._joints, self.dropped_frame_pct = self._repair_dropped_landmarks()
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
        for joint in JOINTS:
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

    def _knee_angular_velocity(self, side: str) -> np.ndarray:
        """Per-frame |d(theta_knee)/dt| (deg/s)."""
        return np.abs(np.diff(self._knee_flexion(side))) * self.fps

    def _peak_knee_angular_velocity(self) -> float:
        return float(
            np.mean(
                [
                    np.percentile(self._knee_angular_velocity(s), 95)
                    for s in ("left", "right")
                ]
            )
        )

    def _knee_moment_proxy(self) -> float:
        """Mass-normalised knee moment proxy (m^2/s^2): I/m * |alpha_knee|.

        Without ground reaction forces the inertial term of inverse dynamics
        is all the camera can see: shank inertia (per kg body mass) times the
        knee angular acceleration, averaged over the walk.
        """
        shank = float(
            np.mean(
                [
                    np.linalg.norm(
                        self._joint_array(f"{s}_knee")
                        - self._joint_array(f"{s}_ankle"),
                        axis=1,
                    ).mean()
                    for s in ("left", "right")
                ]
            )
        )
        inertia = _SHANK_INERTIA_FRACTION * shank**2
        accels = []
        for side in ("left", "right"):
            omega = np.radians(np.diff(self._knee_flexion(side)) * self.fps)
            if len(omega) < 2:
                continue
            smoothed = (
                pd.Series(omega).rolling(window=3, min_periods=1).mean().to_numpy()
            )
            accels.append(np.mean(np.abs(np.diff(smoothed) * self.fps)))
        if not accels:
            return 0.0
        return float(inertia * np.mean(accels))

    def _hip_midpoint(self) -> np.ndarray:
        return (self._joint_array("left_hip") + self._joint_array("right_hip")) / 2.0

    def _hips_translate(self) -> bool:
        """Whether the hip midpoint actually moves through space.

        Hip-centred input (live MediaPipe world coordinates) keeps the midpoint
        near the origin apart from tracker jitter. Judge by the horizontal
        range of a ~0.5 s smoothed midpoint, which jitter cannot inflate no
        matter how long the clip runs.
        """
        mid = self._hip_midpoint()
        window = max(1, int(round(0.5 * self.fps)))
        smoothed = (
            pd.DataFrame(mid[:, [0, 2]])
            .rolling(window=window, min_periods=1)
            .mean()
            .to_numpy()
        )
        extent = float(np.linalg.norm(np.ptp(smoothed, axis=0)))
        return extent >= 0.1 * self.leg_length_m

    def _com_velocity(self) -> float:
        """Mean hip-midpoint speed (m/s); ankle-swing proxy for hip-centred input."""
        step = np.linalg.norm(np.diff(self._hip_midpoint(), axis=0), axis=1)
        if not self._hips_translate():
            # relative to the hips a foot moves backward at ~v in stance and
            # forward at ~2v in swing (plus lift), so its time-averaged speed
            # is ~2x the centre-of-mass velocity
            step = (
                np.linalg.norm(np.diff(self._joint_array("left_ankle"), axis=0), axis=1)
                + np.linalg.norm(np.diff(self._joint_array("right_ankle"), axis=0), axis=1)
            ) / 4.0
        return float(np.mean(step) * self.fps)

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
        mid = self._hip_midpoint()  # (n, 3)
        dx = np.diff(mid[:, 0])
        dz = np.diff(mid[:, 2])
        speed = np.sqrt(dx**2 + dz**2) * self.fps
        # hip-centred inputs (live MediaPipe world coords) barely translate;
        # fall back to mean ankle swing speed as the velocity proxy
        if not self._hips_translate():
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
        v_com = self._com_velocity()
        omega_knee = self._peak_knee_angular_velocity()
        m_knee = self._knee_moment_proxy()
        if gait_detected:
            z = fall_risk_logit(
                v_com=v_com,
                theta_knee=knee_rom,
                m_knee=m_knee,
                omega_knee=omega_knee,
                asymmetry_pct=asymmetry,
                velocity_degradation_pct=vel_deg,
            )
        else:
            # no walk to score: report the low-evidence baseline rather than
            # the zero-velocity extreme of the model
            asymmetry = 0.0
            stride = 0.0
            stride_ratio = 0.0
            cadence = 0.0
            z = -3.0
        score = round(float(np.clip(_sigmoid(z), 0.0, 1.0)), 3)

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
            com_velocity_mps=round(v_com, 4),
            knee_angular_velocity_dps=round(omega_knee, 2),
            knee_moment_proxy=round(m_knee, 4),
        )

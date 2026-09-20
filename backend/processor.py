"""Gait telemetry processing for GaitGuard AI.

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

# plausible stepping rates: the fore-aft ankle swing completes one cycle per
# stride, so a 0.3-3 Hz band spans a slow shuffle to a brisk walk
_STEP_FREQ_HZ = (0.3, 3.0)

# share of the swing's power that must sit at stepping rates to count as
# walking; over 500 simulated standing clips peaked at 0.55 and the weakest
# walk (a 5 cm shuffle) reached 0.75, so the bar sits between them
_MIN_RHYTHM = 0.65


def _smooth(signal: np.ndarray, window: int = 5) -> np.ndarray:
    return (
        pd.Series(signal)
        .rolling(window=window, min_periods=1, center=True)
        .mean()
        .to_numpy()
    )


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

    def _ankle_speed(self, side: str) -> np.ndarray:
        """Per-frame |Δankle| * fps (m/s)."""
        ankle = self._joint_array(f"{side}_ankle")
        return np.linalg.norm(np.diff(ankle, axis=0), axis=1) * self.fps

    def _travel_axis(self) -> np.ndarray:
        """Per-frame unit vector along the walking direction: horizontal and
        perpendicular to the hip line."""
        lateral = self._joint_array("right_hip") - self._joint_array("left_hip")
        lateral = lateral.copy()
        lateral[:, 1] = 0.0
        norm = np.linalg.norm(lateral, axis=1, keepdims=True)
        axis = np.zeros_like(lateral)
        axis[:, 0] = 1.0  # hips edge-on or lost: assume travel along x
        usable = norm[:, 0] > _EPS
        lateral[usable] /= norm[usable]
        up = np.zeros_like(lateral)
        up[:, 1] = 1.0
        axis[usable] = np.cross(lateral, up)[usable]
        return axis

    def _ankle_gap(self) -> np.ndarray:
        """Signed fore-aft distance between the ankles (m).

        Projecting onto the travel axis drops step width and any height
        difference between the feet. A plain 3-D distance folds both into
        stride length and, worse, never falls to zero: two feet planted a
        stride-width apart read as a permanent half-metre of stride, which
        is enough on its own to saturate the stride deficit.
        """
        delta = self._joint_array("left_ankle") - self._joint_array("right_ankle")
        return np.sum(delta * self._travel_axis(), axis=1)

    def _rhythm(self, signal: np.ndarray) -> float:
        """Share of the fore-aft swing's power that sits at stepping rates.

        Amplitude cannot separate walking from standing: tracker jitter pushes
        the ankles further apart than a short shuffling step does. Spectral
        shape can. Jitter is broadband, so only the band's own share of the
        spectrum lands inside it, whereas a walk at any speed concentrates
        nearly all its power there. Pass the unsmoothed signal — smoothing is
        a low-pass filter, so it moves noise power into the band and closes
        the very gap being measured.
        """
        centred = signal - signal.mean()
        if centred.std() < _EPS:
            return 0.0
        power = np.abs(np.fft.rfft(centred * np.hanning(len(centred)))) ** 2
        freqs = np.fft.rfftfreq(len(centred), 1.0 / self.fps)
        total = float(power[1:].sum())  # drop DC: only the varying part counts
        if total <= _EPS:
            return 0.0
        low, high = _STEP_FREQ_HZ
        in_band = power[1:][(freqs[1:] >= low) & (freqs[1:] <= high)]
        return float(in_band.sum() / total)

    def _rhythm_floor(self) -> float:
        """What broadband jitter alone scores on `_rhythm` at this frame rate.

        The stepping band is a fixed width, so the slower the capture the more
        of the spectrum it covers — below roughly 12 fps noise clears the flat
        threshold on its own and the bar has to rise with it.
        """
        nyquist = self.fps / 2.0
        low, high = _STEP_FREQ_HZ
        if nyquist <= low:
            return 1.0
        return (min(high, nyquist) - low) / nyquist

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

        Floor and ceiling come from percentiles rather than min and max: those
        are extremes, so tracker jitter drags them apart, moves the threshold
        and splits the two stance fractions. That alone reported 64% asymmetry
        for a symmetric walk at 4 cm of jitter, against 17% clean.
        """
        left_y = self._joint_array("left_ankle")[:, 1]
        right_y = self._joint_array("right_ankle")[:, 1]
        both = np.concatenate((left_y, right_y))
        floor = float(np.percentile(both, 5))
        threshold = floor + 0.15 * (float(np.percentile(both, 95)) - floor)

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

    def compute(self) -> GaitMetrics:
        raw_gap = self._ankle_gap()
        # smoothed so tracker jitter cannot manufacture peaks, which would
        # both inflate cadence and bias stride toward the noise maxima
        separation = np.abs(_smooth(raw_gap))
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

        # gait detected: the feet lift off, swing fore-aft, and keep repeating.
        # The lift and peak-count conditions are cheap to satisfy by accident —
        # a centimetre of landmark jitter clears both while standing still, and
        # that alone used to saturate the stride and knee deficits and report
        # ~0.8 risk for a stationary subject — so periodicity carries the call.
        ankle_y_range = float(
            np.mean(
                [
                    np.ptp(self._joint_array(f"{s}_ankle")[:, 1])
                    for s in ("left", "right")
                ]
            )
        )
        gait_detected = bool(
            len(peaks) >= 2
            and ankle_y_range > 0.03 * self.leg_length_m
            and self._rhythm(raw_gap)
            >= max(_MIN_RHYTHM, self._rhythm_floor() + 0.15)
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
        )

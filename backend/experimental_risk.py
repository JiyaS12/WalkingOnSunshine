"""Experimental, data-learned CV gait-risk index.

The runtime deliberately contains no hand-authored risk weights.  It extracts
the same six features used by the offline Toronto Older Adults training job and
applies a promoted ridge-regression artifact.  Missing/unpromoted artifacts and
poor-quality trials produce an explicit status instead of a reassuring score.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np


MODEL_PATH = Path(__file__).with_name("models") / "experimental_cv_risk_v1.json"
MODEL_VERSION_UNAVAILABLE = "unavailable"
FEATURE_NAMES = (
    "median_step_time_s",
    "step_time_cv_pct",
    "stance_time_asymmetry_pct",
    "mean_knee_rom_deg",
    "knee_rom_asymmetry_pct",
    "normalized_foot_clearance",
)

EXTENDED_JOINTS = (
    "left_shoulder",
    "right_shoulder",
    "left_heel",
    "right_heel",
    "left_foot_index",
    "right_foot_index",
)

Status = Literal[
    "scored", "scored_with_warning", "not_scorable", "model_unavailable"
]


@dataclass
class FeatureResult:
    status: Status
    features: dict[str, float] | None = None
    warnings: list[str] = field(default_factory=list)
    events: dict[str, list[int]] = field(default_factory=dict)


@dataclass
class RiskResult:
    index: float | None
    status: Status
    model_version: str
    contributors: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _smooth(values: np.ndarray, fps: float) -> np.ndarray:
    window = max(3, int(round(fps * 0.15)))
    if window % 2 == 0:
        window += 1
    if len(values) < window:
        return values.astype(float, copy=True)
    kernel = np.ones(window, dtype=float) / window
    padded = np.pad(values, (window // 2, window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _symmetry(left: float, right: float) -> float:
    return abs(left - right) / max((left + right) / 2.0, 1e-9) * 100.0


def _transitions(
    signal: np.ndarray, fps: float
) -> tuple[list[int], list[tuple[int, int]]]:
    """Return heel-strike frames and complete stance intervals.

    The signal is foot height relative to the ipsilateral hip.  Hysteresis
    avoids counting tracker jitter around the stance/swing boundary.
    """

    low = float(np.percentile(signal, 10))
    high = float(np.percentile(signal, 90))
    amplitude = high - low
    if amplitude <= 1e-6:
        return [], []
    enter_stance = low + 0.25 * amplitude
    enter_swing = low + 0.45 * amplitude
    min_gap = max(1, int(round(0.35 * fps)))

    in_stance = bool(signal[0] <= enter_stance)
    strikes: list[int] = []
    intervals: list[tuple[int, int]] = []
    stance_start: int | None = None
    for idx in range(1, len(signal)):
        if in_stance and signal[idx] >= enter_swing:
            if stance_start is not None and idx > stance_start:
                intervals.append((stance_start, idx))
            in_stance = False
            stance_start = None
        elif not in_stance and signal[idx] <= enter_stance:
            if not strikes or idx - strikes[-1] >= min_gap:
                strikes.append(idx)
                stance_start = idx
            in_stance = True
    return strikes, intervals


def _knee_flexion(hip: np.ndarray, knee: np.ndarray, ankle: np.ndarray) -> np.ndarray:
    first = hip - knee
    second = ankle - knee
    dot = np.sum(first * second, axis=1)
    magnitudes = np.linalg.norm(first, axis=1) * np.linalg.norm(second, axis=1)
    cosine = np.clip(dot / np.maximum(magnitudes, 1e-9), -1.0, 1.0)
    return 180.0 - np.degrees(np.arccos(cosine))


def extract_features(
    joints: dict[str, np.ndarray],
    *,
    fps: float,
    leg_length_m: float,
    dropped_frame_pct: float,
    visibility_frames: list[dict[str, float]] | None = None,
) -> FeatureResult:
    warnings: list[str] = []
    if not np.isfinite(fps) or fps < 15.0:
        return FeatureResult("not_scorable", warnings=["frame rate is below 15 FPS"])
    if not joints:
        return FeatureResult("not_scorable", warnings=["no joint data"])
    if len(next(iter(joints.values()))) < int(round(6.0 * fps)):
        return FeatureResult(
            "not_scorable", warnings=["trial must contain at least 6 seconds"]
        )
    if dropped_frame_pct > 10.0:
        return FeatureResult(
            "not_scorable",
            warnings=["more than 10% of frames required landmark repair"],
        )

    frame_count = len(next(iter(joints.values())))
    if visibility_frames is not None and len(visibility_frames) != frame_count:
        return FeatureResult(
            "not_scorable",
            warnings=["landmark visibility timestamps do not match pose frames"],
        )

    stride = max(1, int(np.ceil(fps / 30.0)))
    stop = min(frame_count, int(round(10 * fps)), 300 * stride)
    joints = {name: values[:stop:stride] for name, values in joints.items()}
    if visibility_frames is not None:
        visibility_frames = visibility_frames[:stop:stride]
    fps /= stride

    required = {
        "left_hip",
        "right_hip",
        "left_knee",
        "right_knee",
        "left_ankle",
        "right_ankle",
    }
    missing = sorted(required.difference(joints))
    if missing:
        return FeatureResult(
            "not_scorable", warnings=[f"missing required joints: {', '.join(missing)}"]
        )
    required.update(joint for joint in EXTENDED_JOINTS if joint in joints)

    if visibility_frames:
        values = [
            float(frame[joint])
            for frame in visibility_frames
            for joint in required
            if joint in frame and np.isfinite(frame[joint])
        ]
        if len(values) < len(visibility_frames) * len(required) * 0.8:
            return FeatureResult(
                "not_scorable", warnings=["landmark visibility data is incomplete"]
            )
        median_visibility = float(np.median(values))
        low_share = float(np.mean(np.asarray(values) < 0.5))
        if median_visibility < 0.6 or low_share > 0.2:
            return FeatureResult(
                "not_scorable", warnings=["lower-body landmark visibility is too low"]
            )
    else:
        warnings.append("landmark visibility was unavailable")

    segment_lengths = []
    for side in ("left", "right"):
        hip = joints[f"{side}_hip"]
        knee = joints[f"{side}_knee"]
        ankle = joints[f"{side}_ankle"]
        segment_lengths.append(
            np.linalg.norm(hip - knee, axis=1)
            + np.linalg.norm(knee - ankle, axis=1)
        )
    lengths = np.concatenate(segment_lengths)
    if float(np.std(lengths) / max(np.mean(lengths), 1e-9)) > 0.10:
        return FeatureResult(
            "not_scorable", warnings=["estimated leg length varied by more than 10%"]
        )

    strikes: dict[str, list[int]] = {}
    stance_intervals: dict[str, list[tuple[int, int]]] = {}
    clearance: dict[str, float] = {}
    rom: dict[str, float] = {}
    for side in ("left", "right"):
        hip_y = joints[f"{side}_hip"][:, 1]
        foot_name = f"{side}_heel" if f"{side}_heel" in joints else f"{side}_ankle"
        if foot_name.endswith("ankle"):
            warnings.append(f"{side} heel landmark unavailable; ankle used as proxy")
        foot_height = _smooth(joints[foot_name][:, 1] - hip_y, fps)
        strikes[side], stance_intervals[side] = _transitions(foot_height, fps)
        clearance[side] = float(
            (np.percentile(foot_height, 90) - np.percentile(foot_height, 10))
            / max(leg_length_m, 1e-9)
        )
        flexion = _knee_flexion(
            joints[f"{side}_hip"],
            joints[f"{side}_knee"],
            joints[f"{side}_ankle"],
        )
        rom[side] = float(np.percentile(flexion, 95) - np.percentile(flexion, 5))

    if min(len(strikes["left"]), len(strikes["right"])) < 3:
        return FeatureResult(
            "not_scorable",
            warnings=warnings + ["fewer than two complete strides were detected per side"],
            events=strikes,
        )
    ordered = sorted(
        [(idx, side) for side, indices in strikes.items() for idx in indices]
    )
    if len(ordered) < 6:
        return FeatureResult(
            "not_scorable",
            warnings=warnings + ["fewer than six alternating steps were detected"],
            events=strikes,
        )
    minimum_opposite_gap = max(1, int(round(0.12 * fps)))
    if any(
        ordered[index][1] != ordered[index - 1][1]
        and ordered[index][0] - ordered[index - 1][0] < minimum_opposite_gap
        for index in range(1, len(ordered))
    ):
        return FeatureResult(
            "not_scorable",
            warnings=warnings + ["left and right foot events were implausibly simultaneous"],
            events=strikes,
        )
    alternation_errors = sum(
        ordered[idx][1] == ordered[idx - 1][1] for idx in range(1, len(ordered))
    )
    if alternation_errors > 1:
        return FeatureResult(
            "not_scorable",
            warnings=warnings + ["detected steps did not alternate consistently"],
            events=strikes,
        )

    step_times = np.diff([idx for idx, _ in ordered]) / fps
    step_times = step_times[(step_times >= 0.20) & (step_times <= 2.0)]
    if len(step_times) < 5:
        return FeatureResult(
            "not_scorable",
            warnings=warnings + ["too few plausible step intervals were detected"],
            events=strikes,
        )
    median_step = float(np.median(step_times))
    mad = float(np.median(np.abs(step_times - median_step)))
    step_cv = 100.0 * 1.4826 * mad / max(median_step, 1e-9)

    stance_medians: dict[str, float] = {}
    for side in ("left", "right"):
        durations = [
            (end - start) / fps for start, end in stance_intervals[side]
            if 0.1 <= (end - start) / fps <= 2.0
        ]
        if len(durations) < 2:
            return FeatureResult(
                "not_scorable",
                warnings=warnings + [f"too few {side} stance intervals were detected"],
                events=strikes,
            )
        stance_medians[side] = float(np.median(durations))

    features = {
        "median_step_time_s": median_step,
        "step_time_cv_pct": step_cv,
        "stance_time_asymmetry_pct": _symmetry(
            stance_medians["left"], stance_medians["right"]
        ),
        "mean_knee_rom_deg": (rom["left"] + rom["right"]) / 2.0,
        "knee_rom_asymmetry_pct": _symmetry(rom["left"], rom["right"]),
        "normalized_foot_clearance": (clearance["left"] + clearance["right"])
        / 2.0,
    }
    return FeatureResult(
        "scored_with_warning" if warnings else "scored",
        features=features,
        warnings=list(dict.fromkeys(warnings)),
        events=strikes,
    )


def load_model(path: Path = MODEL_PATH) -> dict | None:
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not artifact.get("promoted"):
        return None
    if tuple(artifact.get("feature_names", ())) != FEATURE_NAMES:
        return None
    return artifact


def score_features(result: FeatureResult, model: dict | None = None) -> RiskResult:
    if result.features is None:
        return RiskResult(
            None, result.status, MODEL_VERSION_UNAVAILABLE, warnings=result.warnings
        )
    artifact = model if model is not None else load_model()
    if artifact is None:
        return RiskResult(
            None,
            "model_unavailable",
            MODEL_VERSION_UNAVAILABLE,
            warnings=result.warnings + ["no promoted experimental model is installed"],
        )

    vector = np.asarray([result.features[name] for name in FEATURE_NAMES], dtype=float)
    mean = np.asarray(artifact["feature_mean"], dtype=float)
    scale = np.asarray(artifact["feature_scale"], dtype=float)
    coefficients = np.asarray(artifact["coefficients"], dtype=float)
    standardized = (vector - mean) / np.maximum(scale, 1e-9)
    latent = float(artifact["intercept"] + np.dot(standardized, coefficients))
    percentile_x = np.asarray(artifact["percentile_x"], dtype=float)
    percentile_y = np.asarray(artifact["percentile_y"], dtype=float)
    index = float(np.interp(latent, percentile_x, percentile_y))

    warnings = list(result.warnings)
    training_min = np.asarray(artifact.get("training_feature_min", vector), dtype=float)
    training_max = np.asarray(artifact.get("training_feature_max", vector), dtype=float)
    span = np.maximum(training_max - training_min, 1e-9)
    if np.any(vector < training_min - 0.2 * span) or np.any(
        vector > training_max + 0.2 * span
    ):
        warnings.append("one or more features are outside the training reference range")
    contributions = {
        name: round(float(value), 4)
        for name, value in zip(FEATURE_NAMES, standardized * coefficients)
    }
    return RiskResult(
        round(float(np.clip(index, 0.0, 100.0)), 1),
        "scored_with_warning" if warnings else "scored",
        str(artifact["model_version"]),
        contributors=contributions,
        warnings=list(dict.fromkeys(warnings)),
    )

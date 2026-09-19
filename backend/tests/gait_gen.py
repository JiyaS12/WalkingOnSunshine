"""Synthetic sagittal-plane gait sessions for tests.

Compact copy of the leg model from the (removed) data/generate_mock_cohort.py:
two-segment legs driven by a sinusoidal gait cycle, so knee flexion and ankle
lift are physically consistent.
"""

import numpy as np

FPS = 30
FREQ = 1.6  # gait cycles / s
THIGH = 0.45
SHANK = 0.45
HIP_HEIGHT = 0.95
HIP_HALF_WIDTH = 0.09
NOISE_SIGMA = 0.003


def _leg_ext(flexion_deg: float) -> float:
    phi = np.deg2rad(flexion_deg)
    return float(np.sqrt(THIGH**2 + SHANK**2 + 2 * THIGH * SHANK * np.cos(phi)))


def _knee_xy(hip, ankle):
    d = ankle - hip
    dist = float(np.linalg.norm(d))
    dist = min(dist, THIGH + SHANK - 1e-6)
    a = (THIGH**2 - SHANK**2 + dist**2) / (2 * dist)
    h = float(np.sqrt(max(THIGH**2 - a**2, 0.0)))
    mid = hip + a * d / dist
    perp = np.array([-d[1], d[0]]) / dist
    return mid + h * perp


def generate_session(
    step_amp: float,
    flex_amp_l: float,
    flex_amp_r: float,
    lift_bias_r: float,
    speed_decay: float,
    n_frames: int = 300,
    fps: float = FPS,
    seed: int = 42,
) -> dict:
    rng = np.random.default_rng(seed)
    frames = []
    hx = 0.0
    for i in range(n_frames):
        t = i / fps
        frac = i / n_frames
        speed = step_amp * 2 * FREQ * (1.0 - speed_decay * frac)
        hx += speed / fps
        bob = 0.015 * np.sin(4 * np.pi * FREQ * t)

        j: dict[str, list[float]] = {}
        for side, (z_off, amp, bias) in {
            "left": (-HIP_HALF_WIDTH, flex_amp_l, 0.0),
            "right": (HIP_HALF_WIDTH, flex_amp_r, lift_bias_r),
        }.items():
            phase = 2 * np.pi * FREQ * t + (0.0 if side == "left" else np.pi)
            flexion = 5.0 + amp * (0.5 + 0.5 * np.sin(phase))
            d = step_amp * np.sin(phase)
            lift = 0.05 * max(0.0, np.sin(phase) + bias)
            hip = np.array([hx, HIP_HEIGHT + bob])
            ankle = np.array(
                [hx + d, HIP_HEIGHT + bob - _leg_ext(flexion) + lift]
            )
            knee = _knee_xy(hip, ankle)
            j[f"{side}_hip"] = [hip[0], hip[1], z_off]
            j[f"{side}_knee"] = [knee[0], knee[1], z_off]
            j[f"{side}_ankle"] = [ankle[0], ankle[1], z_off]

        for name in j:
            j[name] = [
                round(float(v + rng.normal(0, NOISE_SIGMA)), 4)
                for v in j[name]
            ]
        frames.append(j)
    return {"fps": fps, "frames": frames}


def impaired_session(seed: int = 42, **kw) -> dict:
    """High-risk walk: short stride, asymmetric knees, decaying speed."""
    return generate_session(
        step_amp=0.225, flex_amp_l=22, flex_amp_r=32,
        lift_bias_r=0.45, speed_decay=0.30, seed=seed, **kw,
    )


def recovered_session(seed: int = 49, **kw) -> dict:
    """Healthy walk: ~1.5x leg-length stride, symmetric 55 deg knee flexion."""
    return generate_session(
        step_amp=0.3375, flex_amp_l=55, flex_amp_r=55,
        lift_bias_r=0.0, speed_decay=0.0, seed=seed, **kw,
    )

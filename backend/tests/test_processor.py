import copy
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gait_gen
from processor import GaitMetrics, GaitProcessor, fall_risk_logit


def _flat_frames(n):
    joint = {"left_hip": [0, 1, 0], "right_hip": [0.2, 1, 0],
             "left_knee": [0, 0.5, 0], "right_knee": [0.2, 0.5, 0],
             "left_ankle": [0, 0.1, 0], "right_ankle": [0.2, 0.1, 0]}
    return [dict(joint) for _ in range(n)]


def _walking_frames(n=300, fps=30.0, left_lift=0.30, right_lift=0.30, floor=0.05):
    """Two ankles swinging in antiphase, each clearing the floor by its lift."""
    frames = []
    for i in range(n):
        phase = 2 * math.pi * (i / fps)
        frames.append({
            "left_hip": [0.01 * i, 1.0, -0.09],
            "right_hip": [0.01 * i, 1.0, 0.09],
            "left_knee": [0.01 * i, 0.5, -0.09],
            "right_knee": [0.01 * i, 0.5, 0.09],
            "left_ankle": [0.01 * i,
                           floor + left_lift * max(0.0, math.sin(phase)), -0.09],
            "right_ankle": [0.01 * i,
                            floor + right_lift * max(0.0, math.sin(phase + math.pi)),
                            0.09],
        })
    return frames


def test_compute_returns_valid_metrics():
    metrics = GaitProcessor(_flat_frames(60)).compute()
    assert isinstance(metrics, GaitMetrics)
    assert metrics.frame_count == 60
    assert 0.0 <= metrics.fall_risk_score <= 1.0
    assert metrics.stride_length_m >= 0
    assert metrics.asymmetry_pct >= 0
    assert metrics.velocity_degradation_pct >= 0
    assert metrics.cadence_steps_per_min >= 0


def test_raises_on_too_few_frames():
    with pytest.raises(ValueError):
        GaitProcessor(_flat_frames(1)).compute()
    with pytest.raises(ValueError):
        GaitProcessor([]).compute()


def test_impaired_worse_than_recovered():
    d1 = GaitProcessor(
        gait_gen.impaired_session()["frames"], fps=30.0
    ).compute()
    d14 = GaitProcessor(
        gait_gen.recovered_session()["frames"], fps=30.0
    ).compute()
    assert d1.fall_risk_score > d14.fall_risk_score
    assert d1.asymmetry_pct > d14.asymmetry_pct
    assert d1.stride_length_m < d14.stride_length_m


def test_normal_symmetric_gait_is_low_risk():
    session = gait_gen.recovered_session(seed=7)
    frames = session["frames"][:150]
    metrics = GaitProcessor(frames, fps=session["fps"]).compute()
    assert metrics.gait_detected is True
    assert metrics.fall_risk_score < 0.3


def test_standing_still_not_flagged():
    metrics = GaitProcessor(_flat_frames(90)).compute()
    assert metrics.gait_detected is False
    assert metrics.fall_risk_score < 0.2


def test_settle_then_stand_still_not_flagged():
    frames = _flat_frames(90)
    for i in range(30):
        shift = 0.3 * (1 - i / 30)
        frames[i] = {k: [v[0] + shift, v[1], v[2]] for k, v in frames[i].items()}
    metrics = GaitProcessor(frames).compute()
    assert metrics.gait_detected is False
    assert metrics.velocity_degradation_pct == 0.0
    assert metrics.fall_risk_score < 0.2


def test_leg_length_override_used():
    metrics = GaitProcessor(_flat_frames(90), leg_length_m=0.9).compute()
    assert metrics.leg_length_m == 0.9


def _symmetric_gait_frames(n=150, seed=7):
    return gait_gen.recovered_session(seed=seed)["frames"][:n]


def _hip_centre(frames):
    out = []
    for f in frames:
        mid = [
            (f["left_hip"][i] + f["right_hip"][i]) / 2 for i in range(3)
        ]
        out.append(
            {
                j: [f[j][i] - mid[i] for i in range(3)]
                for j in f
            }
        )
    return out


def test_hip_centred_input_uses_ankle_speed_proxy():
    frames = _hip_centre(_symmetric_gait_frames())
    metrics = GaitProcessor(frames, fps=30.0).compute()
    assert metrics.velocity_degradation_pct < 10
    assert metrics.fall_risk_score < 0.3


def test_hip_centred_slowdown_detected():
    frames = _symmetric_gait_frames(150)
    # time-stretch the second half 2x (repeat each frame)
    stretched = frames[:75] + [f for f in frames[75:] for _ in range(2)]
    metrics = GaitProcessor(_hip_centre(stretched), fps=30.0).compute()
    assert metrics.velocity_degradation_pct > 20


def test_missing_joint_raises():
    with pytest.raises(ValueError):
        GaitProcessor([{}, {}], fps=30.0)


def _ramp_frames(n):
    """Every coordinate advances linearly, so interpolation is exact."""
    frames = []
    for i in range(n):
        t = i * 0.01
        frames.append({
            "left_hip": [t, 1.0, 0.0], "right_hip": [t + 0.2, 1.0, 0.0],
            "left_knee": [t, 0.5, 0.0], "right_knee": [t + 0.2, 0.5, 0.0],
            "left_ankle": [t, 0.1, 0.0], "right_ankle": [t + 0.2, 0.1, 0.0],
        })
    return frames


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None])
def test_dropped_landmark_is_interpolated(bad):
    frames = _ramp_frames(30)
    frames[10]["left_ankle"] = [bad, 0.1, 0.0]
    processor = GaitProcessor(frames, fps=30.0)
    assert processor._joint_array("left_ankle")[10, 0] == pytest.approx(0.10)


@pytest.mark.parametrize("position", ["first", "last"])
def test_dropped_landmark_at_clip_edges(position):
    frames = _ramp_frames(30)
    i = 0 if position == "first" else 29
    frames[i]["left_ankle"] = [float("nan"), 0.1, 0.0]
    processor = GaitProcessor(frames, fps=30.0)
    # no neighbour on one side, so the nearest valid sample carries over
    assert processor._joint_array("left_ankle")[i, 0] == pytest.approx(
        0.01 if position == "first" else 0.28
    )


def test_dropped_landmarks_do_not_mask_risk():
    """The bug this guards: one NaN used to read as a healthy patient."""
    frames = copy.deepcopy(gait_gen.impaired_session()["frames"][:120])
    clean = GaitProcessor(frames, fps=30.0).compute()
    assert clean.gait_detected is True
    assert clean.fall_risk_score > 0.3

    for i in (3, 4, 41):
        frames[i]["left_ankle"][1] = float("nan")
    repaired = GaitProcessor(frames, fps=30.0).compute()
    assert repaired.gait_detected is True
    assert repaired.fall_risk_score == pytest.approx(clean.fall_risk_score, abs=0.1)
    assert repaired.dropped_frame_pct == pytest.approx(2.5)


def test_dropped_frame_pct_is_zero_for_clean_input():
    assert GaitProcessor(_ramp_frames(30), fps=30.0).compute().dropped_frame_pct == 0.0


def test_joint_missing_in_every_frame_raises():
    frames = _ramp_frames(30)
    for frame in frames:
        frame["left_ankle"] = [float("nan")] * 3
    with pytest.raises(ValueError, match="no usable coordinates"):
        GaitProcessor(frames, fps=30.0)


def test_mostly_dropped_clip_raises():
    frames = _ramp_frames(30)
    for frame in frames[:20]:
        frame["right_knee"] = [float("nan"), 0.5, 0.0]
    with pytest.raises(ValueError, match="too sparse"):
        GaitProcessor(frames, fps=30.0)


def test_symmetric_gait_has_near_zero_asymmetry():
    metrics = GaitProcessor(_walking_frames()).compute()
    assert metrics.asymmetry_pct < 1.0


def test_asymmetry_detects_reduced_swing_height():
    """A foot that barely clears the floor must not be rescaled away."""
    metrics = GaitProcessor(
        _walking_frames(left_lift=0.30, right_lift=0.002)
    ).compute()
    assert metrics.asymmetry_pct > 20.0


def test_asymmetry_scales_with_swing_deficit():
    deficits = [
        GaitProcessor(_walking_frames(right_lift=lift)).compute().asymmetry_pct
        for lift in (0.30, 0.15, 0.05)
    ]
    assert deficits[0] < deficits[1] < deficits[2]


def test_asymmetry_detects_static_foot():
    metrics = GaitProcessor(_walking_frames(right_lift=0.0)).compute()
    assert metrics.asymmetry_pct > 20.0


def test_fall_risk_is_logistic_of_features():
    session = gait_gen.impaired_session()
    metrics = GaitProcessor(session["frames"], fps=30.0).compute()
    z = fall_risk_logit(
        v_com=metrics.com_velocity_mps,
        theta_knee=metrics.knee_flexion_rom_deg,
        m_knee=metrics.knee_moment_proxy,
        omega_knee=metrics.knee_angular_velocity_dps,
        asymmetry_pct=metrics.asymmetry_pct,
        velocity_degradation_pct=metrics.velocity_degradation_pct,
    )
    assert metrics.fall_risk_score == pytest.approx(1 / (1 + math.exp(-z)), abs=0.002)


def test_slower_com_velocity_raises_risk():
    assert fall_risk_logit(0.6, 50.0, 0.05, 250.0) > fall_risk_logit(1.2, 50.0, 0.05, 250.0)
    assert fall_risk_logit(1.2, 30.0, 0.05, 250.0) > fall_risk_logit(1.2, 50.0, 0.05, 250.0)
    assert fall_risk_logit(1.2, 50.0, 0.05, 150.0) > fall_risk_logit(1.2, 50.0, 0.05, 250.0)


def test_com_velocity_tracks_hip_translation():
    session = gait_gen.recovered_session(seed=7)
    translating = GaitProcessor(session["frames"][:150], fps=30.0).compute()
    centred = GaitProcessor(_hip_centre(session["frames"][:150]), fps=30.0).compute()
    assert translating.com_velocity_mps > 0.8
    assert centred.com_velocity_mps == pytest.approx(translating.com_velocity_mps, rel=0.15)




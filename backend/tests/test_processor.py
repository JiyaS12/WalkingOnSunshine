import copy
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from processor import GaitMetrics, GaitProcessor
from simulator import get_comparison, load_cohort


def _flat_frames(n):
    joint = {"left_hip": [0, 1, 0], "right_hip": [0.2, 1, 0],
             "left_knee": [0, 0.5, 0], "right_knee": [0.2, 0.5, 0],
             "left_ankle": [0, 0.1, 0], "right_ankle": [0.2, 0.1, 0]}
    return [dict(joint) for _ in range(n)]


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


def test_day1_worse_than_day14():
    comparison = get_comparison()
    d1, d14 = comparison["day_1"], comparison["day_14"]
    assert d1["fall_risk_score"] > d14["fall_risk_score"]
    assert d1["asymmetry_pct"] > d14["asymmetry_pct"]
    assert d1["stride_length_m"] < d14["stride_length_m"]


def test_normal_symmetric_gait_is_low_risk():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))
    import generate_mock_cohort as gen

    session = gen.generate_session(
        14, step_amp=0.3375, flex_amp_l=55, flex_amp_r=55,
        lift_bias_r=0.0, speed_decay=0.0, seed=7,
    )
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
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))
    import generate_mock_cohort as gen

    session = gen.generate_session(
        14, step_amp=0.3375, flex_amp_l=55, flex_amp_r=55,
        lift_bias_r=0.0, speed_decay=0.0, seed=seed,
    )
    return session["frames"][:n]


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


def test_cohort_structure():
    cohort = load_cohort()
    assert cohort["patient_id"] == "RGN-0417"
    assert set(cohort["sessions"]) == {"day_1", "day_14"}
    for session in cohort["sessions"].values():
        assert session["fps"] == 30
        assert len(session["frames"]) == 300
        for joint in ("left_hip", "right_hip", "left_knee", "right_knee",
                      "left_ankle", "right_ankle"):
            assert joint in session["frames"][0]
            assert len(session["frames"][0][joint]) == 3


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
    frames = copy.deepcopy(load_cohort()["sessions"]["day_1"]["frames"][:120])
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

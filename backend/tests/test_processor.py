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

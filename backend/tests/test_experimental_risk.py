import copy
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gait_gen
import experimental_risk
from experimental_risk import FEATURE_NAMES, FeatureResult, score_features
from processor import GaitProcessor


def _processor(session: dict, visibility: float | None = None) -> GaitProcessor:
    visibility_frames = None
    if visibility is not None:
        visibility_frames = [
            {joint: visibility for joint in frame}
            for frame in session["frames"]
        ]
    return GaitProcessor(
        session["frames"],
        fps=session["fps"],
        visibility_frames=visibility_frames,
    )


def _transform(session: dict, scale: float, offset: np.ndarray) -> dict:
    transformed = copy.deepcopy(session)
    for frame in transformed["frames"]:
        for joint, point in frame.items():
            frame[joint] = (np.asarray(point) * scale + offset).tolist()
    return transformed


def _artifact() -> dict:
    return {
        "promoted": True,
        "model_version": "test-v1",
        "feature_names": list(FEATURE_NAMES),
        "feature_mean": [0.5, 5.0, 5.0, 40.0, 5.0, 0.1],
        "feature_scale": [0.1, 2.0, 2.0, 10.0, 2.0, 0.05],
        "coefficients": [0.5, 0.25, 0.25, -0.5, 0.25, -0.25],
        "intercept": 0.0,
        "percentile_x": [-2.0, 0.0, 2.0],
        "percentile_y": [0.0, 50.0, 100.0],
        "training_feature_min": [0.2, 0.0, 0.0, 10.0, 0.0, 0.01],
        "training_feature_max": [1.0, 20.0, 20.0, 80.0, 20.0, 0.3],
    }


def test_symmetric_walk_extracts_all_predefined_features():
    result = _processor(gait_gen.recovered_session()).experimental_feature_result()
    assert result.features is not None
    assert tuple(result.features) == FEATURE_NAMES
    assert len(result.events["left"]) >= 3
    assert len(result.events["right"]) >= 3


def test_translation_and_scale_invariance():
    session = gait_gen.recovered_session()
    baseline = _processor(session).experimental_feature_result().features
    transformed = _processor(
        _transform(session, 1.7, np.array([4.0, -2.0, 3.0]))
    ).experimental_feature_result().features
    assert baseline is not None and transformed is not None
    for feature in FEATURE_NAMES:
        assert transformed[feature] == pytest.approx(baseline[feature], rel=0.02, abs=0.02)


def test_fps_invariance():
    at_30 = _processor(
        gait_gen.recovered_session(n_frames=300, fps=30)
    ).experimental_feature_result().features
    at_60 = _processor(
        gait_gen.recovered_session(n_frames=600, fps=60)
    ).experimental_feature_result().features
    assert at_30 is not None and at_60 is not None
    assert at_60["median_step_time_s"] == pytest.approx(
        at_30["median_step_time_s"], abs=0.04
    )
    assert at_60["mean_knee_rom_deg"] == pytest.approx(
        at_30["mean_knee_rom_deg"], abs=2.0
    )


def test_asymmetric_irregular_and_shuffling_patterns_are_measured():
    symmetric = _processor(
        gait_gen.recovered_session()
    ).experimental_feature_result().features
    asymmetric = _processor(
        gait_gen.impaired_session()
    ).experimental_feature_result().features
    shuffle = _processor(
        gait_gen.generate_session(0.08, 8, 8, 0, 0)
    ).experimental_feature_result().features

    irregular_session = gait_gen.recovered_session()
    sample = np.arange(300)
    warped = np.clip(
        np.rint(sample + 8 * np.sin(2 * np.pi * sample / 100)).astype(int),
        0,
        299,
    )
    original = irregular_session["frames"]
    irregular_session["frames"] = [copy.deepcopy(original[index]) for index in warped]
    irregular = _processor(irregular_session).experimental_feature_result().features

    assert all(item is not None for item in (symmetric, asymmetric, shuffle, irregular))
    assert asymmetric["knee_rom_asymmetry_pct"] > symmetric["knee_rom_asymmetry_pct"]
    assert irregular["step_time_cv_pct"] > symmetric["step_time_cv_pct"]
    assert shuffle["normalized_foot_clearance"] < symmetric["normalized_foot_clearance"]


def test_dropped_frames_within_limit_remain_scorable():
    session = gait_gen.recovered_session()
    for index in (20, 81, 140):
        session["frames"][index]["left_ankle"] = [float("nan")] * 3
    result = _processor(session).experimental_feature_result()
    assert result.features is not None


def test_low_visibility_is_not_scorable():
    result = _processor(
        gait_gen.recovered_session(), visibility=0.2
    ).experimental_feature_result()
    assert result.status == "not_scorable"
    assert result.features is None


def test_misaligned_visibility_timestamps_are_not_scorable():
    session = gait_gen.recovered_session()
    visibility = [{joint: 1.0 for joint in frame} for frame in session["frames"][:-1]]
    result = GaitProcessor(
        session["frames"], fps=session["fps"], visibility_frames=visibility
    ).experimental_feature_result()
    assert result.status == "not_scorable"
    assert result.features is None


def test_stationary_and_insufficient_trials_are_not_scorable():
    session = gait_gen.recovered_session()
    stationary = copy.deepcopy(session)
    stationary["frames"] = [copy.deepcopy(session["frames"][0]) for _ in range(300)]
    assert _processor(stationary).experimental_feature_result().status == "not_scorable"

    short = gait_gen.recovered_session(n_frames=90)
    assert _processor(short).experimental_feature_result().status == "not_scorable"


def test_broken_left_right_alternation_is_not_scorable():
    session = gait_gen.recovered_session()
    for frame in session["frames"]:
        for joint in ("hip", "knee", "ankle"):
            left = np.asarray(frame[f"left_{joint}"], dtype=float)
            right = left.copy()
            right[2] *= -1
            frame[f"right_{joint}"] = right.tolist()
    result = _processor(session).experimental_feature_result()
    assert result.status == "not_scorable"
    assert result.features is None


def test_model_scoring_is_percentile_mapped_and_has_contributions():
    feature_values = dict(zip(FEATURE_NAMES, [0.5, 5.0, 5.0, 40.0, 5.0, 0.1]))
    scored = score_features(FeatureResult("scored", feature_values), _artifact())
    assert scored.index == 50.0
    assert scored.status == "scored"
    assert scored.model_version == "test-v1"
    assert tuple(scored.contributors) == FEATURE_NAMES


def test_missing_model_never_becomes_a_low_score(monkeypatch):
    monkeypatch.setattr(experimental_risk, "load_model", lambda: None)
    values = dict(zip(FEATURE_NAMES, [0.5, 5.0, 5.0, 40.0, 5.0, 0.1]))
    unavailable = score_features(FeatureResult("scored", values), None)
    assert unavailable.index is None
    assert unavailable.status == "model_unavailable"

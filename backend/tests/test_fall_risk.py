import json
import math
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gait_gen  # noqa: E402
from experimental_risk import FEATURE_NAMES, FeatureResult  # noqa: E402
from fall_risk import PROMOTION_CHECKS, display_index, load_model, score_fall_risk  # noqa: E402
from main import app  # noqa: E402
from processor import GaitMetrics  # noqa: E402


def artifact() -> dict:
    return {
        "schema_version": 1, "promoted": True,
        "promotion_checks": {key: True for key in PROMOTION_CHECKS},
        "model_version": "test-only", "feature_protocol": "ankle-six-features-v1",
        "target": "prior_12_month_fall_history", "feature_names": list(FEATURE_NAMES),
        "feature_mean": [0] * 6, "feature_scale": [1] * 6,
        "coefficients": [0] * 6, "intercept": math.log(3),
        "calibration_a": -1, "calibration_b": 0,
        "training_feature_min": [0] * 6, "training_feature_max": [2] * 6,
    }


def features() -> FeatureResult:
    return FeatureResult("scored", dict.fromkeys(FEATURE_NAMES, 1.0))


def test_learned_index_requires_a_promoted_calibrated_artifact(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps(artifact()))
    result = score_fall_risk(features(), 0.1, True, model_path=path)
    assert (result.index, result.status, result.method) == (75, "scored", "learned_fall_history")
    assert result.model_version == "test-only"


@pytest.mark.parametrize("change", [
    {"promoted": False},
    {"promotion_checks": {"rgb_transfer": True}},
    {"promotion_checks": {key: key != "rgb_transfer" for key in PROMOTION_CHECKS}},
    {"coefficients": [1]},
    {"feature_scale": [0] * 6},
    {"coefficients": [math.nan] * 6},
    {"feature_names": list(reversed(FEATURE_NAMES))},
    {"feature_protocol": "heel-features"},
])
def test_invalid_or_unvalidated_artifact_uses_explicit_fallback(tmp_path, change):
    path = tmp_path / "model.json"
    path.write_text(json.dumps(artifact() | change))
    assert load_model(path) is None
    result = score_fall_risk(features(), 0.1, True, model_path=path)
    assert (result.index, result.status, result.method) == (11, "fallback", "heuristic_fallback")


def test_missing_corrupt_and_out_of_range_models_use_fallback(tmp_path):
    path = tmp_path / "model.json"
    for content in (None, "corrupt", json.dumps(artifact() | {"training_feature_max": [0.5] * 6})):
        if content is not None:
            path.write_text(content)
        result = score_fall_risk(features(), 0.5, True, model_path=path)
        assert result.index == 51
        assert result.method == "heuristic_fallback"


@pytest.mark.parametrize("value, expected", [(0, 1), (0.5, 51), (1, 100)])
def test_display_mapping_endpoints(value, expected):
    assert display_index(value) == expected


@pytest.mark.parametrize("payload", [
    {"frames": gait_gen.recovered_session()["frames"][:90], "fps": 30},
    {"frames": gait_gen.recovered_session()["frames"], "fps": 30, "expected_frame_count": 400},
    {"frames": gait_gen.recovered_session()["frames"], "fps": 30,
     "visibility_frames": [{joint: 0.1 for joint in frame} for frame in gait_gen.recovered_session()["frames"]]},
])
def test_api_never_falls_back_for_bad_recordings(payload):
    response = TestClient(app).post("/api/process-frame", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["cv_fall_risk_index"] is None
    assert body["cv_fall_risk_status"] == "not_scorable"
    assert body["cv_fall_risk_method"] == "none"
    assert isinstance(body["fall_risk_score"], float)


def test_api_fallback_preserves_original_score_and_round_trips_history():
    response = TestClient(app).post("/api/process-frame", json=gait_gen.recovered_session())
    assert response.status_code == 200
    body = response.json()
    assert body["cv_fall_risk_method"] == "heuristic_fallback"
    assert body["cv_fall_risk_index"] == display_index(body["fall_risk_score"])
    assert GaitMetrics.model_validate_json(json.dumps(body)).model_dump() == body
    historical = {key: value for key, value in body.items() if not key.startswith("cv_fall_risk_")}
    assert GaitMetrics.model_validate(historical).cv_fall_risk_index is None


def test_rejection_precedes_both_learned_and_fallback_paths(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps(artifact()))
    for result, gait in [(FeatureResult("not_scorable"), True), (features(), False)]:
        score = score_fall_risk(result, 0.5, gait, model_path=path)
        assert score.index is None
        assert score.status == "not_scorable"

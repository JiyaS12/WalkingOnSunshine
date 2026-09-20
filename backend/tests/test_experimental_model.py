import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import experimental_risk  # noqa: E402
import gait_gen  # noqa: E402
from experimental_risk import FEATURE_NAMES, FeatureResult, load_model, score_features  # noqa: E402
from main import app  # noqa: E402


def artifact() -> dict:
    return {
        "promoted": True, "model_version": "test-only",
        "feature_names": list(FEATURE_NAMES),
        "feature_mean": [0.0] * 6, "feature_scale": [1.0] * 6,
        "coefficients": [0.0] * 6, "intercept": 0.0,
        "percentile_x": [-1.0, 0.0, 1.0], "percentile_y": [0.0, 50.0, 100.0],
        "training_feature_min": [0.0] * 6, "training_feature_max": [2.0] * 6,
    }


def features() -> FeatureResult:
    return FeatureResult("scored", dict.fromkeys(FEATURE_NAMES, 1.0))


@pytest.mark.parametrize("field", list(artifact()))
def test_missing_runtime_parameters_are_rejected(tmp_path, field):
    model = artifact()
    del model[field]
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model))
    assert load_model(path) is None
    assert score_features(features(), model).status == "model_unavailable"


@pytest.mark.parametrize("change", [
    {"feature_scale": [1.0] * 5},
    {"feature_mean": [0.0] * 7},
    {"coefficients": [[0.0]] * 6},
    {"training_feature_min": [0.0]},
    {"training_feature_max": [0.0] * 5},
    {"training_feature_min": [3.0] * 6},
    {"feature_scale": [0.0] * 6},
    {"feature_scale": [-1.0] * 6},
    {"feature_mean": [float("nan")] * 6},
    {"intercept": float("inf")},
    {"coefficients": [float("-inf")] * 6},
    {"percentile_x": []},
    {"percentile_x": [-1.0, 1.0]},
    {"percentile_x": [0.0, 0.0, 1.0]},
    {"percentile_x": [1.0, 0.0, -1.0]},
    {"percentile_y": [0.0, 75.0, 50.0]},
    {"percentile_y": [0.0, 50.0, 101.0]},
    {"percentile_y": [-1.0, 50.0, 100.0]},
    {"percentile_y": [0.0, float("nan"), 100.0]},
    {"model_version": "  "},
    {"model_version": 1},
    {"feature_names": list(reversed(FEATURE_NAMES))},
    {"promoted": False},
    {"promoted": "true"},
    {"promoted": 1},
])
def test_malformed_promoted_models_cannot_enter_scoring(tmp_path, change):
    model = artifact() | change
    path = tmp_path / "model.json"
    path.write_text(json.dumps(model))
    assert load_model(path) is None
    result = score_features(features(), model)
    assert result.status == "model_unavailable"
    assert result.index is None
    assert result.contributors == {}


@pytest.mark.parametrize("content", [b"null", b"[]", b"42", b"{", b"\xff"])
def test_non_object_corrupt_or_non_utf8_artifacts_are_unavailable(tmp_path, content):
    path = tmp_path / "model.json"
    path.write_bytes(content)
    assert load_model(path) is None


def test_valid_disk_artifact_still_scores(monkeypatch, tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps(artifact()))
    loaded = load_model(path)
    assert loaded is not None
    monkeypatch.setattr(experimental_risk, "load_model", lambda: loaded)
    result = score_features(features())
    assert (result.index, result.status, result.model_version) == (50.0, "scored", "test-only")
    assert result.contributors == dict.fromkeys(FEATURE_NAMES, 0.0)


def test_numeric_overflow_returns_unavailable_instead_of_nonfinite_contributors():
    result = score_features(features(), artifact() | {"coefficients": [1e308] * 6})
    assert result.status == "model_unavailable"
    assert result.index is None
    assert result.contributors == {}


def test_malformed_toronto_model_does_not_break_api_fallback(tmp_path, monkeypatch):
    path = tmp_path / "model.json"
    path.write_text(json.dumps(artifact() | {"feature_scale": [1.0] * 5}))
    monkeypatch.setattr(experimental_risk, "load_model", lambda: load_model(path))
    response = TestClient(app).post("/api/process-frame", json=gait_gen.recovered_session())
    assert response.status_code == 200
    body = response.json()
    assert body["experimental_cv_risk_status"] == "model_unavailable"
    assert body["experimental_cv_risk_index"] is None
    assert body["cv_fall_risk_method"] == "heuristic_fallback"
    assert 1 <= body["cv_fall_risk_index"] <= 100

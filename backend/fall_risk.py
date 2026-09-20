"""Display index with an explicitly identified learned or heuristic source."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal, Self

import numpy as np
from pydantic import BaseModel, Field, FiniteFloat, model_validator

from experimental_risk import FEATURE_NAMES, FeatureResult

MODEL_PATH = Path(__file__).with_name("models") / "kinecal_fall_history_v1.json"
PROMOTION_CHECKS = {
    "sample_size", "class_size", "coverage", "discrimination",
    "baseline", "calibration", "rgb_transfer",
}


class FallHistoryModel(BaseModel):
    schema_version: Literal[1]
    model_version: str
    feature_protocol: Literal["ankle-six-features-v1"]
    target: Literal["prior_12_month_fall_history"]
    promoted: Literal[True]
    promotion_checks: dict[str, bool]
    feature_names: list[str]
    feature_mean: list[FiniteFloat] = Field(min_length=6, max_length=6)
    feature_scale: list[FiniteFloat] = Field(min_length=6, max_length=6)
    coefficients: list[FiniteFloat] = Field(min_length=6, max_length=6)
    intercept: FiniteFloat
    calibration_a: FiniteFloat
    calibration_b: FiniteFloat
    training_feature_min: list[FiniteFloat] = Field(min_length=6, max_length=6)
    training_feature_max: list[FiniteFloat] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def validate_protocol(self) -> Self:
        if tuple(self.feature_names) != FEATURE_NAMES:
            raise ValueError("incompatible feature order")
        if set(self.promotion_checks) != PROMOTION_CHECKS or not all(self.promotion_checks.values()):
            raise ValueError("model has not passed all promotion gates")
        if min(self.feature_scale) <= 0:
            raise ValueError("feature scales must be positive")
        if any(low > high for low, high in zip(self.training_feature_min, self.training_feature_max)):
            raise ValueError("invalid feature bounds")
        return self


class FallRiskResult(BaseModel):
    index: int | None = Field(default=None, ge=1, le=100)
    status: Literal["scored", "fallback", "not_scorable"]
    method: Literal["learned_fall_history", "heuristic_fallback", "none"]
    model_version: str
    warnings: list[str] = Field(default_factory=list)


def load_model(path: Path = MODEL_PATH) -> FallHistoryModel | None:
    try:
        return FallHistoryModel.model_validate(json.loads(path.read_text()))
    except (OSError, ValueError):
        return None


def display_index(value: float) -> int:
    return int(np.clip(math.floor(1 + 99 * value + 0.5), 1, 100))


def score_fall_risk(
    features: FeatureResult, heuristic: float, gait_detected: bool,
    *, model_path: Path = MODEL_PATH,
) -> FallRiskResult:
    warnings = list(features.warnings)
    if (
        not gait_detected or features.status == "not_scorable" or features.features is None
        or set(features.features) != set(FEATURE_NAMES)
        or not all(math.isfinite(value) for value in features.features.values())
    ):
        return FallRiskResult(
            status="not_scorable", method="none", model_version="unavailable",
            warnings=warnings + ["Retake the recording with sufficient visible walking."],
        )
    model = load_model(model_path)
    if model is not None:
        vector = np.asarray([features.features[name] for name in FEATURE_NAMES])
        if np.all(vector >= model.training_feature_min) and np.all(vector <= model.training_feature_max):
            standardized = (vector - model.feature_mean) / model.feature_scale
            latent = model.intercept + float(standardized @ model.coefficients)
            calibrated = -(model.calibration_a * latent + model.calibration_b)
            probability = 1 / (1 + math.exp(-float(np.clip(calibrated, -40, 40))))
            return FallRiskResult(
                index=display_index(probability), status="scored",
                method="learned_fall_history", model_version=model.model_version,
                warnings=warnings + ["Association with prior falls; not a probability of future falling."],
            )
        warnings.append("Features fall outside the learned model's development range.")
    else:
        warnings.append("The learned model is unavailable or has not passed all validation gates.")
    if not math.isfinite(heuristic) or not 0 <= heuristic <= 1:
        return FallRiskResult(
            status="not_scorable", method="none", model_version="unavailable",
            warnings=warnings + ["The original heuristic could not be calculated."],
        )
    return FallRiskResult(
        index=display_index(heuristic), status="fallback", method="heuristic_fallback",
        model_version="heuristic-display-v1",
        warnings=warnings + ["Heuristic fallback: predictive accuracy is unvalidated. Not a probability."],
    )

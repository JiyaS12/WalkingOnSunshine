import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "research"))

from experimental_risk import FEATURE_NAMES  # noqa: E402
from train_experimental_risk import _find_column, train  # noqa: E402


def _write_inputs(root: Path) -> tuple[Path, Path]:
    participants = np.arange(1, 11, dtype=float)
    windows = []
    for participant in participants.astype(int):
        impairment = (participant - 1) / 9
        for window in range(2):
            jitter = (window - 0.5) * 0.01
            windows.append(
                {
                    "participant_id": str(participant),
                    "video": f"OAW{participant:02d}-test.mp4",
                    "window": window,
                    "median_step_time_s": 0.35 + 0.25 * impairment + jitter,
                    "step_time_cv_pct": 2 + 12 * impairment + jitter,
                    "stance_time_asymmetry_pct": 1 + 15 * impairment + jitter,
                    "mean_knee_rom_deg": 60 - 25 * impairment + jitter,
                    "knee_rom_asymmetry_pct": 1 + 10 * impairment + jitter,
                    "normalized_foot_clearance": 0.18 - 0.10 * impairment + jitter,
                }
            )
    feature_path = root / "features.csv"
    pd.DataFrame(windows).to_csv(feature_path, index=False)

    clinical_path = root / "participants.xlsx"
    pd.DataFrame(
        {
            "Participant No.": participants.astype(int),
            "TUG (s) ": 8 + 8 * participants / 10,
            "POMA-Gait": 13 - 5 * participants / 10,
            "POMA-Balance": 15 - 6 * participants / 10,
            "BBS": 55 - 20 * participants / 10,
        }
    ).to_excel(clinical_path, index=False)
    return feature_path, clinical_path


def test_actual_archive_column_names_are_recognized():
    columns = ["Participant No.", "POMA-Balance", "POMA-Gait", "BBS", "TUG (s) "]
    assert _find_column(columns, ("participant", "subject", "id")) == "Participant No."
    assert _find_column(columns, ("pomagait", "tinetti gait")) == "POMA-Gait"


def test_training_is_deterministic_for_identical_inputs(tmp_path):
    feature_path, clinical_path = _write_inputs(tmp_path)
    first_artifact, first_metrics = train(feature_path, clinical_path)
    second_artifact, second_metrics = train(feature_path, clinical_path)
    first = json.dumps(first_artifact, sort_keys=True).encode()
    second = json.dumps(second_artifact, sort_keys=True).encode()
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert first_metrics == second_metrics
    assert first_artifact["feature_names"] == list(FEATURE_NAMES)


def test_intercept_baseline_is_leave_one_participant_out(tmp_path):
    feature_path, clinical_path = _write_inputs(tmp_path)
    artifact, metrics = train(feature_path, clinical_path)
    assert metrics["participant_count"] == 10
    assert artifact["validation"]["baseline_rmse"] == metrics["baseline_rmse"]
    assert metrics["baseline_rmse"] > 0


def test_event_validation_uses_sensor_reference_and_promotion_thresholds(tmp_path):
    feature_path, clinical_path = _write_inputs(tmp_path)
    features = pd.read_csv(feature_path, dtype={"participant_id": str})
    medians = features.groupby("participant_id", as_index=False)[
        "median_step_time_s"
    ].median()
    reference = medians.rename(
        columns={"median_step_time_s": "reference_step_time_s"}
    )
    reference["reference_cadence_steps_per_min"] = (
        60.0 / reference["reference_step_time_s"]
    )
    artifact, metrics = train(feature_path, clinical_path, reference)
    event = metrics["event_detection"]
    assert event["status"] == "passed"
    assert event["step_time_mae_s"] == 0.0
    assert event["cadence_mae_steps_per_min"] == 0.0
    assert artifact["validation"]["event_detection"] == event

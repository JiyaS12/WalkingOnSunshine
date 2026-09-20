from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

import gait_gen  # noqa: E402
from experimental_risk import FEATURE_NAMES  # noqa: E402
from processor import GaitProcessor  # noqa: E402
from train_fall_history import (  # noqa: E402
    JOINT_MAP, eligible_cohort, extract_recording, parse_skeleton, timestamp, train,
)


def training_rows() -> pd.DataFrame:
    rng = np.random.default_rng(123)
    rows = []
    for i in range(10):
        rows.append({
            "participant_id": f"P{i:02d}", "label": i % 2,
            **dict(zip(FEATURE_NAMES, rng.uniform(0, 1, 6))),
        })
    return pd.DataFrame(rows)


def test_cohort_excludes_young_controls_and_handles_top_coding():
    metadata = pd.DataFrame({
        "part_id": ["a", "b", "c", "d"],
        "age": ["64", "65", ">89", "50"], "group": ["FHm", "FHs", "NF", "HA"],
    })
    result = eligible_cohort(metadata)
    assert result["part_id"].tolist() == ["b", "c"]
    assert result["label"].tolist() == [1, 0]


def test_kinect_parser_preserves_axis_units_tracking_and_both_filename_formats():
    text = "\n".join(f"{name} Tracked 1.2 -0.5 3.0 100 200" for name in JOINT_MAP)
    frame, visibility = parse_skeleton(text.replace("HipLeft Tracked", "HipLeft Inferred"))
    assert frame["left_hip"] == [1.2, -0.5, 3.0]
    assert visibility["left_hip"] == 0
    assert visibility["right_hip"] == 1
    assert timestamp("72057594037946549_63708725667798.txt") == timestamp("63708725667798.txt")
    with pytest.raises(ValueError, match="missing"):
        parse_skeleton("SpineBase Tracked 0 0 0 0 0")


def test_kinect_adapter_matches_runtime_and_does_not_hide_timing_gaps():
    session = gait_gen.recovered_session()
    filenames = [f"{63708725667798 + round(i * 1000 / 30)}.txt" for i in range(300)]
    bodies = [
        "\n".join(
            f"{source} Tracked {' '.join(str(c) for c in frame[target])} 100 200"
            for source, target in JOINT_MAP.items()
        ) for frame in session["frames"]
    ]
    features, _, _ = extract_recording(filenames, bodies)
    reference = GaitProcessor(session["frames"]).experimental_feature_result().features
    assert features == pytest.approx(reference, rel=0.02, abs=0.02)
    retained = [i for i in range(300) if not 80 <= i < 130]
    bad_features, warnings, quality = extract_recording(
        [filenames[i] for i in retained], [bodies[i] for i in retained],
    )
    assert bad_features is None
    assert quality["missing_pct"] > 10
    assert any("repair" in warning for warning in warnings)


def test_training_groups_participants_and_never_promotes_without_rgb_evidence():
    rows = training_rows()
    first, metrics = train(rows, 10)
    second, repeated = train(pd.concat([rows, rows], ignore_index=True), 10)
    assert first == second
    assert metrics == repeated
    assert metrics["accepted_participants"] == 10
    assert len(metrics["folds"]) == 10
    assert first["promoted"] is False
    assert first["promotion_checks"]["rgb_transfer"] is False


def test_held_out_label_does_not_enter_scaling_fit_or_calibration():
    rows = training_rows()
    _, before = train(rows, 10)
    rows.loc[0, "label"] = 1 - rows.loc[0, "label"]
    _, after = train(rows, 10)
    assert before["folds"][0]["probability"] == after["folds"][0]["probability"]
    assert before["folds"][0]["baseline_probability"] == after["folds"][0]["baseline_probability"]


def test_sparse_cohort_can_fit_weights_but_cannot_claim_validation_or_calibration():
    rows = training_rows().iloc[[0, 1, 3, 5]]
    model, metrics = train(rows, 53)
    assert len(model["coefficients"]) == 6
    assert model["fit_status"] == "exploratory_only"
    assert model["promoted"] is False
    assert metrics["class_counts"] == [1, 3]
    assert metrics["status"] == "insufficient_usable_participants"
    assert "auc" not in metrics
    assert "calibration_a" not in model

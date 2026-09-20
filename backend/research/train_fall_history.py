"""KINECAL skeleton-only extraction and participant-held-out fall-history study."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import StringIO
import json
from pathlib import Path
import re
import shutil
import sys
from threading import local

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import LeaveOneOut, StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from urllib3.util.retry import Retry

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from experimental_risk import FEATURE_NAMES  # noqa: E402
from processor import GaitProcessor, JOINTS  # noqa: E402

BASE = "https://physionet.org/files/kinecal/1.0.3/"
MODEL_VERSION = "kinecal-fall-history-logistic-v1"
PROTOCOL = "ankle-six-features-v1"
MIN_FREE_BYTES = 2 * 1024**3
GATES = {
    "participants_min": 40,
    "minority_class_min": 15,
    "rejected_fraction_max": 0.25,
    "auc_min": 0.70,
    "auc_ci_lower_min": 0.50,
    "brier_skill_min": 0.0,
    "brier_skill_ci_lower_min": 0.0,
    "calibration_error_max": 0.10,
}
JOINT_MAP = {
    f"{joint.title()}{side.title()}": f"{side}_{joint}"
    for side in ("left", "right")
    for joint in ("hip", "knee", "ankle")
}


class HTTP(local):
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=3, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504),
        )))


HTTP_CLIENT = HTTP()


def get_text(url: str) -> str:
    response = HTTP_CLIENT.session.get(url, timeout=(15, 60))
    response.raise_for_status()
    return response.text


def links(url: str) -> list[str]:
    return [
        name for name in re.findall(r'href="([^"]+)"', get_text(url))
        if name != "../" and "/" not in name.rstrip("/")
    ]


def eligible_cohort(metadata: pd.DataFrame) -> pd.DataFrame:
    ages = metadata["age"].astype(str).str.strip()
    eligible = (pd.to_numeric(ages, errors="coerce") >= 65) | (ages == ">89")
    cohort = metadata[eligible & metadata["group"].isin(("NF", "FHs", "FHm"))].copy()
    if cohort["part_id"].duplicated().any():
        raise ValueError("participant metadata contains duplicates")
    cohort["label"] = cohort["group"].isin(("FHs", "FHm")).astype(int)
    return cohort


def timestamp(filename: str) -> int:
    match = re.fullmatch(r"(?:\d+_)?(\d+)\.txt", filename)
    if match is None:
        raise ValueError(f"unrecognized skeleton timestamp: {filename}")
    return int(match[1])


def parse_skeleton(text: str) -> tuple[dict[str, list[float]], dict[str, float]]:
    frame: dict[str, list[float]] = {}
    visibility: dict[str, float] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] not in JOINT_MAP:
            continue
        if len(fields) != 7:
            raise ValueError("malformed skeleton joint")
        joint = JOINT_MAP[fields[0]]
        frame[joint] = [float(value) for value in fields[2:5]]
        visibility[joint] = 1.0 if fields[1] == "Tracked" else 0.0
    if set(frame) != set(JOINTS):
        raise ValueError("skeleton is missing a required lower-body joint")
    return frame, visibility


def extract_recording(
    filenames: list[str], bodies: list[str],
) -> tuple[dict[str, float] | None, list[str], dict[str, float]]:
    times = np.asarray([timestamp(name) for name in filenames], dtype=np.int64)
    order = np.argsort(times)
    times = (times[order] - times.min()) / 1000.0
    if len(times) < 2 or np.any(np.diff(times) <= 0):
        raise ValueError("recording has duplicate or insufficient timestamps")
    interval = float(np.median(np.diff(times)))
    fps = min(30.0, 1.0 / interval)
    duration = min(10.0, float(times[-1] + interval))
    grid = np.arange(0.0, duration - interval / 2, 1.0 / fps)
    frames_and_visibility = [parse_skeleton(bodies[i]) for i in order]
    nearest = np.abs(grid[:, None] - times).min(axis=1)
    missing_pct = float(np.mean(nearest > interval * 0.75) * 100)
    joint_values = {}
    vis_values = {}
    for joint in JOINTS:
        raw = np.asarray([frame[joint] for frame, _ in frames_and_visibility])
        joint_values[joint] = np.column_stack([
            np.interp(grid, times, raw[:, axis]) for axis in range(3)
        ])
        raw_visibility = np.asarray([vis[joint] for _, vis in frames_and_visibility])
        right = np.searchsorted(times, grid).clip(0, len(times) - 1)
        left = (right - 1).clip(0, len(times) - 1)
        vis_values[joint] = np.minimum(raw_visibility[left], raw_visibility[right])
    frames = [
        {joint: joint_values[joint][i].tolist() for joint in JOINTS}
        for i in range(len(grid))
    ]
    visibility = [
        {joint: float(vis_values[joint][i]) for joint in JOINTS}
        for i in range(len(grid))
    ]
    processor = GaitProcessor(
        frames, fps=fps, visibility_frames=visibility, capture_missing_pct=missing_pct,
    )
    result = processor.experimental_feature_result()
    return result.features, result.warnings, {
        "fps": fps, "duration_s": duration, "missing_pct": missing_pct,
    }


def extract(output: Path, workers: int) -> tuple[pd.DataFrame, list[dict]]:
    if shutil.disk_usage(output).free < MIN_FREE_BYTES:
        raise RuntimeError("less than 2 GB free disk space")
    metadata_text = get_text(BASE + "register.csv").replace("\r\n", "\n")
    (output / "kinecal_register.csv").write_text(metadata_text)
    cohort = eligible_cohort(pd.read_csv(StringIO(metadata_text)))
    checkpoints = output / "kinecal_checkpoints"
    checkpoints.mkdir(exist_ok=True)
    records = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for participant in cohort.itertuples(index=False):
            if shutil.disk_usage(output).free < MIN_FREE_BYTES:
                raise RuntimeError("less than 2 GB free disk space")
            participant_id = participant.part_id
            checkpoint = checkpoints / f"{participant_id}.json"
            if checkpoint.exists():
                record = json.loads(checkpoint.read_text())
                if record["protocol"] != PROTOCOL:
                    raise ValueError("checkpoint protocol mismatch; use a fresh output directory")
                records.append(record)
                continue
            root = BASE + "kinecal/" + participant_id.removeprefix("SPPB") + "/"
            recordings = [name for name in links(root) if name.endswith("_3m-walk-Front-View/")]
            record = {
                "participant_id": participant_id, "label": int(participant.label),
                "protocol": PROTOCOL, "recordings": [],
            }
            for name in recordings:
                url = root + name + "skel/"
                files = sorted(
                    (name for name in links(url) if name.endswith(".txt")), key=timestamp,
                )
                if not files:
                    raise ValueError(f"no skeleton frames at {url}")
                bodies = list(pool.map(get_text, [url + name for name in files]))
                fingerprint = hashlib.sha256()
                for filename, body in zip(files, bodies):
                    fingerprint.update(filename.encode())
                    fingerprint.update(body.encode())
                features, warnings, quality = extract_recording(files, bodies)
                record["recordings"].append({
                    "url": url, "sha256": fingerprint.hexdigest(),
                    "raw_frames": len(files), "features": features,
                    "warnings": warnings, **quality,
                })
            checkpoint.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
            records.append(record)
            accepted = sum(row["features"] is not None for row in record["recordings"])
            print(f"{participant_id}: {accepted}/{len(recordings)} accepted", flush=True)
    return cohort, records


def classifier() -> CalibratedClassifierCV:
    return CalibratedClassifierCV(
        make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000)),
        method="sigmoid",
        cv=StratifiedKFold(n_splits=2, shuffle=True, random_state=2026),
        ensemble=False,
    )


def train(rows: pd.DataFrame, eligible_count: int) -> tuple[dict, dict]:
    features = rows.groupby("participant_id", sort=True)[list(FEATURE_NAMES)].median()
    labels = rows.groupby("participant_id", sort=True)["label"]
    if (labels.nunique() != 1).any():
        raise ValueError("participant labels are inconsistent")
    y = labels.first().to_numpy(dtype=int)
    x = features.to_numpy(dtype=float)
    counts = np.bincount(y, minlength=2)
    metrics = {
        "eligible_participants": eligible_count,
        "accepted_participants": len(y),
        "class_counts": counts.tolist(),
        "rejected_fraction": 1 - len(y) / max(1, eligible_count),
        "gates": GATES,
        "transfer_validation": {
            "status": "not_evaluated",
            "reason": "No paired Kinect/MediaPipe cohort or independent RGB fall-label cohort.",
        },
    }
    artifact = {
        "schema_version": 1, "model_version": MODEL_VERSION,
        "feature_protocol": PROTOCOL, "feature_names": list(FEATURE_NAMES),
        "target": "prior_12_month_fall_history",
        "source": BASE, "license": "CC0-1.0", "promoted": False,
    }
    if min(counts) < 3:
        metrics["status"] = "insufficient_usable_participants"
        if min(counts) > 0:
            candidate = make_pipeline(
                StandardScaler(), LogisticRegression(C=1.0, max_iter=2000),
            ).fit(x, y)
            scaler = candidate.named_steps["standardscaler"]
            logistic = candidate.named_steps["logisticregression"]
            artifact.update({
                "fit_status": "exploratory_only",
                "calibration_status": "unavailable_insufficient_participants",
                "feature_mean": scaler.mean_.tolist(),
                "feature_scale": scaler.scale_.tolist(),
                "coefficients": logistic.coef_[0].tolist(),
                "intercept": float(logistic.intercept_[0]),
                "training_feature_min": x.min(axis=0).tolist(),
                "training_feature_max": x.max(axis=0).tolist(),
            })
        artifact["validation"] = metrics
        return artifact, metrics
    predictions = np.zeros(len(y))
    baseline = np.zeros(len(y))
    folds = []
    for training, held_out in LeaveOneOut().split(x):
        model = classifier().fit(x[training], y[training])
        predictions[held_out] = model.predict_proba(x[held_out])[:, 1]
        baseline[held_out] = y[training].mean()
        folds.append({
            "participant_id": str(features.index[held_out[0]]),
            "label": int(y[held_out][0]),
            "probability": float(predictions[held_out][0]),
            "baseline_probability": float(baseline[held_out][0]),
        })
    auc = float(roc_auc_score(y, predictions))
    brier = float(brier_score_loss(y, predictions))
    baseline_brier = float(brier_score_loss(y, baseline))
    rng = np.random.default_rng(2026)
    bootstrap = []
    for _ in range(2000):
        sample = rng.integers(0, len(y), size=len(y))
        if len(np.unique(y[sample])) < 2:
            continue
        bs = float(brier_score_loss(y[sample], predictions[sample]))
        bb = float(brier_score_loss(y[sample], baseline[sample]))
        bootstrap.append([roc_auc_score(y[sample], predictions[sample]), 1 - bs / bb])
    intervals = np.quantile(bootstrap, [0.025, 0.975], axis=0)
    bins = np.array_split(np.argsort(predictions), min(5, len(y)))
    calibration_error = float(sum(
        len(group) * abs(np.mean(y[group]) - np.mean(predictions[group]))
        for group in bins
    ) / len(y))
    observed, predicted = calibration_curve(y, predictions, n_bins=5, strategy="quantile")
    metrics.update({
        "status": "evaluated", "auc": auc, "auc_ci": intervals[:, 0].tolist(),
        "brier": brier, "baseline_brier": baseline_brier,
        "brier_skill": 1 - brier / baseline_brier,
        "brier_skill_ci": intervals[:, 1].tolist(),
        "calibration_error": calibration_error,
        "calibration_curve": {"predicted": predicted.tolist(), "observed": observed.tolist()},
        "folds": folds,
    })
    checks = {
        "sample_size": len(y) >= GATES["participants_min"],
        "class_size": min(counts) >= GATES["minority_class_min"],
        "coverage": metrics["rejected_fraction"] <= GATES["rejected_fraction_max"],
        "discrimination": auc >= GATES["auc_min"] and intervals[0, 0] > GATES["auc_ci_lower_min"],
        "baseline": 1 - brier / baseline_brier > GATES["brier_skill_min"]
        and intervals[0, 1] > GATES["brier_skill_ci_lower_min"],
        "calibration": calibration_error <= GATES["calibration_error_max"],
        "rgb_transfer": False,
    }
    final = classifier().fit(x, y).calibrated_classifiers_[0]
    scaler = final.estimator.named_steps["standardscaler"]
    logistic = final.estimator.named_steps["logisticregression"]
    calibration = final.calibrators[0]
    artifact.update({
        "promoted": bool(all(checks.values())),
        "promotion_checks": {key: bool(value) for key, value in checks.items()},
        "feature_mean": scaler.mean_.tolist(), "feature_scale": scaler.scale_.tolist(),
        "coefficients": logistic.coef_[0].tolist(),
        "intercept": float(logistic.intercept_[0]),
        "calibration_a": float(calibration.a_), "calibration_b": float(calibration.b_),
        "training_feature_min": x.min(axis=0).tolist(),
        "training_feature_max": x.max(axis=0).tolist(), "validation": metrics,
    })
    return artifact, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8, choices=range(1, 17))
    parser.add_argument("--reuse-features", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "kinecal_features.csv"
    if args.reuse_features:
        cohort = eligible_cohort(pd.read_csv(args.output / "kinecal_register.csv"))
    else:
        cohort, records = extract(args.output, args.workers)
        rows = pd.DataFrame([
            {"participant_id": record["participant_id"], "label": record["label"],
             **recording["features"]}
            for record in records for recording in record["recordings"]
            if recording["features"] is not None
        ], columns=["participant_id", "label", *FEATURE_NAMES])
        rows.to_csv(path, index=False, float_format="%.17g")
        (args.output / "kinecal_extraction.json").write_text(
            json.dumps(records, indent=2, allow_nan=False) + "\n",
        )
    rows = pd.read_csv(path, float_precision="round_trip")
    artifact, metrics = train(rows, len(cohort))
    artifact["feature_csv_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact["metadata_sha256"] = hashlib.sha256(
        (args.output / "kinecal_register.csv").read_bytes(),
    ).hexdigest()
    artifact["trainer_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    artifact["extractor_sha256"] = hashlib.sha256(
        (BACKEND / "experimental_risk.py").read_bytes(),
    ).hexdigest()
    for name, value in [("kinecal_fall_history_v1.json", artifact),
                        ("kinecal_validation.json", metrics)]:
        (args.output / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: v for k, v in metrics.items() if k != "folds"}, indent=2))
    return 0 if artifact["promoted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

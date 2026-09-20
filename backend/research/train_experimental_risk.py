"""Train the experimental CV risk index from the Toronto gait archive.

The 2.35 GB ZIP is streamed and only one video is materialized at a time.
Raw videos and the archive are never retained.  Run from ``backend`` with the
research requirements installed::

    python research/train_experimental_risk.py

Use ``--reuse-features`` to rebuild only the model after a successful feature
extraction pass.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from sklearn.decomposition import PCA
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error
from sklearn.preprocessing import StandardScaler
from stream_unzip import stream_unzip

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import video  # noqa: E402
from experimental_risk import (  # noqa: E402
    FEATURE_NAMES,
    MODEL_PATH,
    _smooth,
    _step_times,
    _transitions,
)
from processor import GaitProcessor  # noqa: E402


VIDEOS_URL = "https://ndownloader.figshare.com/files/35454242"
PARTICIPANTS_URL = "https://ndownloader.figshare.com/files/35454230"
XSENS_URL = "https://ndownloader.figshare.com/files/35454227"
DATASET_DOI = "10.6084/m9.figshare.c.5515953.v1"
MODEL_VERSION = "toronto-ridge-pc1-v1"
MIN_FREE_BYTES = 2 * 1024**3
MAX_TEMP_VIDEO_BYTES = 1536 * 1024**2
ALPHAS = np.logspace(-3, 3, 25)
RESEARCH_DIR = Path(__file__).resolve().parent
FEATURES_PATH = RESEARCH_DIR / "artifacts" / "toronto_cv_features.csv"
METRICS_PATH = RESEARCH_DIR / "artifacts" / "training_metrics.json"
CHECKPOINT_PATH = RESEARCH_DIR / ".feature-checkpoint.json"
EXTRACTION_PATH = RESEARCH_DIR / "artifacts" / "extraction_metrics.json"


def _extractor_fingerprint() -> str:
    source = b"".join(
        (RESEARCH_DIR.parent / filename).read_bytes()
        for filename in ("experimental_risk.py", "processor.py", "video.py")
    )
    return hashlib.sha256(source + b"mediapipe-0.10.14").hexdigest()


def _feature_provenance(features_path: Path, participants_path: Path) -> dict:
    extraction_path = features_path.with_name("extraction_metrics.json")
    extraction = json.loads(extraction_path.read_text()) if extraction_path.exists() else {}
    return {
        "feature_csv_sha256": hashlib.sha256(features_path.read_bytes()).hexdigest(),
        "clinical_file_sha256": hashlib.sha256(participants_path.read_bytes()).hexdigest(),
        "extractor_sha256": extraction.get("extractor_sha256", _extractor_fingerprint()),
        "runtime_source_sha256": _extractor_fingerprint(),
        "training_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".pending")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _require_space(path: Path) -> None:
    free = _free_bytes(path)
    if free < MIN_FREE_BYTES:
        raise RuntimeError(
            f"training stopped: only {free / 1024**3:.2f} GB free; "
            "at least 2.00 GB is required"
        )


def _download_small(url: str, destination: Path) -> None:
    total = 0
    with requests.get(url, stream=True, timeout=(30, 120)) as response:
        response.raise_for_status()
        with destination.open("wb") as output:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    total += len(chunk)
                    if total > 16 * 1024**2:
                        raise RuntimeError("metadata download exceeded 16 MB")
                    _require_space(destination.parent)
                    output.write(chunk)


def _participant_id(name: str) -> str | None:
    match = re.search(r"OAW\s*0*(\d+)", name, flags=re.IGNORECASE)
    return str(int(match.group(1))) if match else None


def _extract_archive_features(temp_root: Path) -> pd.DataFrame:
    rows: list[dict[str, float | str | int]] = []
    processed_videos: set[str] = set()
    diagnostics: dict[str, dict] = {}
    fingerprint = _extractor_fingerprint()
    if CHECKPOINT_PATH.exists():
        checkpoint = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        if checkpoint.get("extractor_sha256") != fingerprint:
            raise RuntimeError("checkpoint extractor differs; use a fresh checkpoint")
        rows = checkpoint["rows"]
        diagnostics = checkpoint["diagnostics"]
        processed_videos = set(checkpoint.get("processed_videos", []))
        print(
            f"resuming from {len(processed_videos)} processed videos and "
            f"{len(rows)} scorable windows",
            flush=True,
        )

    def save_checkpoint() -> None:
        pd.DataFrame(
            rows,
            columns=["participant_id", "video", "window", *FEATURE_NAMES],
        ).to_csv(FEATURES_PATH, index=False)
        _write_json(CHECKPOINT_PATH, {
            "extractor_sha256": fingerprint,
            "processed_videos": sorted(processed_videos),
            "rows": rows,
            "diagnostics": diagnostics,
        })
        _write_json(EXTRACTION_PATH, {
            "extractor_sha256": fingerprint,
            "processed_videos": sorted(processed_videos),
            "diagnostics": diagnostics,
        })

    _require_space(temp_root)
    with requests.get(VIDEOS_URL, stream=True, timeout=(30, 600)) as response:
        response.raise_for_status()
        chunks = response.iter_content(4 * 1024 * 1024)
        for raw_name, declared_size, unzipped_chunks in stream_unzip(chunks):
            name = raw_name.decode("utf-8", errors="replace")
            suffix = Path(name).suffix.lower()
            participant = _participant_id(name)
            if suffix not in {".mp4", ".mov", ".webm"} or participant is None:
                for _ in unzipped_chunks:
                    pass
                continue
            if name in processed_videos:
                print(f"skipping checkpointed {name}", flush=True)
                for _ in unzipped_chunks:
                    pass
                continue
            if declared_size and declared_size > MAX_TEMP_VIDEO_BYTES:
                raise RuntimeError(
                    f"{name} is {declared_size / 1024**3:.2f} GB, above the "
                    "1.50 GB per-video safety limit"
                )
            _require_space(temp_root)
            temporary = tempfile.NamedTemporaryFile(
                dir=temp_root, suffix=suffix, delete=False
            )
            temporary_path = Path(temporary.name).resolve()
            written = 0
            next_space_check = 32 * 1024**2
            completed_video = False
            video_rows = []
            rejected: dict[str, int] = {}
            window_count = 0
            try:
                for chunk in unzipped_chunks:
                    written += len(chunk)
                    if written > MAX_TEMP_VIDEO_BYTES:
                        raise RuntimeError(f"{name} exceeded the temporary-file limit")
                    temporary.write(chunk)
                    # Check while materializing, not only between entries.  A
                    # corrupt size declaration or concurrent disk usage must
                    # not be able to consume the 2 GB safety reserve.
                    if written >= next_space_check:
                        temporary.flush()
                        _require_space(temp_root)
                        next_space_check += 32 * 1024**2
                temporary.close()
                print(f"processing {name} ({written / 1024**2:.1f} MB)", flush=True)
                for window_index, (frames, visibility, fps, missing_pct) in enumerate(
                    video.extract_frame_windows(str(temporary_path))
                ):
                    _require_space(temp_root)
                    window_count += 1
                    try:
                        processor = GaitProcessor(
                            frames,
                            fps=fps,
                            visibility_frames=visibility,
                            capture_missing_pct=missing_pct,
                        )
                        result = processor.experimental_feature_result()
                    except ValueError as exc:
                        reason = str(exc)
                        rejected[reason] = rejected.get(reason, 0) + 1
                        continue
                    if result.features is None:
                        for reason in result.warnings:
                            rejected[reason] = rejected.get(reason, 0) + 1
                        continue
                    video_rows.append(
                        {
                            "participant_id": participant,
                            "video": Path(name).name,
                            "window": window_index,
                            **result.features,
                        }
                    )
                completed_video = True
            finally:
                temporary.close()
                if temporary_path.parent == temp_root.resolve():
                    temporary_path.unlink(missing_ok=True)
            if completed_video:
                rows.extend(video_rows)
                diagnostics[name] = {
                    "evaluated_windows": window_count,
                    "scorable_windows": len(video_rows),
                    "rejections": rejected,
                }
                processed_videos.add(name)
                save_checkpoint()
                print(
                    f"checkpointed {name}: {len(video_rows)}/{window_count} "
                    f"windows scorable; {len(processed_videos)}/28 videos",
                    flush=True,
                )
    if not rows:
        raise RuntimeError("no scorable 10-second windows were extracted")
    return pd.DataFrame(rows)


def _normalise_column(name: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _find_column(columns: list[object], alternatives: tuple[str, ...]) -> object:
    normalized = {_normalise_column(column): column for column in columns}
    normalized_alternatives = tuple(_normalise_column(item) for item in alternatives)
    for key, original in normalized.items():
        if any(alternative in key for alternative in normalized_alternatives):
            return original
    raise KeyError(f"could not find metadata column matching {alternatives}")


def _load_clinical(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path)
    columns = list(raw.columns)
    id_column = _find_column(columns, ("participant", "subject", "id"))
    tug_column = _find_column(columns, ("tug", "timedupandgo"))
    poma_gait_column = _find_column(columns, ("pomagait", "tinetti gait"))
    poma_balance_column = _find_column(columns, ("pomabalance", "tinetti balance"))
    berg_column = _find_column(columns, ("berg", "bbs"))
    clinical = raw[
        [id_column, tug_column, poma_gait_column, poma_balance_column, berg_column]
    ].copy()
    clinical.columns = ["participant_id", "tug", "poma_gait", "poma_balance", "berg"]
    clinical["participant_id"] = (
        clinical["participant_id"].astype(str).str.extract(r"(\d+)")[0].astype(float)
    )
    clinical = clinical.dropna().copy()
    clinical["participant_id"] = clinical["participant_id"].astype(int).astype(str)
    for column in ("tug", "poma_gait", "poma_balance", "berg"):
        clinical[column] = pd.to_numeric(clinical[column], errors="coerce")
    return clinical.dropna()


def _load_xsens_reference() -> pd.DataFrame:
    """Derive sensor-reference step time and cadence without retaining raw data."""
    archive_bytes = bytearray()
    with requests.get(XSENS_URL, stream=True, timeout=(30, 180)) as response:
        response.raise_for_status()
        for chunk in response.iter_content(1024 * 1024):
            archive_bytes.extend(chunk)
            if len(archive_bytes) > 256 * 1024**2:
                raise RuntimeError("Xsens archive exceeded the 256 MB memory limit")

    rows: list[dict[str, float | str]] = []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in archive.namelist():
            participant = _participant_id(name)
            if participant is None or not name.lower().endswith(".csv"):
                continue
            if archive.getinfo(name).file_size > 128 * 1024**2:
                raise RuntimeError("Xsens entry exceeded the 128 MB limit")
            with archive.open(name) as entry:
                frame = pd.read_csv(entry)
            frame = frame[pd.to_numeric(frame["index"], errors="coerce").notna()]
            # Xsens time codes reset the last field each second.  Counting
            # samples in complete seconds is robust to the partial first/last
            # seconds present in the exported files (100 Hz in this archive).
            samples_per_second = (
                frame["tc"].astype(str).str.slice(0, 8).value_counts()
            )
            complete_seconds = samples_per_second[samples_per_second >= 20]
            if complete_seconds.empty:
                continue
            fps = float(np.median(complete_seconds.to_numpy(dtype=float)))

            strikes: dict[str, list[int]] = {}
            for side in ("R", "L"):
                foot = pd.to_numeric(frame[f"{side}FootZ"], errors="coerce")
                pelvis = pd.to_numeric(frame["PelvisZ"], errors="coerce")
                signal = (foot - pelvis).interpolate(limit_direction="both").to_numpy(dtype=float)
                if not np.isfinite(signal).all():
                    continue
                strikes[side] = _transitions(_smooth(signal, fps), fps)[0]
            ordered = sorted(
                (index, side)
                for side, indices in strikes.items()
                for index in indices
            )
            intervals = _step_times(ordered, fps)
            if len(intervals) < 5:
                continue
            step_time = float(np.median(intervals))
            rows.append(
                {
                    "participant_id": participant,
                    "reference_step_time_s": step_time,
                    "reference_cadence_steps_per_min": 60.0 / step_time,
                }
            )
    if not rows:
        raise RuntimeError("no Xsens reference gait values could be extracted")
    return pd.DataFrame(rows)


def _oriented_clinical(clinical: pd.DataFrame) -> np.ndarray:
    return clinical[["tug", "poma_gait", "poma_balance", "berg"]].to_numpy(
        dtype=float
    ) * np.array([1, -1, -1, -1])


def _clinical_target(clinical: pd.DataFrame) -> tuple[np.ndarray, dict]:
    oriented = _oriented_clinical(clinical)
    scaler = StandardScaler().fit(oriented)
    standardized = scaler.transform(oriented)
    pca = PCA(n_components=1, svd_solver="full").fit(standardized)
    target = pca.transform(standardized)[:, 0]
    if np.corrcoef(target, standardized[:, 0])[0, 1] < 0:
        target = -target
        pca.components_[0] *= -1
    metadata = {
        "clinical_columns": [
            "tug",
            "negative_poma_gait",
            "negative_poma_balance",
            "negative_berg",
        ],
        "clinical_mean": scaler.mean_.tolist(),
        "clinical_scale": scaler.scale_.tolist(),
        "pca_component": pca.components_[0].tolist(),
        "pca_center": pca.mean_.tolist(),
        "pca_explained_variance_ratio": float(pca.explained_variance_ratio_[0]),
    }
    return target, metadata


def _project_clinical(clinical: pd.DataFrame, metadata: dict) -> np.ndarray:
    normalized = (
        _oriented_clinical(clinical) - np.asarray(metadata["clinical_mean"])
    ) / np.asarray(metadata["clinical_scale"])
    return (normalized - np.asarray(metadata["pca_center"])) @ np.asarray(
        metadata["pca_component"]
    )


def _choose_alpha(features: np.ndarray, clinical: pd.DataFrame) -> float:
    folds = []
    for holdout in range(len(clinical)):
        mask = np.arange(len(clinical)) != holdout
        target, metadata = _clinical_target(clinical.iloc[mask])
        truth = float(_project_clinical(clinical.iloc[[holdout]], metadata)[0])
        scaler = StandardScaler().fit(features[mask])
        folds.append((
            scaler.transform(features[mask]),
            scaler.transform(features[[holdout]]),
            target,
            truth,
        ))
    best_alpha = float(ALPHAS[0])
    best_error = math.inf
    for alpha in ALPHAS:
        predictions = []
        truths = []
        for train_features, heldout_features, target, truth in folds:
            model = Ridge(alpha=float(alpha)).fit(train_features, target)
            predictions.append(float(model.predict(heldout_features)[0]))
            truths.append(truth)
        error = mean_squared_error(truths, predictions)
        if error < best_error:
            best_error = error
            best_alpha = float(alpha)
    return best_alpha


def _nested_leave_one_out(
    features: np.ndarray, clinical: pd.DataFrame
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[float]]:
    predictions = np.zeros(len(clinical), dtype=float)
    truths = np.zeros(len(clinical), dtype=float)
    baselines = np.zeros(len(clinical), dtype=float)
    selected_alphas: list[float] = []
    for holdout in range(len(clinical)):
        train = np.arange(len(clinical)) != holdout
        training_clinical = clinical.iloc[train]
        target, metadata = _clinical_target(training_clinical)
        truths[holdout] = _project_clinical(clinical.iloc[[holdout]], metadata)[0]
        baselines[holdout] = float(np.mean(target))
        alpha = _choose_alpha(features[train], training_clinical)
        selected_alphas.append(alpha)
        scaler = StandardScaler().fit(features[train])
        model = Ridge(alpha=alpha).fit(scaler.transform(features[train]), target)
        predictions[holdout] = model.predict(
            scaler.transform(features[[holdout]])
        )[0]
    return predictions, truths, baselines, selected_alphas


def _spearman(left: np.ndarray, right: np.ndarray) -> float:
    left_rank = pd.Series(left).rank(method="average").to_numpy()
    right_rank = pd.Series(right).rank(method="average").to_numpy()
    if np.ptp(left_rank) == 0 or np.ptp(right_rank) == 0:
        return 0.0
    return float(np.corrcoef(left_rank, right_rank)[0, 1])


def train(
    features_path: Path,
    participants_path: Path,
    xsens_reference: pd.DataFrame | None = None,
) -> tuple[dict, dict]:
    windows = pd.read_csv(features_path, dtype={"participant_id": str})
    participant_features = (
        windows.groupby("participant_id", as_index=False)[list(FEATURE_NAMES)].median()
    )
    clinical = _load_clinical(participants_path)
    joined = participant_features.merge(
        clinical, on="participant_id", how="inner", validate="one_to_one"
    ).sort_values("participant_id", key=lambda values: values.astype(int))
    if len(joined) < 8:
        reason = f"only {len(joined)} participants had both scorable video and clinical data; at least 8 are required"
        metrics = {
            "promoted": False,
            "participant_count": int(len(joined)),
            "scorable_window_count": int(len(windows)),
            "promotion_failure_reasons": [reason],
            "validation_status": "insufficient_participants",
        }
        return {
            "schema_version": 1,
            "model_version": MODEL_VERSION,
            "promoted": False,
            "feature_names": list(FEATURE_NAMES),
            "validation": metrics,
            "dataset": {"doi": DATASET_DOI, "license": "CC0"},
            "provenance": _feature_provenance(features_path, participants_path),
        }, metrics
    x = joined[list(FEATURE_NAMES)].to_numpy(dtype=float)
    target, target_metadata = _clinical_target(joined)
    if not np.isfinite(x).all():
        raise ValueError("training features must be finite")
    oof, heldout_targets, baseline_predictions, selected_alphas = _nested_leave_one_out(x, joined)
    rho = _spearman(oof, heldout_targets)
    rmse = float(mean_squared_error(heldout_targets, oof) ** 0.5)
    baseline_rmse = float(
        mean_squared_error(heldout_targets, baseline_predictions) ** 0.5
    )
    event_validation = {
        "status": "not_run",
        "participant_count": 0,
        "step_time_mae_s": None,
        "cadence_mae_steps_per_min": None,
    }
    event_gate = False
    if xsens_reference is not None:
        reference_joined = participant_features.merge(
            xsens_reference.groupby("participant_id", as_index=False).median(numeric_only=True),
            on="participant_id", how="inner", validate="one_to_one",
        )
        if len(reference_joined) >= 8:
            predicted_step_time = reference_joined[
                "median_step_time_s"
            ].to_numpy(dtype=float)
            reference_step_time = reference_joined[
                "reference_step_time_s"
            ].to_numpy(dtype=float)
            step_time_mae = float(
                np.mean(np.abs(predicted_step_time - reference_step_time))
            )
            cadence_mae = float(
                np.mean(
                    np.abs(
                        60.0 / predicted_step_time
                        - reference_joined[
                            "reference_cadence_steps_per_min"
                        ].to_numpy(dtype=float)
                    )
                )
            )
            event_validation = {
                "status": "passed"
                if step_time_mae <= 0.08 and cadence_mae <= 5.0
                else "failed",
                "participant_count": int(len(reference_joined)),
                "step_time_mae_s": step_time_mae,
                "cadence_mae_steps_per_min": cadence_mae,
            }
            event_gate = event_validation["status"] == "passed"
        else:
            event_validation["status"] = "insufficient_reference_matches"
            event_validation["participant_count"] = int(len(reference_joined))
    promoted = bool(
        rho >= 0.50 and rmse < baseline_rmse and event_gate
    )
    failure_reasons = []
    if not np.isfinite(rho) or rho < 0.50:
        failure_reasons.append("LOOCV Spearman is below 0.50 or undefined")
    if rmse >= baseline_rmse:
        failure_reasons.append("LOOCV RMSE did not beat the intercept-only baseline")
    if not event_gate:
        failure_reasons.append("Xsens timing/cadence agreement did not meet the promotion gate")

    alpha = _choose_alpha(x, joined)
    scaler = StandardScaler().fit(x)
    model = Ridge(alpha=alpha).fit(scaler.transform(x), target)
    ordered, unique_indices, counts = np.unique(np.sort(oof), return_index=True, return_counts=True)
    percentile = 100 * (unique_indices + (counts - 1) / 2) / (len(oof) - 1)
    artifact = {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "promoted": promoted,
        "feature_names": list(FEATURE_NAMES),
        "feature_mean": scaler.mean_.tolist(),
        "feature_scale": scaler.scale_.tolist(),
        "coefficients": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "percentile_x": ordered.tolist(),
        "percentile_y": percentile.tolist(),
        "training_feature_min": np.min(x, axis=0).tolist(),
        "training_feature_max": np.max(x, axis=0).tolist(),
        "dataset": {
            "name": "Toronto Older Adults Gait Archive",
            "doi": DATASET_DOI,
            "license": "CC0",
            "xsens_file_url": XSENS_URL,
            "videos_file_url": VIDEOS_URL,
            "clinical_file_url": PARTICIPANTS_URL,
        },
        "provenance": _feature_provenance(features_path, participants_path),
        "participant_count": int(len(joined)),
        "ridge_alpha": alpha,
        "target": target_metadata,
        "validation": {
            "loocv_spearman": rho,
            "loocv_rmse": rmse,
            "baseline_rmse": baseline_rmse,
            "event_detection": event_validation,
        },
    }
    metrics = {
        "promoted": promoted,
        "promotion_failure_reasons": failure_reasons,
        "participant_count": int(len(joined)),
        "scorable_window_count": int(len(windows)),
        "loocv_spearman": rho,
        "loocv_rmse": rmse,
        "baseline_rmse": baseline_rmse,
        "event_detection": event_validation,
        "outer_selected_alphas": selected_alphas,
        "final_alpha": alpha,
        "validation_method": "Nested participant LOOCV; clinical scaler/PCA and feature scaler fit within every fold",
        "reference_comparison": "Participant median step-time/cadence proxies; not synchronized event annotations",
        "participant_ids": joined["participant_id"].tolist(),
        "heldout_targets": heldout_targets.tolist(),
        "out_of_fold_predictions": oof.tolist(),
        "baseline_predictions": baseline_predictions.tolist(),
    }
    return artifact, metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-features", action="store_true")
    args = parser.parse_args()
    FEATURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache_root = RESEARCH_DIR / ".cache"
    cache_root.mkdir(exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="gaitguard-toronto-", dir=cache_root))
    participants_path = temp_root / "participants.xlsx"
    try:
        _require_space(temp_root)
        print(f"free space: {_free_bytes(temp_root) / 1024**3:.2f} GB", flush=True)
        _download_small(PARTICIPANTS_URL, participants_path)
        if not args.reuse_features:
            features = _extract_archive_features(temp_root)
            features.to_csv(FEATURES_PATH, index=False)
            print(f"saved {len(features)} scorable windows to {FEATURES_PATH}")
        elif not FEATURES_PATH.exists():
            raise RuntimeError(f"feature file does not exist: {FEATURES_PATH}")

        xsens_reference = _load_xsens_reference()
        xsens_reference.to_csv(RESEARCH_DIR / "artifacts" / "xsens_reference.csv", index=False)
        artifact, metrics = train(
            FEATURES_PATH, participants_path, xsens_reference
        )
        encoded = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
        MODEL_PATH.write_text(encoded, encoding="utf-8")
        METRICS_PATH.write_text(
            json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(metrics, indent=2, sort_keys=True))
        print(f"artifact sha256: {hashlib.sha256(encoded.encode()).hexdigest()}")
        if not artifact["promoted"]:
            print("model did not meet the promotion gate; runtime will report unavailable")
            return 2
        return 0
    finally:
        resolved = temp_root.resolve()
        expected_parent = cache_root.resolve()
        if resolved.parent == expected_parent and resolved.name.startswith("gaitguard-toronto-"):
            shutil.rmtree(resolved, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

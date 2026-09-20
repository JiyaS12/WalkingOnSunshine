# Experimental CV risk model

## Current 1–100 fall-risk index

The API's `cv_fall_risk_index` is separate from the Toronto experiment below.
`cv_fall_risk_method` identifies `learned_fall_history`, `heuristic_fallback`,
or `none`; `cv_fall_risk_status` is `scored`, `fallback`, or `not_scorable`.
Historical records have no new score. The original `fall_risk_score` remains
unchanged.

For scorable recordings, the fallback is
`clamp(floor(1 + 99 * fall_risk_score + 0.5), 1, 100)`.
Its label explicitly says that predictive accuracy is unvalidated. It is a
display transformation, with the same ranking and limitations as the original
heuristic. Insufficient walking, excessive missingness, poor visibility, or
implausible gait events produce no index and require a retake.

The learned path applies standardized logistic coefficients followed by sigmoid
calibration, then the same 1–100 mapping. Higher means stronger association with
the development cohort's **reported prior falls**, not prospective fall
probability. No clinical severity bands are assigned to either index.

### KINECAL study and measured outcome

Source: [KINECAL v1.0.3](https://physionet.org/content/kinecal/1.0.3/),
[dataset paper](https://www.nature.com/articles/s41597-023-02375-w), CC0-1.0.
Of 90 participants, we selected 53 aged at least 65 (including the top-coded
`>89` age): 33 `NF` non-fallers, 12 `FHs` single fallers, and 8 `FHm` multiple
fallers. `FHs`/`FHm` mean reported falls within the prior 12 months. Younger
participants and the healthy-adult comparison group were excluded.

`train_fall_history.py` fetches only metadata and timestamped front-view 3 m
walk skeleton text. It never downloads the 86 GB archive or videos. Skeleton
coordinates are in meters; timestamp differences are milliseconds. The first
up-to-ten seconds are resampled to a uniform grid at at most 30 Hz. Capture gaps
and Kinect tracking flags feed the existing quality gates.

The `ankle-six-features-v1` protocol uses bilateral hips, knees, and ankles for
both KINECAL and runtime scoring; the foot-clearance feature is an ankle proxy.
It shares the six-feature extractor with Toronto but does not mix Toronto's
additional foot landmarks into this model. A synthetic coordinate/timestamp
adapter test verifies numerical parity, **not real Kinect-to-webcam validity**.

Only **4 of 53** participants passed (1 non-faller, 3 fallers; **92.45% rejected
or missing a matching recording**). This failed coverage and sample-size gates.
An exploratory regularized logistic fit is retained with learned coefficients,
but held-out discrimination and calibration cannot be meaningfully estimated
with a single non-faller. They are reported as unavailable, not as passing.
The candidate has `promoted: false` and is stored only under research artifacts;
there is no deployed KINECAL model. The application therefore uses the labeled
fallback for otherwise scorable recordings.

For a sufficiently large usable cohort, the implemented evaluation groups all
recordings by participant (median features), holds out one participant at a time,
and fits both scaling and sigmoid calibration using training participants only.
The fixed model is `StandardScaler` + logistic regression (`C=1`), with
two-fold stratified training-only calibration. Reports include participant
predictions, AUC, Brier score against a training-prevalence baseline, calibration
error, and 2,000 participant bootstrap percentile intervals. Bootstrap intervals
over out-of-fold predictions are exploratory and do not replace independent
validation.

Promotion requires all of:

| Check | Required |
| --- | ---: |
| Usable participants | ≥ 40 |
| Smaller class | ≥ 15 |
| Participant rejection/missing-recording fraction | ≤ 25% |
| Participant-held-out AUC | ≥ 0.70 |
| AUC bootstrap lower bound | > 0.50 |
| Brier skill and its bootstrap lower bound | > 0 |
| Five-bin calibration error | ≤ 0.10 |
| Independent RGB/MediaPipe transfer validation | passed |

**RGB transfer remains unevaluated and fails promotion unconditionally.** Passing
Kinect-only metrics would not enable the model. A future transfer study must
validate both measurement agreement and fall-history performance on unseen
webcam participants before implementing that gate. Prospective outcomes would
still be required to claim future-fall probability.

### Reproduce KINECAL fitting or extraction

From the repository root, using the research dependencies already listed below:

```bash
python backend/research/train_fall_history.py \
  --output backend/research/artifacts/kinecal --reuse-features
```

Exit **2** means the study completed with promotion rejected. Exit 1 indicates
an error; it must not be treated as validation. To perform fresh skeleton
extraction, omit `--reuse-features`; `--workers 8` bounds concurrent downloads.
Participant checkpoints resume interruptions. Use a fresh output directory
after any extractor, adapter, or protocol change; do not reuse old checkpoints.
The 2 GB free-space floor is checked before extraction and each participant.
Only metadata, derived features, source hashes, and quality reports remain on
disk. Local checkpoints are ignored by Git.

Committed research artifacts in `artifacts/kinecal/` include the metadata and
feature CSVs, per-recording rejection audit, validation report, and unpromoted
candidate. The artifact records source URL/license and feature, metadata,
extractor, and trainer hashes. Repeated fitting from the CSV produces identical
model and validation files. A future deployed artifact belongs at
`backend/models/kinecal_fall_history_v1.json`; the strict runtime loader requires
the exact protocol, feature order, finite parameters, and every promotion check.
Absent, corrupt, unpromoted, or out-of-range models use the fallback.

## Toronto mobility-proxy experiment

`train_experimental_risk.py` learns the experimental 0–100 index from the
CC0 Toronto Older Adults Gait Archive. It streams the video ZIP, keeps only one
temporary video at a time, and removes that file immediately after feature
extraction. The raw archive and videos must never be committed.

The target is the first principal component of risk-oriented TUG, POMA gait,
POMA balance, and Berg scores. Ridge feature coefficients, PCA loadings, and
the percentile mapping are all learned from the data. A model is promoted only
when nested leave-one-participant-out predictions have Spearman correlation at
least 0.50 and beat the intercept-only RMSE. Promotion also requires agreement
with the archive's Xsens reference: step-time MAE at most 0.08 seconds and
cadence MAE at most 5 steps/min across at least eight matched participants.

This is a small-cohort hackathon experiment, not a validated probability of a
future fall.

Install and run from the repository root with Python 3.12:

```bash
python -m pip install -r backend/requirements-research.txt
python backend/research/train_experimental_risk.py
```

The job requires at least 2 GB free before and throughout extraction. It
checks the active temporary file every 32 MB, checkpoints aggregate windows
after each video, and resumes checkpointed videos after an interruption. A
successful pass keeps only:

- `artifacts/toronto_cv_features.csv` (deidentified window features)
- `artifacts/training_metrics.json` (nested participant-level CV results)
- `../models/experimental_cv_risk_v1.json` (runtime artifact)

The runtime loads the artifact only when its `promoted` flag is true. A failed
validation gate therefore produces `model_unavailable`, never a low score.
After feature extraction, `--reuse-features` can rerun deterministic model
training without streaming the videos again.

## Recorded public-data result

All 28 videos were processed. Quality gates accepted 26 of 191 evaluated
windows, representing 12 of 14 participants. Nine participants matched the
Xsens reference. The committed artifact is **not promoted**:

| Check | Measured | Required |
| --- | ---: | ---: |
| Nested participant-LOOCV Spearman | 0.3818 | ≥ 0.50 |
| Nested participant-LOOCV RMSE | 3.2169 | < baseline 2.7308 |
| Xsens step-time MAE | 0.0392 s | ≤ 0.08 s |
| Xsens cadence MAE | 7.6746 steps/min | ≤ 5 steps/min |
| Matched Xsens participants | 9 | ≥ 8 |

These results do not support enabling the experimental number. Runtime
therefore returns a null index and `model_unavailable` for otherwise scorable
trials. The original heuristic remains independent.

`artifacts/training_metrics.json` includes participant IDs, out-of-fold
predictions and targets, fold-specific ridge choices, and promotion-failure
reasons. `artifacts/extraction_metrics.json` records per-video rejection
reasons; `artifacts/xsens_reference.csv` records the derived reference values.
The Xsens comparison uses participant median proxies, not synchronized,
independently annotated heel-strike events. It does not establish event-level
accuracy.

All features and Xsens references were regenerated with the extractor from
commit `61c7cae`. Step timing uses only adjacent opposite-foot events; a
same-foot interval is a stride and cannot count toward the minimum five
plausible step intervals. CV and Xsens share this timing calculation.
The model records matching extraction and runtime fingerprints, the trainer
source fingerprint, clinical spreadsheet hash, and feature CSV hash.

To reproduce model fitting from the committed features:

```bash
python backend/research/train_experimental_risk.py --reuse-features
sha256sum backend/models/experimental_cv_risk_v1.json
```

Exit code **2** means validation completed but promotion was rejected. Other
errors must not be interpreted as a completed validation run. Two consecutive
retraining passes produced identical model and metrics hashes; the runtime
artifact SHA-256 is:

```
30c011a66221eef824614e46e8308a52791b45b85205db6f0a7be98b91fca9f3
```

For a new extraction after changing source, move the old
`.feature-checkpoint.json` aside first. The trainer refuses to mix checkpoints
from different extractor versions. No raw Toronto videos or archives are
retained in the repository.

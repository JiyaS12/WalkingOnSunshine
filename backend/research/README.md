# Experimental CV risk model

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
| Nested participant-LOOCV Spearman | 0.0876 | ≥ 0.50 |
| Nested participant-LOOCV RMSE | 3.1918 | < baseline 2.7308 |
| Xsens step-time MAE | 0.0465 s | ≤ 0.08 s |
| Xsens cadence MAE | 9.0137 steps/min | ≤ 5 steps/min |
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

The feature CSV records the extraction fingerprint from its checkpoint.
The model separately records the runtime and trainer source fingerprints,
clinical spreadsheet hash, and feature CSV hash. Extraction ran before the
final runtime input-validation/capture-loss fixes and 300-sample cap, using
the extractor from commit `17285a0`. Those
fixes are redundant for the extractor's already-bounded, finite, complete
landmark windows; both fingerprints are retained rather than relabeling the
saved features as a new extraction run.

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
fc778c211606097976164d5a3694426879a25d5654188b0ad12e6f33b9f30a99
```

For a new extraction after changing source, move the old
`.feature-checkpoint.json` aside first. The trainer refuses to mix checkpoints
from different extractor versions. No raw Toronto videos or archives are
retained in the repository.

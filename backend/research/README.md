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

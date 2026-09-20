import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import train_experimental_risk as training


def test_heldout_clinical_outlier_cannot_change_its_prediction_or_alpha():
    rng = np.random.default_rng(42)
    features = rng.normal(size=(8, 6))
    clinical = pd.DataFrame(rng.normal(size=(8, 4)), columns=["tug", "poma_gait", "poma_balance", "berg"])
    first = training._nested_leave_one_out(features, clinical)
    clinical.loc[0, "tug"] = 10000
    second = training._nested_leave_one_out(features, clinical)
    assert first[0][0] == pytest.approx(second[0][0], abs=1e-12)
    assert first[3][0] == second[3][0]
    assert first[1][0] != second[1][0]


def test_space_floor_stops_before_download(monkeypatch, tmp_path):
    monkeypatch.setattr(training, "_free_bytes", lambda _: training.MIN_FREE_BYTES - 1)
    with pytest.raises(RuntimeError, match="at least 2.00 GB"):
        training._extract_archive_features(tmp_path)

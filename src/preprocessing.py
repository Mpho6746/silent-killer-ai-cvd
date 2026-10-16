"""
Loading and cleaning for each raw dataset.

Every loader returns the dataset's own columns (cleaned, no engineered features)
plus one shared target column, `at_risk`, where 1 = coronary heart disease
present. Normalising the label here means nothing downstream has to know how
each source file happened to encode it.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"


def _check_clean(df, name):
    """Both datasets ship complete; fail loudly if a future file doesn't."""
    missing = df.isna().sum()
    if missing.any():
        raise ValueError(f"{name}: unexpected missing values\n{missing[missing > 0]}")
    return df


def load_uci_heart(path=RAW_DIR / "heart.csv"):
    """
    UCI / Cleveland heart disease data (303 patients, Kaggle export).

    In this export `target=0` is the disease-positive class: patients with
    target=0 have more blocked vessels (`ca` 1.17 vs 0.36), more exercise
    angina and a lower max heart rate. We flip it so 1 = at risk.
    Outliers are kept for the reasons given in 01_preprocessing.ipynb.
    """
    df = pd.read_csv(path)
    df["at_risk"] = (df.pop("target") == 0).astype(int)
    return _check_clean(df, "uci_heart")


def load_sa_heart(path=RAW_DIR / "SAHeart.csv"):
    """
    South African Heart Disease data (462 men, Western Cape, Coronary
    Risk-Factor Study, 1980s). `chd` is already 1 = coronary heart disease.
    `famhist` is text ("Present"/"Absent") and becomes 1/0. `row.names` is
    just the original row index, so it is dropped.

    Like the UCI data, extreme readings (sbp up to 218, ldl up to 15.3) are
    clinically plausible high-risk values, not entry errors, so they are kept.
    """
    df = pd.read_csv(path).drop(columns="row.names")
    df["famhist"] = (df["famhist"] == "Present").astype(int)
    df = df.rename(columns={"chd": "at_risk"})
    return _check_clean(df, "sa_heart")

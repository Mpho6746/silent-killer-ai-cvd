"""
Dataset registry: the single place that tells the system which datasets it can use.

Adding a new cardiovascular dataset means writing a loader (preprocessing.py),
optionally a feature function (feature_engineering.py), and adding one
DatasetSpec below. Training, evaluation and the bias audit then work on it
unchanged, because they only talk to `get_dataset()`.
"""
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from src.feature_engineering import add_sa_features, add_uci_features, age_band
from src.preprocessing import load_sa_heart, load_uci_heart


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    title: str
    source: str
    population: str
    load: Callable[[], pd.DataFrame]
    engineer: Callable[[pd.DataFrame], pd.DataFrame]
    # Builds the subgroup labels for the bias audit (never fed to the model)
    groups: Callable[[pd.DataFrame], pd.DataFrame]
    # Harmonised name -> this dataset's column, for features both datasets measure
    # the same way. Used to test whether a model transfers between datasets.
    shared: dict = field(default_factory=dict)


def _uci_groups(df):
    return pd.DataFrame({
        "sex": df["sex"].map({1: "Male", 0: "Female"}),
        "age_band": age_band(df["age"]),
    })


def _sa_groups(df):
    return pd.DataFrame({
        "age_band": age_band(df["age"]),
        "family_history": df["famhist"].map({1: "Present", 0: "Absent"}),
    })


DATASETS = {
    "uci_heart": DatasetSpec(
        key="uci_heart",
        title="UCI Cleveland Heart Disease",
        source="Cleveland Clinic Foundation (1988), via UCI / Kaggle",
        population="303 US hospital patients referred for angiography; 68% male, ages 29-77",
        load=load_uci_heart,
        engineer=add_uci_features,
        groups=_uci_groups,
        shared={"age": "age", "systolic_bp": "trestbps"},
    ),
    "sa_heart": DatasetSpec(
        key="sa_heart",
        title="South African Heart Disease",
        source="Coronary Risk-Factor Study, Western Cape (Rousseauw et al., 1983)",
        population="462 men from a high-risk rural region of the Western Cape; ages 15-64; "
                   "case-control sample (about 2 controls per CHD case)",
        load=load_sa_heart,
        engineer=add_sa_features,
        groups=_sa_groups,
        shared={"age": "age", "systolic_bp": "sbp"},
    ),
}


def get_dataset(key):
    """
    Returns (X, y, groups) for a registered dataset:
      X      - model features (original + engineered)
      y      - at_risk (1 = coronary heart disease)
      groups - subgroup labels for the bias audit, row-aligned with X
    """
    spec = DATASETS[key]
    raw = spec.load()
    df = spec.engineer(raw)
    y = df.pop("at_risk")
    return df, y, spec.groups(raw)


def get_shared_features(key):
    """Only the features every dataset shares, renamed to common names (for transfer tests)."""
    spec = DATASETS[key]
    X, y, groups = get_dataset(key)
    shared = X[list(spec.shared.values())].rename(columns={v: k for k, v in spec.shared.items()})
    return shared, y, groups

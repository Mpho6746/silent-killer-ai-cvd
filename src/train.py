"""
Trains and compares the same three models on any registered dataset.

    python -m src.train                 # every dataset in the registry
    python -m src.train sa_heart        # just one

Models are saved to models/<dataset>/ so they never overwrite the
models/*.pkl files the Streamlit app currently loads.
"""
import json
import sys
from dataclasses import dataclass

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_validate, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from src.datasets import DATASETS, get_dataset
from src.evaluate import SEED, classification_metrics
from src.preprocessing import ROOT

MODELS_DIR = ROOT / "models"
SCORING = ["accuracy", "precision", "recall", "f1", "roc_auc"]


def make_models(pos_weight):
    """
    Same three model families as 03_training.ipynb. Each is a Pipeline with
    its own scaler, so a saved model is self-contained (no separate scaler.pkl).
    Class weighting compensates for imbalance: SAHeart is only 35% positive, and
    without it the models trade recall (our priority metric) for accuracy.
    """
    return {
        "logistic_regression": Pipeline([
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=1000, class_weight="balanced")),
        ]),
        "random_forest": Pipeline([
            ("scale", StandardScaler()),
            ("model", RandomForestClassifier(n_estimators=300, min_samples_leaf=3,
                                             class_weight="balanced", random_state=SEED)),
        ]),
        "xgboost": Pipeline([
            ("scale", StandardScaler()),
            ("model", XGBClassifier(n_estimators=200, max_depth=3, learning_rate=0.05,
                                    subsample=0.9, colsample_bytree=0.9,
                                    scale_pos_weight=pos_weight, eval_metric="logloss",
                                    random_state=SEED)),
        ]),
    }


@dataclass
class TrainResult:
    dataset: str
    cv: pd.DataFrame        # 5-fold CV means on the training split, one row per model
    test: pd.DataFrame      # held-out test metrics, one row per model
    best_model: str         # chosen by CV ROC-AUC
    models: dict            # fitted pipelines
    X: pd.DataFrame
    y: pd.Series
    groups: pd.DataFrame


def train_dataset(key, save=True):
    X, y, groups = get_dataset(key)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=SEED)
    pos_weight = (y_train == 0).sum() / (y_train == 1).sum()
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)

    cv_rows, test_rows, fitted = [], [], {}
    for name, model in make_models(pos_weight).items():
        scores = cross_validate(model, X_train, y_train, cv=cv, scoring=SCORING)
        cv_rows.append({"model": name, **{m: scores[f"test_{m}"].mean() for m in SCORING}})

        model.fit(X_train, y_train)
        test_rows.append({"model": name, **classification_metrics(y_test, model.predict_proba(X_test)[:, 1])})
        fitted[name] = model

    cv_df = pd.DataFrame(cv_rows).set_index("model")
    test_df = pd.DataFrame(test_rows).set_index("model")
    # ROC-AUC is threshold-independent, so it is a fairer way to pick a model
    # than recall alone (a model that flags everyone has perfect recall).
    best = cv_df["roc_auc"].idxmax()

    if save:
        out = MODELS_DIR / key
        out.mkdir(parents=True, exist_ok=True)
        for name, model in fitted.items():
            joblib.dump(model, out / f"{name}.pkl")
        (out / "metadata.json").write_text(json.dumps({
            "dataset": key,
            "title": DATASETS[key].title,
            "features": list(X.columns),
            "best_model": best,
            "target": "at_risk (1 = coronary heart disease)",
            "cv_metrics": cv_df.round(4).to_dict(orient="index"),
            "test_metrics": test_df.round(4).to_dict(orient="index"),
        }, indent=2))

    return TrainResult(key, cv_df, test_df, best, fitted, X, y, groups)


if __name__ == "__main__":
    for key in sys.argv[1:] or DATASETS:
        result = train_dataset(key)
        print(f"\n=== {DATASETS[key].title} ({len(result.X)} rows, {result.X.shape[1]} features) ===")
        print("5-fold CV (training split):\n", result.cv.round(3))
        print("Held-out test:\n", result.test.round(3))
        print("Best by CV ROC-AUC:", result.best_model)

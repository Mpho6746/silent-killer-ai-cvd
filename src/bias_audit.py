"""
Runs the multi-dataset comparison, the subgroup bias audit and the
cross-dataset transfer test, and saves every table to reports/multi_dataset/.

    python -m src.bias_audit

06_multi_dataset_and_bias.ipynb calls run_all() and adds the plots and the
written interpretation.
"""
import json

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.datasets import DATASETS, get_shared_features
from src.evaluate import cross_dataset_transfer, fairness_gaps, out_of_fold_probabilities, subgroup_report
from src.preprocessing import ROOT
from src.train import train_dataset

REPORT_DIR = ROOT / "reports" / "multi_dataset"


def dataset_profile(result):
    """Who is in the data - the starting point for any discussion of bias."""
    g = result.groups
    profile = {
        "rows": len(result.X),
        "features": result.X.shape[1],
        "prevalence": round(result.y.mean(), 3),
        "age_range": f"{int(result.X['age'].min())}-{int(result.X['age'].max())}",
    }
    for col in g.columns:
        profile[col] = g[col].value_counts(normalize=True).round(3).to_dict()
    return profile


def run_all(save=True):
    results, profiles, subgroup_tables, gaps = {}, {}, {}, []

    for key in DATASETS:
        res = train_dataset(key, save=save)
        results[key] = res
        profiles[key] = dataset_profile(res)

        # Bias audit on out-of-fold predictions of the chosen model over the whole dataset
        oof = out_of_fold_probabilities(res.models[res.best_model], res.X, res.y)
        for col in res.groups.columns:
            table = subgroup_report(res.y, oof, res.groups[col])
            subgroup_tables[(key, col)] = table
            gaps.append({"dataset": key, "attribute": col, **fairness_gaps(table)})

    # Transfer test on the features both datasets share (age, systolic BP).
    # SAHeart is all male, so the UCI side is restricted to men for a like-for-like test.
    simple_lr = Pipeline([("scale", StandardScaler()),
                          ("model", LogisticRegression(max_iter=1000, class_weight="balanced"))])
    uci_X, uci_y, uci_g = get_shared_features("uci_heart")
    men = uci_g["sex"] == "Male"
    uci = (uci_X[men], uci_y[men])
    sa_X, sa_y, _ = get_shared_features("sa_heart")
    sa = (sa_X, sa_y)
    transfer = pd.DataFrame([
        {"train_on": "uci_heart (men)", "test_on": "sa_heart", **cross_dataset_transfer(simple_lr, uci, sa)},
        {"train_on": "sa_heart", "test_on": "uci_heart (men)", **cross_dataset_transfer(simple_lr, sa, uci)},
    ])

    comparison = pd.concat({k: r.test.loc[r.best_model] for k, r in results.items()}, axis=1).T
    comparison.insert(0, "best_model", [r.best_model for r in results.values()])
    gaps_df = pd.DataFrame(gaps)

    if save:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        comparison.to_csv(REPORT_DIR / "dataset_comparison.csv")
        gaps_df.to_csv(REPORT_DIR / "fairness_gaps.csv", index=False)
        transfer.to_csv(REPORT_DIR / "cross_dataset_transfer.csv", index=False)
        for (key, col), table in subgroup_tables.items():
            table.to_csv(REPORT_DIR / f"subgroups_{key}_{col}.csv")
        (REPORT_DIR / "dataset_profiles.json").write_text(json.dumps(profiles, indent=2))

    return {"results": results, "profiles": profiles, "comparison": comparison,
            "subgroups": subgroup_tables, "gaps": gaps_df, "transfer": transfer}


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    out = run_all()
    print(json.dumps(out["profiles"], indent=2))
    print("\nBest model per dataset (held-out test):\n", out["comparison"].round(3))
    for (key, col), table in out["subgroups"].items():
        print(f"\n--- {key} by {col} (out-of-fold) ---\n", table.round(3))
    print("\nFairness gaps:\n", out["gaps"].round(3))
    print("\nCross-dataset transfer (ROC-AUC):\n", out["transfer"].round(3))

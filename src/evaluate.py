"""
Evaluation helpers: overall metrics, the subgroup bias audit, and the
cross-dataset transfer test.

All functions take probabilities for the at-risk class (1) and use a 0.5
decision threshold unless told otherwise.
"""
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

SEED = 42


def classification_metrics(y_true, y_prob, threshold=0.5):
    y_true = np.asarray(y_true)
    y_pred = (np.asarray(y_prob) >= threshold).astype(int)
    return {
        "n": len(y_true),
        "accuracy": accuracy_score(y_true, y_pred),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "f1": f1_score(y_true, y_pred, zero_division=0),
        "roc_auc": roc_auc_score(y_true, y_prob) if len(set(y_true)) == 2 else np.nan,
    }


def out_of_fold_probabilities(model, X, y, n_splits=5):
    """
    A prediction for every row from a model that never saw that row.
    The bias audit uses these instead of the 20% test split: with only
    ~60-90 test rows, some subgroups (e.g. women aged 60+) would have a
    handful of patients and the numbers would be noise.
    """
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=SEED)
    return cross_val_predict(clone(model), X, y, cv=cv, method="predict_proba")[:, 1]


def _rates(y_true, y_pred):
    """Recall (true positive rate) and false positive rate; NaN when undefined."""
    pos, neg = y_true == 1, y_true == 0
    recall = y_pred[pos].mean() if pos.any() else np.nan
    fpr = y_pred[neg].mean() if neg.any() else np.nan
    return recall, fpr


def subgroup_report(y_true, y_prob, group_labels, threshold=0.5, n_boot=1000):
    """
    Per-subgroup performance, the core of the bias audit.

    For each group: size, how common heart disease is (prevalence), how often
    the model flags people (flagged_rate), recall (share of sick patients
    caught), false positive rate (share of healthy patients wrongly flagged),
    precision and ROC-AUC. Recall and FPR get 95% bootstrap confidence
    intervals, because with small groups a gap can easily be chance.
    """
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    y_pred = (y_prob >= threshold).astype(int)
    labels = np.asarray(group_labels)
    rng = np.random.default_rng(SEED)

    rows = []
    # "<45" sorts after "60+" alphabetically; put it first so age bands read youngest -> oldest
    for group in sorted(set(labels), key=lambda g: (not str(g).startswith("<"), str(g))):
        idx = np.where(labels == group)[0]
        yt, yp = y_true[idx], y_pred[idx]
        recall, fpr = _rates(yt, yp)

        boot = np.array([_rates(yt[s], yp[s]) for s in (rng.choice(len(idx), len(idx)) for _ in range(n_boot))])
        recall_lo, recall_hi = np.nanpercentile(boot[:, 0], [2.5, 97.5])
        fpr_lo, fpr_hi = np.nanpercentile(boot[:, 1], [2.5, 97.5])

        rows.append({
            "group": group,
            "n": len(idx),
            "n_at_risk": int(yt.sum()),
            "prevalence": yt.mean(),
            "flagged_rate": yp.mean(),
            "recall": recall,
            "recall_95ci": f"{recall_lo:.2f}-{recall_hi:.2f}",
            "fpr": fpr,
            "fpr_95ci": f"{fpr_lo:.2f}-{fpr_hi:.2f}",
            "precision": precision_score(yt, yp, zero_division=0),
            "roc_auc": roc_auc_score(yt, y_prob[idx]) if len(set(yt)) == 2 else np.nan,
        })
    return pd.DataFrame(rows).set_index("group")


def fairness_gaps(report):
    """
    Summary of one subgroup_report:
      recall_gap  - equal opportunity: do sick patients in every group get caught equally?
      fpr_gap     - do healthy patients in every group get false alarms equally?
      flag_ratio  - lowest / highest flagged rate (demographic parity; 1.0 = equal)
    Gaps are differences between the best- and worst-off group.
    """
    return {
        "recall_gap": report["recall"].max() - report["recall"].min(),
        "worst_recall_group": report["recall"].idxmin(),
        "fpr_gap": report["fpr"].max() - report["fpr"].min(),
        "highest_fpr_group": report["fpr"].idxmax(),
        "flag_ratio": report["flagged_rate"].min() / report["flagged_rate"].max(),
    }


def cross_dataset_transfer(model, source, target, n_splits=5):
    """
    Does a model trained on one dataset work on another?

    `source` and `target` are (X, y) pairs restricted to the features both
    datasets share. Returns the target ROC-AUC for:
      in_domain - the model cross-validated on the target itself (best case)
      transfer  - the model trained on all of the source, tested on all of the target
    A large drop from in_domain to transfer means the source population does
    not represent the target population.
    """
    (Xs, ys), (Xt, yt) = source, target
    in_domain = roc_auc_score(yt, out_of_fold_probabilities(model, Xt, yt, n_splits))
    fitted = clone(model).fit(Xs, ys)
    transfer_prob = fitted.predict_proba(Xt)[:, 1]
    return {
        "in_domain_auc": in_domain,
        "transfer_auc": roc_auc_score(yt, transfer_prob),
        "transfer_flagged_rate": (transfer_prob >= 0.5).mean(),
        "target_prevalence": yt.mean(),
    }

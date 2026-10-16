"""
The two risk models behind the app, wrapped with everything the UI needs:
a probability, a risk tier, the per-patient drivers of that risk, and a
"what would lower it" plan computed by re-scoring the model.

- ClinicalModel  : tuned Random Forest on the UCI Cleveland data (hospital
                   test results). Used by clinicians.
- LifestyleModel : Logistic Regression on the South African Heart data
                   (risk factors a person can know without hospital tests).
                   Used by patients for a self-check.
"""
import joblib
import numpy as np
import pandas as pd
import shap

from app.utils import calculate_risk_tier
from src.feature_engineering import add_sa_features, add_uci_features
from src.preprocessing import ROOT, load_sa_heart

MODELS_DIR = ROOT / "models"

TIER_KEYS = {"Low Risk": "low", "Moderate Risk": "moderate", "High Risk": "high", "Critical Risk": "critical"}


def risk_tier(probability):
    label, _ = calculate_risk_tier(probability)
    return TIER_KEYS[label]


def _rank_drivers(features, raw_values, impacts, top=None):
    """Drivers sorted by absolute impact; positive impact pushes risk up."""
    drivers = [{"feature": f, "value": _plain(raw_values[f]), "impact": round(float(i), 4)}
               for f, i in zip(features, impacts)]
    drivers.sort(key=lambda d: abs(d["impact"]), reverse=True)
    return drivers[:top] if top else drivers


def _plain(value):
    """numpy scalars -> plain Python, so results serialise to JSON."""
    return value.item() if hasattr(value, "item") else value


class ClinicalModel:
    """Tuned Random Forest from notebooks 01-03 (the same files Streamlit uses)."""

    RAW_FEATURES = ["age", "sex", "cp", "trestbps", "chol", "fbs", "restecg",
                    "thalach", "exang", "oldpeak", "slope", "ca", "thal"]
    FEATURES = RAW_FEATURES + ["hr_reserve", "bp_risk_flag"]

    # Changes a clinician can actually work on with the patient, and the
    # guideline target each one is moved to when building the plan.
    PLAN = [
        ("lower_bp", "trestbps", lambda v: v > 120, 120),       # AHA normal BP
        ("lower_cholesterol", "chol", lambda v: v > 200, 200),  # NCEP desirable total cholesterol
        ("control_sugar", "fbs", lambda v: v == 1, 0),          # fasting sugar back under 120 mg/dL
    ]

    def __init__(self):
        self.model = joblib.load(MODELS_DIR / "rf_model.pkl")
        self.scaler = joblib.load(MODELS_DIR / "scaler.pkl")
        self.explainer = shap.TreeExplainer(self.model)

    def _features(self, inputs):
        """Raw inputs -> engineered (unscaled) and scaled feature frames."""
        engineered = add_uci_features(pd.DataFrame([inputs])[self.RAW_FEATURES])[self.FEATURES]
        scaled = pd.DataFrame(self.scaler.transform(engineered), columns=self.FEATURES)
        return engineered, scaled

    def predict(self, inputs):
        _, scaled = self._features(inputs)
        return float(self.model.predict_proba(scaled)[0, 1])

    def assess(self, inputs):
        engineered, scaled = self._features(inputs)
        probability = float(self.model.predict_proba(scaled)[0, 1])
        # SHAP values for class 1 (heart disease), in probability units
        impacts = self.explainer(scaled).values[0, :, 1]

        plan = []
        for action, feature, applies, target in self.PLAN:
            if applies(inputs[feature]):
                new_p = self.predict({**inputs, feature: target})
                if new_p < probability - 0.005:   # only suggest changes the model says help
                    plan.append({"action": action, "target": target, "probability": round(new_p, 4)})

        return {
            "model": "clinical",
            "probability": round(probability, 4),
            "tier": risk_tier(probability),
            "drivers": _rank_drivers(self.FEATURES, engineered.iloc[0], impacts),
            "impact_unit": "probability",
            "plan": plan,
        }


class LifestyleModel:
    """
    Logistic Regression trained by src/train.py on SAHeart. Patients describe
    smoking as cigarettes/day and years, which is converted to the dataset's
    cumulative tobacco (kg). Values a person usually doesn't know (adiposity,
    Type-A score, LDL, alcohol) fall back to the study median and are reported
    back as "estimated".

    Every input is clipped to the range seen in the study (reported back as
    "clipped"): a linear model extrapolates without limit, so e.g. a 40-year
    smoking history (far above the study's 31 kg maximum) would otherwise
    produce a meaningless ~100%.
    """

    FEATURES = ["sbp", "tobacco", "ldl", "adiposity", "famhist", "typea", "obesity",
                "alcohol", "age", "bp_risk_flag", "smoker", "ldl_high", "obese"]
    OPTIONAL = ["ldl", "adiposity", "typea", "alcohol"]
    PROJECTION_YEARS = 10

    def __init__(self):
        self.model = joblib.load(MODELS_DIR / "sa_heart" / "logistic_regression.pkl")
        study = load_sa_heart()
        self.medians = study.median(numeric_only=True)
        self.ranges = {c: (float(study[c].min()), float(study[c].max()))
                       for c in ["sbp", "tobacco", "ldl", "adiposity", "typea", "obesity", "alcohol", "age"]}
        scaler, lr = self.model.named_steps["scale"], self.model.named_steps["model"]
        self._scale, self._coef = scaler, lr.coef_[0]

    @staticmethod
    def tobacco_kg(cigarettes_per_day, years_smoked):
        """About 1 g of tobacco per cigarette, accumulated over the years smoked."""
        return cigarettes_per_day * 365 * years_smoked / 1000

    def to_features(self, inputs):
        """Patient-friendly inputs -> the model's raw columns (+ which were estimated)."""
        bmi = inputs.get("bmi")
        if bmi is None and inputs.get("height_cm") and inputs.get("weight_kg"):
            bmi = inputs["weight_kg"] / (inputs["height_cm"] / 100) ** 2
        row = {
            "sbp": inputs["sbp"],
            "tobacco": self.tobacco_kg(inputs.get("cigarettes_per_day", 0), inputs.get("years_smoked", 0)),
            "famhist": int(bool(inputs.get("family_history"))),
            "obesity": bmi if bmi is not None else float(self.medians["obesity"]),
            "age": inputs["age"],
        }
        estimated = [] if bmi is not None else ["obesity"]
        for col in self.OPTIONAL:
            if inputs.get(col) is None:
                row[col] = float(self.medians[col])
                estimated.append(col)
            else:
                row[col] = inputs[col]
        row, clipped = self._clip(row)
        return row, estimated, clipped

    def _clip(self, row):
        clipped = []
        for col, (lo, hi) in self.ranges.items():
            value = min(max(row[col], lo), hi)
            if value != row[col]:
                clipped.append(col)
            row[col] = value
        return row, clipped

    def _frame(self, row):
        return add_sa_features(pd.DataFrame([row]))[self.FEATURES]

    def _prob(self, row):
        row, _ = self._clip(dict(row))
        return float(self.model.predict_proba(self._frame(row))[0, 1])

    def assess(self, inputs):
        row, estimated, clipped = self.to_features(inputs)
        frame = self._frame(row)
        probability = float(self.model.predict_proba(frame)[0, 1])
        # For a linear model the exact per-feature contribution (in log-odds,
        # relative to the average training patient) is coefficient x scaled value.
        impacts = self._coef * self._scale.transform(frame)[0]

        # Proactive part: where does this person head in 10 years if nothing
        # changes, and how much does each change bend that curve?
        years = self.PROJECTION_YEARS
        future = {**row, "age": row["age"] + years,
                  "tobacco": row["tobacco"] + self.tobacco_kg(inputs.get("cigarettes_per_day", 0), years)}
        future_p = self._prob(future)

        changes = []
        if inputs.get("cigarettes_per_day", 0) > 0:
            changes.append(("quit_smoking", {"tobacco": row["tobacco"]}))
        if row["sbp"] > 120:
            changes.append(("lower_bp", {"sbp": 120}))
        if "ldl" not in estimated and row["ldl"] > 3.0:
            changes.append(("lower_ldl", {"ldl": 3.0}))
        if "obesity" not in estimated and row["obesity"] > 25:
            changes.append(("healthy_weight", {"obesity": 25.0}))

        plan = []
        for action, change in changes:
            p = self._prob({**future, **change})
            if p < future_p - 0.005:
                plan.append({"action": action, "probability_in_10y": round(p, 4)})
        if len(plan) > 1:
            combined = {k: v for action, change in changes for k, v in change.items()
                        if action in {p["action"] for p in plan}}
            plan.append({"action": "all_changes", "probability_in_10y": round(self._prob({**future, **combined}), 4)})

        return {
            "model": "lifestyle",
            "probability": round(probability, 4),
            "tier": risk_tier(probability),
            "drivers": _rank_drivers(self.FEATURES, frame.iloc[0], impacts),
            "impact_unit": "log-odds",
            "estimated": estimated,
            "clipped": clipped,
            "projection": {"years": years, "probability_if_unchanged": round(future_p, 4)},
            "plan": plan,
        }

"""
Seeds a small set of demo patients the first time the app runs, so the triage
queue, trends and alerts have something to show. Clinical inputs are real
records from the held-out test split (data/processed/heart_test.csv); names
are fictional and every ID starts with DEMO-.
"""
import pandas as pd

from app.storage import days_ago
from src.preprocessing import ROOT

TEST_SET = ROOT / "data" / "processed" / "heart_test.csv"


def _pick(rows, probs, sex, quantile):
    """The test-set patient of the given sex closest to a risk quantile."""
    subset = probs[rows["sex"] == sex]
    target = subset.quantile(quantile)
    return rows.loc[(subset - target).abs().idxmin()].drop("target").to_dict()


def seed(store, clinical, lifestyle):
    rows = pd.read_csv(TEST_SET)
    inputs = rows.drop(columns="target").to_dict(orient="records")
    probs = pd.Series([clinical.predict(r) for r in inputs], index=rows.index)

    def record(pid, name, sex, kind, model, patient_inputs, when):
        store.upsert_patient(pid, name, sex, created_at=days_ago(120))
        result = model.assess(patient_inputs)
        store.add_assessment(pid, kind, patient_inputs, result["probability"], result["tier"], created_at=when)

    # Rising risk: blood pressure and cholesterol climbed since the last visit
    thabo = _pick(rows, probs, 1, 0.95)
    earlier = {**thabo, "trestbps": max(thabo["trestbps"] - 30, 100), "chol": max(thabo["chol"] - 60, 150),
               "exang": 0, "oldpeak": max(thabo["oldpeak"] - 1.5, 0.0)}
    record("DEMO-001", "Thabo Ndlovu", "M", "clinical", clinical, earlier, days_ago(75))
    record("DEMO-001", "Thabo Ndlovu", "M", "clinical", clinical, thabo, days_ago(2))

    # High risk, last seen 45 days ago: follow-up overdue
    record("DEMO-002", "Sipho Mokoena", "M", "clinical", clinical, _pick(rows, probs, 1, 0.80), days_ago(45))

    record("DEMO-003", "Sarah Venter", "F", "clinical", clinical, _pick(rows, probs, 0, 0.75), days_ago(6))
    record("DEMO-004", "Pieter Botha", "M", "clinical", clinical, _pick(rows, probs, 1, 0.45), days_ago(12))
    record("DEMO-005", "Lerato Dlamini", "F", "clinical", clinical, _pick(rows, probs, 0, 0.10), days_ago(20))

    # Patient self-checks (lifestyle model), improving after quitting smoking
    before = {"age": 47, "sbp": 152, "cigarettes_per_day": 12, "years_smoked": 3,
              "family_history": True, "height_cm": 176, "weight_kg": 92, "ldl": 4.8}
    after = {**before, "cigarettes_per_day": 0, "sbp": 134, "weight_kg": 86, "ldl": 3.9}
    record("DEMO-101", "John Doe", "M", "lifestyle", lifestyle, before, days_ago(90))
    record("DEMO-101", "John Doe", "M", "lifestyle", lifestyle, after, days_ago(3))
    record("DEMO-102", "Nomvula Khumalo", "F", "lifestyle", lifestyle,
           {"age": 56, "sbp": 164, "family_history": True, "height_cm": 162, "weight_kg": 84}, days_ago(8))

"""
Seeds a small set of demo patients the first time the app runs, so the triage
queue, trends and alerts have something to show. Clinical inputs are real
records from the held-out test split (data/processed/heart_test.csv); names
are fictional and every ID starts with DEMO-.
"""
from datetime import date, timedelta

import pandas as pd

from app.documents import read_document, sha256
from app.patient_api import lifestyle_inputs
from app.storage import days_ago
from src.preprocessing import ROOT

TEST_SET = ROOT / "data" / "processed" / "heart_test.csv"
SAMPLES = ROOT / "sample_documents"


def _pick(rows, probs, sex, quantile):
    """The test-set patient of the given sex closest to a risk quantile."""
    subset = probs[rows["sex"] == sex]
    target = subset.quantile(quantile)
    return rows.loc[(subset - target).abs().idxmin()].drop("target").to_dict()


def seed(store, clinical, lifestyle):
    rows = pd.read_csv(TEST_SET)
    inputs = rows.drop(columns="target").to_dict(orient="records")
    probs = pd.Series([clinical.predict(r) for r in inputs], index=rows.index)

    def record(pid, name, sex, kind, model, patient_inputs, when, by=None):
        store.upsert_patient(pid, name, sex, created_at=days_ago(120))
        result = model.assess(patient_inputs)
        store.add_assessment(pid, kind, patient_inputs, result["probability"], result["tier"], created_at=when, created_by=by)
        if by:
            store.log(by, "assess", pid, at=when)

    # Rising risk: blood pressure and cholesterol climbed since the last visit
    thabo = _pick(rows, probs, 1, 0.95)
    earlier = {**thabo, "trestbps": max(thabo["trestbps"] - 30, 100), "chol": max(thabo["chol"] - 60, 150),
               "exang": 0, "oldpeak": max(thabo["oldpeak"] - 1.5, 0.0)}
    record("DEMO-001", "Thabo Ndlovu", "M", "clinical", clinical, earlier, days_ago(75), by="sfiso.kekana")
    record("DEMO-001", "Thabo Ndlovu", "M", "clinical", clinical, thabo, days_ago(2), by="samuel.olajide")

    # High risk, last seen 45 days ago: follow-up overdue
    record("DEMO-002", "Sipho Mokoena", "M", "clinical", clinical, _pick(rows, probs, 1, 0.80), days_ago(45), by="tshiamo.bokaba")

    record("DEMO-003", "Sarah Venter", "F", "clinical", clinical, _pick(rows, probs, 0, 0.75), days_ago(6), by="mpho.mutengwe")
    record("DEMO-004", "Pieter Botha", "M", "clinical", clinical, _pick(rows, probs, 1, 0.45), days_ago(12), by="kamogelo")
    record("DEMO-005", "Lerato Dlamini", "F", "clinical", clinical, _pick(rows, probs, 0, 0.10), days_ago(20), by="neontle")

    if SAMPLES.exists():
        seed_patients(store, lifestyle)


def _upload(store, patient_id, name, filename, uploaded_at):
    content = (SAMPLES / filename).read_bytes()
    result = read_document(content, expected_name=name).to_dict()
    doc_id, _ = store.add_document(patient_id, filename, sha256(content), content, result, uploaded_at=uploaded_at)
    return doc_id


def _self_check(store, lifestyle, patient_id, doc_id, answers, when):
    inputs = lifestyle_inputs(store.get_profile(patient_id), store.get_document(doc_id), answers)
    r = lifestyle.assess(inputs)
    store.add_assessment(patient_id, "lifestyle", inputs, r["probability"], r["tier"], created_at=when)


def seed_patients(store, lifestyle):
    """Health-tracker patients whose vitals come from the sample clinical documents."""
    # John: an older clinic printout (approved by a clinician), then a verified
    # signed report after he stopped smoking. Medications and check-ins included.
    store.upsert_patient("DEMO-101", "John Doe", "M", created_at=days_ago(120), dob="1979-03-14")
    older = _upload(store, "DEMO-101", "John Doe", "john_doe_older_unsigned.pdf", days_ago(98))
    store.review_document(older, approve=True, reviewer="mpho.mutengwe")
    _self_check(store, lifestyle, "DEMO-101", older,
                {"cigarettes_per_day": 12, "years_smoked": 3, "family_history": True}, days_ago(97))
    verified = _upload(store, "DEMO-101", "John Doe", "john_doe_verified.pdf", days_ago(12))
    _self_check(store, lifestyle, "DEMO-101", verified,
                {"cigarettes_per_day": 0, "years_smoked": 3, "family_history": True}, days_ago(12))

    for name, dose, times in [("Aspirin", "75 mg", ["08:00"]), ("Amlodipine", "5 mg", ["08:00"]),
                              ("Atorvastatin", "20 mg", ["20:00"])]:
        med_id = store.add_medication("DEMO-101", name, dose, times, created_at=days_ago(30))
        for n in range(1, 8):
            if (n, name) not in {(2, "Atorvastatin"), (4, "Amlodipine")}:   # a couple of missed doses
                for slot in times:
                    store.log_dose("DEMO-101", med_id, (date.today() - timedelta(days=n)).isoformat(), slot, True)
    for n, (mood, minutes, water) in enumerate([(4, 30, 7), (3, 20, 5), (4, 45, 8), (5, 30, 6), (4, 0, 4), (4, 25, 6)], 1):
        store.save_checkin("DEMO-101", (date.today() - timedelta(days=n)).isoformat(), mood, minutes, water, [])

    # Nomvula: an unsigned clinic printout still waiting for a clinician to confirm it
    store.upsert_patient("DEMO-102", "Nomvula Khumalo", "F", created_at=days_ago(30), dob="1970-05-21")
    pending = _upload(store, "DEMO-102", "Nomvula Khumalo", "nomvula_khumalo_unsigned.pdf", days_ago(8))
    _self_check(store, lifestyle, "DEMO-102", pending,
                {"cigarettes_per_day": 0, "years_smoked": 0, "family_history": True}, days_ago(8))

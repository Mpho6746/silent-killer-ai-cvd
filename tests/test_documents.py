"""Document verification and the patient health-tracker API."""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["SKAI_DB"] = ":memory:"

from app.api import app  # noqa: E402
from app.auth import DEMO_PASSWORD  # noqa: E402
from app.patient_auth import DEMO_PATIENT_PASSWORD  # noqa: E402
from app.documents import read_document  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "sample_documents"


def sample(name):
    return (SAMPLES / name).read_bytes()


# ---------- document verification ----------

def test_signed_document_is_verified():
    r = read_document(sample("john_doe_verified.pdf"), expected_name="John Doe")
    assert r.status == "verified"
    assert r.vitals["sbp"] == 134 and r.vitals["ldl"] == 3.9
    assert r.issuer == "SKAI Partner Demo Clinic"


def test_tampered_document_is_rejected():
    r = read_document(sample("john_doe_tampered.pdf"), expected_name="John Doe")
    assert r.status == "rejected" and "signature_invalid" in r.reasons and not r.vitals


def test_signed_document_for_someone_else_is_rejected():
    r = read_document(sample("jane_smith_verified.pdf"), expected_name="John Doe")
    assert r.status == "rejected" and "name_mismatch" in r.reasons


def test_unsigned_document_needs_review():
    r = read_document(sample("nomvula_khumalo_unsigned.pdf"), expected_name="Nomvula Khumalo")
    assert r.status == "pending_review" and "unsigned" in r.warnings
    assert r.vitals == {"sbp": 164, "dbp": 98, "heart_rate": 81, "ldl": 4.6, "height_cm": 162, "weight_kg": 84}
    assert r.document_date != "1970-05-21"          # date of birth is not the test date


def test_unknown_issuer_is_rejected():
    r = read_document(sample("john_doe_verified.pdf"), expected_name="John Doe", issuers={})
    assert r.status == "rejected" and "unknown_issuer" in r.reasons


def test_future_and_old_documents():
    pdf = sample("john_doe_verified.pdf")
    issued = date.fromisoformat(read_document(pdf).document_date)
    assert read_document(pdf, today=issued - timedelta(days=1)).reasons == ["future_date"]
    assert "outdated" in read_document(pdf, today=issued + timedelta(days=400)).warnings


def test_not_a_pdf():
    assert read_document(b"hello").reasons == ["not_a_pdf"]


# ---------- patient API ----------

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        # Default header: a signed-in clinician. Patient calls pass their own session (see new_patient).
        token = c.post("/api/auth/login", json={"username": "sfiso.kekana", "password": DEMO_PASSWORD}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


def new_patient(client, pid, name="John Doe"):
    """Registers a patient and returns headers for their own session."""
    r = client.post("/api/patient-auth/register",
                    json={"consent": True, "patient_id": pid, "name": name, "dob": "1979-03-14", "sex": "M", "password": "Patient2026!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def upload(client, pid, filename, h):
    return client.post(f"/api/patients/{pid}/documents", headers=h,
                       files={"file": (filename, sample(filename), "application/pdf")}).json()


def test_self_check_needs_a_document_and_rejects_typed_vitals(client):
    h = new_patient(client, "T-DOC1")
    assert client.post("/api/patients/T-DOC1/self-check", headers=h, json={}).json()["detail"]["code"] == "no_document"
    upload(client, "T-DOC1", "john_doe_verified.pdf", h)
    assert client.post("/api/patients/T-DOC1/self-check", headers=h, json={"sbp": 100}).status_code == 422
    r = client.post("/api/patients/T-DOC1/self-check", headers=h, json={"cigarettes_per_day": 0, "family_history": True}).json()
    assert r["vitals_source"]["status"] == "verified"
    assert "sbp" not in r["estimated"] and "ldl" not in r["estimated"]


def test_rejected_upload_is_kept_but_not_used(client):
    h = new_patient(client, "T-DOC2")
    doc = upload(client, "T-DOC2", "john_doe_tampered.pdf", h)
    assert doc["status"] == "rejected"
    assert client.get("/api/patients/T-DOC2/dashboard", headers=h).json()["vitals_source"] is None


def test_duplicate_upload_is_detected(client):
    h = new_patient(client, "T-DOC3")
    first = upload(client, "T-DOC3", "john_doe_verified.pdf", h)
    second = upload(client, "T-DOC3", "john_doe_verified.pdf", h)
    assert second["duplicate"] and second["id"] == first["id"]


def test_clinician_review_flow(client):
    h = new_patient(client, "T-DOC4", "Nomvula Khumalo")
    doc = upload(client, "T-DOC4", "nomvula_khumalo_unsigned.pdf", h)
    client.post("/api/patients/T-DOC4/self-check", headers=h, json={})
    queue = {q["id"]: q for q in client.get("/api/patients").json()}
    assert "needs_verification" in queue["T-DOC4"]["flags"]
    assert client.get(f"/api/documents/{doc['id']}/file").content.startswith(b"%PDF")

    assert client.post(f"/api/documents/{doc['id']}/review", json={"approve": True}).json()["status"] == "approved"
    assert client.post(f"/api/documents/{doc['id']}/review", json={"approve": False}).status_code == 409
    queue = {q["id"]: q for q in client.get("/api/patients").json()}
    assert "needs_verification" not in queue["T-DOC4"]["flags"]


def test_medication_planner(client):
    h = new_patient(client, "T-MED")
    plan = client.post("/api/patients/T-MED/medications", headers=h,
                       json={"name": "Aspirin", "dose": "75 mg", "times": ["08:00"]}).json()
    med = plan["medications"][0]
    plan = client.post(f"/api/patients/T-MED/medications/{med['id']}/doses", headers=h, json={"slot": "08:00", "taken": True}).json()
    assert plan["medications"][0]["doses"][0]["taken"]
    assert client.post("/api/patients/T-MED/medications", headers=h, json={"name": "X", "times": ["25:00"]}).status_code == 422
    assert client.delete(f"/api/patients/T-MED/medications/{med['id']}", headers=h).json()["medications"] == []


def test_checkin_urgent_symptoms_trigger_urgent_advice(client):
    h = new_patient(client, "T-CHK")
    r = client.post("/api/patients/T-CHK/checkins", headers=h,
                    json={"mood": 2, "activity_minutes": 0, "water_glasses": 2, "symptoms": ["chest_pain"]}).json()
    assert r["urgent"]
    advice = client.get("/api/patients/T-CHK/dashboard", headers=h).json()["advice"]
    assert advice[0]["key"] == "symptoms_urgent" and advice[0]["level"] == "urgent"


def test_photo_validation(client):
    h = new_patient(client, "T-PIC")
    ok = client.put("/api/patients/T-PIC/photo", headers=h, json={"data_url": "data:image/png;base64,iVBORw0KGgo="})
    assert ok.status_code == 200
    bad = client.put("/api/patients/T-PIC/photo", headers=h, json={"data_url": "javascript:alert(1)"})
    assert bad.status_code == 422


def test_registration_rules(client):
    base = {"name": "X", "sex": "F", "password": "Patient2026!", "consent": True}
    too_young = client.post("/api/patient-auth/register",
                            json={**base, "patient_id": "T-KID", "dob": str(date.today().year - 10) + "-01-01"})
    assert too_young.status_code == 422
    bad_id = client.post("/api/patient-auth/register", json={**base, "patient_id": "bad id!", "dob": "1980-01-01"})
    assert bad_id.status_code == 422
    short_pw = client.post("/api/patient-auth/register", json={**base, "patient_id": "T-PW", "dob": "1980-01-01", "password": "short"})
    assert short_pw.status_code == 422
    no_consent = client.post("/api/patient-auth/register", json={**base, "patient_id": "T-NC", "dob": "1980-01-01", "consent": False})
    assert no_consent.status_code == 422 and no_consent.json()["detail"]["code"] == "consent_required"


def test_demo_john_dashboard(client):
    token = client.post("/api/patient-auth/login", json={"patient_id": "DEMO-101", "password": DEMO_PATIENT_PASSWORD}).json()["token"]
    d = client.get("/api/patients/DEMO-101/dashboard", headers={"Authorization": f"Bearer {token}"}).json()
    assert d["vitals_source"]["status"] == "verified"
    assert len(d["history"]) == 2 and d["history"][-1]["probability"] < d["history"][0]["probability"]
    assert d["medications"]["adherence_7d"] > 0.8
    assert d["streak"] >= 6

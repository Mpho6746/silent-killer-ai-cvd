"""SA ID numbers, HL7 FHIR, POPIA rights, BP monitor readings, outcomes and monitoring."""
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

os.environ["SKAI_DB"] = ":memory:"

from app.api import app  # noqa: E402
from app.auth import DEMO_PASSWORD  # noqa: E402
from app.interop import luhn_ok, parse_sa_id  # noqa: E402
from app.patient_auth import DEMO_PATIENT_PASSWORDS  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def staff(client, username):
    token = client.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def patient(client, pid="DEMO-101"):
    token = client.post("/api/patient-auth/login", json={"patient_id": pid, "password": DEMO_PATIENT_PASSWORDS[pid]}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def make_id(first12):
    """Append the Luhn check digit."""
    return next(first12 + str(d) for d in range(10) if luhn_ok(first12 + str(d)))


# ---------- SA ID ----------

def test_sa_id_parsing():
    man = parse_sa_id(make_id("790314512308"))         # John Doe: 14 March 1979, male, citizen
    assert man == {"valid": True, "dob": "1979-03-14", "sex": "M", "citizen": True, "number": man["number"]}
    woman = parse_sa_id(make_id("050101012318"), today=date(2026, 10, 17))
    assert woman["dob"] == "2005-01-01" and woman["sex"] == "F"
    good = make_id("790314512308")
    bad_digit = good[:-1] + str((int(good[-1]) + 1) % 10)
    assert parse_sa_id(bad_digit)["reason"] == "checksum"
    assert parse_sa_id("12345")["reason"] == "format"
    assert parse_sa_id(make_id("791314512308"))["reason"] == "date"


def test_contact_settings(client):
    h = patient(client)
    ok = client.put("/api/patients/DEMO-101/contact", headers=h,
                    json={"phone": "082 555 0101", "sms_opt_in": True, "sa_id": make_id("790314512308")})
    assert ok.status_code == 200 and ok.json()["phone"] == "+27825550101" and ok.json()["sms_opt_in"] == 1
    wrong_person = client.put("/api/patients/DEMO-101/contact", headers=h, json={"sa_id": make_id("850101012308")})
    assert wrong_person.json()["detail"]["code"] == "sa_id_dob_mismatch"
    assert client.put("/api/patients/DEMO-101/contact", headers=h, json={"phone": "123"}).json()["detail"]["code"] == "phone_invalid"
    assert client.put("/api/patients/DEMO-101/contact", headers=h, json={"sms_opt_in": True}).json()["detail"]["code"] == "phone_needed"
    # someone else's settings
    assert client.put("/api/patients/DEMO-102/contact", headers=h, json={}).status_code == 403


# ---------- POPIA ----------

def test_consent_recorded_and_export(client):
    h = patient(client)
    profile = client.get("/api/patients/DEMO-101/dashboard", headers=h).json()["profile"]
    assert profile["consent_version"] and profile["consent_at"]
    client.get("/api/patients/DEMO-101", headers=staff(client, "sfiso.kekana"))     # a staff member opens the record
    data = client.get("/api/patients/DEMO-101/export", headers=h).json()
    assert {"profile", "assessments", "documents", "medications", "who_opened_my_record"} <= set(data)
    assert "password_hash" not in data["profile"]
    assert any(a["staff"] == "Dr Sfiso Kekana" for a in data["who_opened_my_record"])
    assert client.get("/api/patients/DEMO-102/export", headers=h).status_code == 403


# ---------- FHIR ----------

def lab_bundle(pid, ldl=3.9, unit="mmol/L"):
    def obs(code, value, unit):
        return {"resource": {"resourceType": "Observation", "status": "final",
                             "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
                             "subject": {"reference": f"Patient/{pid}"}, "effectiveDateTime": "2026-10-15",
                             "performer": [{"display": "NHLS Chemical Pathology"}], "valueQuantity": {"value": value, "unit": unit}}}
    return {"resourceType": "Bundle", "type": "collection",
            "entry": [obs("13457-7", ldl, unit), obs("2085-9", 1.2, "mmol/L"), obs("2093-3", 232, "mg/dL"), obs("9999-9", 1, "x")]}


def test_fhir_lab_import_by_clinician(client):
    doc = staff(client, "sfiso.kekana")
    r = client.post("/api/fhir/lab-results", headers=doc, json=lab_bundle("DEMO-101")).json()["imported"][0]
    assert r["status"] == "approved" and r["vitals"] == {"ldl": 3.9, "hdl": 1.2, "total_cholesterol": 6.0}
    # another facility's patient
    assert client.post("/api/fhir/lab-results", headers=doc, json=lab_bundle("DEMO-102")).status_code == 403
    assert client.post("/api/fhir/lab-results", headers=staff(client, "manager.district"), json=lab_bundle("DEMO-101")).status_code == 403
    assert client.post("/api/fhir/lab-results", headers=doc, json={"resourceType": "Patient"}).status_code == 422


def test_lab_results_add_to_clinic_vitals(client):
    """A lipids-only lab result must not hide the blood pressure from the clinic document."""
    h = patient(client)
    client.post("/api/fhir/lab-results", headers=staff(client, "sfiso.kekana"), json=lab_bundle("DEMO-101", ldl=4.6))
    check = client.post("/api/patients/DEMO-101/self-check", headers=h,
                        json={"cigarettes_per_day": 0, "years_smoked": 0, "family_history": True, "bp_treated": True})
    assert check.status_code == 200, check.text
    inputs = check.json()["inputs"]
    assert inputs["sbp"] == 134 and inputs["ldl"] == 4.6        # BP from the clinic, newer LDL from the lab
    assert check.json()["validated_score"]["version"] == "lab"  # cholesterol + HDL now available


def test_fhir_lab_feed_with_integration_key(client):
    feed = client.post("/api/fhir/lab-results", headers={"X-Integration-Key": "demo-lab-key"}, json=lab_bundle("DEMO-102", ldl=4.4))
    assert feed.json()["imported"][0]["status"] == "verified"
    assert client.post("/api/fhir/lab-results", headers={"X-Integration-Key": "wrong"}, json=lab_bundle("DEMO-102")).status_code == 401
    docs = client.get("/api/patients/DEMO-102", headers=staff(client, "kamogelo")).json()["documents"]
    assert any(d["issuer"] == "NHLS Chemical Pathology (FHIR)" and d["status"] == "verified" for d in docs)


def test_fhir_export(client):
    bundle = client.get("/api/patients/DEMO-101/fhir", headers=patient(client)).json()
    kinds = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert bundle["resourceType"] == "Bundle" and kinds[0] == "Patient"
    assert "Observation" in kinds and "RiskAssessment" in kinds
    ldl = [e["resource"] for e in bundle["entry"] if e["resource"].get("code", {}).get("coding", [{}])[0].get("code") == "13457-7"]
    assert ldl and ldl[0]["valueQuantity"]["unit"] == "mmol/L"
    assert client.get("/api/patients/DEMO-101/fhir", headers=staff(client, "kamogelo")).status_code == 403
    assert client.get("/api/patients/DEMO-101/fhir").status_code == 401


# ---------- clinic BP monitor ----------

def test_triage_keeps_device_reading(client):
    nurse = staff(client, "nurse.thandi")
    body = {"name": "Cuff Test", "vitals": {"mobility": "walking", "rr": 16, "hr": 88, "sbp": 168, "dbp": 102, "temp": 36.7,
                                            "avpu": "alert", "bp_source": "device", "bp_device": "Omron M7"}}
    client.post("/api/triage", headers=nurse, json=body)
    visit = next(v for v in client.get("/api/triage/queue", headers=nurse).json() if v["name"] == "Cuff Test")
    assert visit["vitals"]["bp_source"] == "device" and visit["vitals"]["dbp"] == 102


# ---------- outcomes and monitoring ----------

def test_outcomes_and_monitoring(client):
    doctor, nurse = staff(client, "sfiso.kekana"), staff(client, "nurse.thandi")
    assert client.post("/api/patients/DEMO-101/outcome", headers=nurse, json={"has_cvd": False, "source": "ecg"}).status_code == 403
    assert client.post("/api/patients/DEMO-101/outcome", headers=doctor, json={"has_cvd": False, "source": "ecg"}).status_code == 200
    district = client.get("/api/monitoring", headers=staff(client, "manager.district")).json()
    o = district["overall"]
    # seeded: DEMO-001 critical+CVD, 002 high+CVD, 004 moderate+CVD (missed), 003/005 no CVD; plus DEMO-101 no CVD
    assert o["n"] == 6 and o["with_cvd"] == 3 and round(o["recall"], 2) == 0.67 and not o["enough"]
    assert district["alerts"][0]["level"] == "info"
    assert district["groups"]["sex"]["Male"]["n"] == 4
    # a Kopanong doctor only sees Kopanong patients' outcomes
    assert client.get("/api/monitoring", headers=doctor).json()["overall"]["n"] == 3
    assert client.get("/api/monitoring", headers=nurse).status_code == 403


def test_district_view(client):
    rows = {f["id"]: f for f in client.get("/api/district", headers=staff(client, "manager.district")).json()}
    assert set(rows) == {"kopanong-clinic", "ubuntu-chc", "lesedi-hospital"}
    assert rows["kopanong-clinic"]["overdue_referrals_out"] >= 1 and rows["kopanong-clinic"]["waiting"] >= 1
    only_mine = client.get("/api/district", headers=staff(client, "sfiso.kekana")).json()
    assert [f["id"] for f in only_mine] == ["kopanong-clinic"]

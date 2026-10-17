"""Patient sign-in: registration, sessions, record ownership and clinic activation codes."""
import os

import pytest
from fastapi.testclient import TestClient

os.environ["SKAI_DB"] = ":memory:"

from app.api import app  # noqa: E402
from app.auth import DEMO_PASSWORD  # noqa: E402
from app.patient_auth import DEMO_PATIENT_PASSWORD  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def patient_login(client, pid="DEMO-101", password=DEMO_PATIENT_PASSWORD):
    return client.post("/api/patient-auth/login", json={"patient_id": pid, "password": password})


def clinician(client, username="mpho.mutengwe"):
    return bearer(client.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORD}).json()["token"])


@pytest.mark.parametrize("method,path", [
    ("get", "/api/patients/DEMO-101/dashboard"),
    ("post", "/api/patients/DEMO-101/self-check"),
    ("post", "/api/patients/DEMO-101/checkins"),
    ("post", "/api/patients/DEMO-101/medications"),
    ("put", "/api/patients/DEMO-101/photo"),
    ("post", "/api/patients/DEMO-101/profile"),
])
def test_patient_endpoints_need_the_patients_session(client, method, path):
    call = lambda headers=None: client.request(method.upper(), path, json={}, headers=headers)
    assert call().status_code == 401
    other = bearer(patient_login(client, "DEMO-102").json()["token"])
    assert call(other).status_code == 403
    # a clinician session is not a patient session either
    assert call(clinician(client)).status_code == 401


def test_login_never_returns_secrets(client):
    r = patient_login(client)
    assert r.status_code == 200
    assert not {"password_hash", "failed_attempts", "locked_until"} & set(r.json()["profile"])
    dash = client.get("/api/patients/DEMO-101/dashboard", headers=bearer(r.json()["token"])).json()
    assert "password_hash" not in dash["profile"]


def test_wrong_password_unknown_id_and_no_account_look_the_same(client):
    codes = {patient_login(client, password="nope").json()["detail"]["code"],
             patient_login(client, "NOBODY").json()["detail"]["code"],
             patient_login(client, "DEMO-003").json()["detail"]["code"]}   # clinic patient without an account
    assert codes == {"login_failed"}


def test_patient_lockout(client):
    client.post("/api/patient-auth/register",
                json={"consent": True, "patient_id": "LOCK-1", "name": "L", "dob": "1980-01-01", "password": "Patient2026!"})
    for _ in range(5):
        patient_login(client, "LOCK-1", "wrong")
    assert patient_login(client, "LOCK-1", "Patient2026!").status_code == 429


def test_report_only_for_owner_or_clinician(client):
    token = patient_login(client).json()["token"]
    aid = client.get("/api/patients/DEMO-101/dashboard", headers=bearer(token)).json()["result"]["assessment_id"]
    assert client.get(f"/api/assessments/{aid}/report.pdf", headers=bearer(token)).status_code == 200
    assert client.get(f"/api/assessments/{aid}/report.pdf").status_code == 401
    other = patient_login(client, "DEMO-102").json()["token"]
    assert client.get(f"/api/assessments/{aid}/report.pdf", headers=bearer(other)).status_code == 403
    assert client.get(f"/api/assessments/{aid}/report.pdf", headers=clinician(client)).status_code == 200


def test_activation_code_flow(client):
    assert client.post("/api/patients/DEMO-003/activation-code").status_code == 401   # clinicians only
    issued = client.post("/api/patients/DEMO-003/activation-code", headers=clinician(client)).json()
    assert len(issued["code"]) == 6

    wrong = "000000" if issued["code"] != "000000" else "111111"
    assert client.post("/api/patient-auth/activate",
                       json={"consent": True, "patient_id": "DEMO-003", "code": wrong, "password": "Sarah2026!"}).status_code == 401
    ok = client.post("/api/patient-auth/activate", json={"consent": True, "patient_id": "DEMO-003", "code": issued["code"], "password": "Sarah2026!"})
    assert ok.status_code == 200 and ok.json()["profile"]["has_account"]
    # one use only, and the code can't be used for another patient
    assert client.post("/api/patient-auth/activate",
                       json={"consent": True, "patient_id": "DEMO-003", "code": issued["code"], "password": "Other2026!"}).status_code == 401
    assert patient_login(client, "DEMO-003", "Sarah2026!").status_code == 200

    log = client.get("/api/audit", headers=clinician(client)).json()
    assert any(a["action"] == "activation_code" and a["target"] == "DEMO-003" for a in log)


def test_activation_code_tries_are_limited(client):
    code = client.post("/api/patients/DEMO-004/activation-code", headers=clinician(client, "kamogelo")).json()["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        client.post("/api/patient-auth/activate", json={"consent": True, "patient_id": "DEMO-004", "code": wrong, "password": "Pieter2026!"})
    late = client.post("/api/patient-auth/activate", json={"consent": True, "patient_id": "DEMO-004", "code": code, "password": "Pieter2026!"})
    assert late.status_code == 401


def test_logout_ends_patient_session(client):
    h = bearer(patient_login(client, "DEMO-102").json()["token"])
    assert client.get("/api/patient-auth/me", headers=h).status_code == 200
    client.post("/api/patient-auth/logout", headers=h)
    assert client.get("/api/patient-auth/me", headers=h).status_code == 401

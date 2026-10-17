"""Clinician sign-in, sessions, protected endpoints and the audit trail."""
import os

import pytest
from fastapi.testclient import TestClient

os.environ["SKAI_DB"] = ":memory:"

from app.api import app  # noqa: E402
from app.auth import DEMO_CLINICIANS, DEMO_PASSWORD, hash_password, verify_password  # noqa: E402

CLINICAL = dict(age=58, sex=1, cp=0, trestbps=150, chol=270, fbs=1, restecg=1,
                thalach=130, exang=1, oldpeak=2.0, slope=1, ca=1, thal=3)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def login(client, username="sfiso.kekana", password=DEMO_PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def auth(client, username="sfiso.kekana"):
    return {"Authorization": f"Bearer {login(client, username).json()['token']}"}


def test_password_hashing():
    stored = hash_password("correct horse")
    assert "correct horse" not in stored and stored.startswith("pbkdf2_sha256$")
    assert verify_password("correct horse", stored) and not verify_password("wrong", stored)
    assert hash_password("same") != hash_password("same")          # salted


def test_all_demo_doctors_can_sign_in(client):
    for username, name, _ in DEMO_CLINICIANS:
        r = login(client, username)
        assert r.status_code == 200 and r.json()["clinician"]["name"] == name
        assert "password_hash" not in r.json()["clinician"]


def test_wrong_password_and_unknown_user_look_the_same(client):
    wrong, unknown = login(client, password="nope"), login(client, "nobody")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/patients", None),
    ("get", "/api/patients/DEMO-001", None),
    ("post", "/api/clinical/assess", {"inputs": CLINICAL}),
    ("get", "/api/documents/1/file", None),
    ("post", "/api/documents/1/review", {"approve": True}),
    ("get", "/api/audit", None),
])
def test_clinician_endpoints_need_sign_in(client, method, path, body):
    r = getattr(client, method)(path, **({"json": body} if body else {}))
    assert r.status_code == 401
    bad = getattr(client, method)(path, headers={"Authorization": "Bearer not-a-token"}, **({"json": body} if body else {}))
    assert bad.status_code == 401


def test_saving_a_lifestyle_assessment_needs_a_clinician(client):
    body = {"inputs": {"age": 50, "sbp": 140}, "patient": {"id": "X-1", "name": "X"}}
    assert client.post("/api/lifestyle/assess", json=body).status_code == 401
    # the patient app's stateless what-if stays open
    assert client.post("/api/lifestyle/assess", json={"inputs": {"age": 50, "sbp": 140}}).status_code == 200


def test_actions_are_attributed_and_logged(client):
    h = auth(client, "mpho.mutengwe")
    saved = client.post("/api/clinical/assess", headers=h,
                        json={"inputs": CLINICAL, "patient": {"id": "AUD-1", "name": "Audit Patient"}}).json()
    record = client.get("/api/patients/AUD-1", headers=h).json()
    assert record["assessments"][-1]["created_by_name"] == "Dr Mpho Mutengwe"
    queue = {q["id"]: q for q in client.get("/api/patients", headers=h).json()}
    assert queue["AUD-1"]["latest"]["created_by_name"] == "Dr Mpho Mutengwe"
    log = client.get("/api/audit", headers=h).json()
    actions = [(a["clinician_id"], a["action"], a["target"]) for a in log[:3]]
    assert ("mpho.mutengwe", "view_patient", "AUD-1") in actions and ("mpho.mutengwe", "assess", "AUD-1") in actions
    assert saved["assessment_id"]


def test_lockout_after_five_wrong_passwords(client):
    for _ in range(5):
        login(client, "kamogelo", "wrong")
    r = login(client, "kamogelo")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "locked"


def test_logout_ends_the_session(client):
    h = auth(client, "neontle")
    assert client.get("/api/auth/me", headers=h).status_code == 200
    client.post("/api/auth/logout", headers=h)
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_photo_and_password_change(client):
    h = auth(client, "tshiamo.bokaba")
    assert client.put("/api/clinicians/me/photo", headers=h, json={"data_url": "data:image/png;base64,iVBORw0KGgo="}).status_code == 200
    assert client.get("/api/auth/me", headers=h).json()["photo"].startswith("data:image/png")
    assert client.put("/api/clinicians/me/photo", headers=h, json={"data_url": "javascript:alert(1)"}).status_code == 422

    other = auth(client, "tshiamo.bokaba")             # a second session for the same doctor
    assert client.put("/api/clinicians/me/password", headers=h, json={"current": "wrong", "new": "NewPassword1"}).status_code == 403
    assert client.put("/api/clinicians/me/password", headers=h, json={"current": DEMO_PASSWORD, "new": "short"}).status_code == 422
    assert client.put("/api/clinicians/me/password", headers=h, json={"current": DEMO_PASSWORD, "new": "NewPassword1"}).status_code == 200
    assert client.get("/api/auth/me", headers=other).status_code == 401   # other sessions signed out
    assert client.get("/api/auth/me", headers=h).status_code == 200       # this one kept
    assert login(client, "tshiamo.bokaba", "NewPassword1").status_code == 200

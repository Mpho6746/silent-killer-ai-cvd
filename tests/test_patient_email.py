"""Email sign-up: SKAI-made patient IDs, confirmation codes, sign-in by email, password reset."""
import os
import re

import pytest
from fastapi.testclient import TestClient

os.environ["SKAI_DB"] = ":memory:"

from app.api import app  # noqa: E402

NEW = {"name": "Ayanda Zulu", "email": "ayanda@example.com", "dob": "1985-04-02", "sex": "F",
       "password": "Ayanda2026", "consent": True}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def latest_code(client, identifier):
    body = client.get("/api/demo/inbox", params={"identifier": identifier}).json()[0]["body"]
    return re.search(r"code is (\d{6})", body).group(1)


def login(client, identifier, password):
    return client.post("/api/patient-auth/login", json={"patient_id": identifier, "password": password})


def test_sign_up_confirm_and_sign_in(client):
    r = client.post("/api/patient-auth/register", json=NEW).json()
    pid = r["patient_id"]
    assert re.fullmatch(r"SKAI-\d{6}", pid) and r["needs_verification"] and "token" not in r
    # the welcome email carries the patient ID
    assert pid in client.get("/api/demo/inbox", params={"identifier": NEW["email"]}).json()[0]["body"]
    # no access until the email is confirmed
    blocked = login(client, NEW["email"], NEW["password"])
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "email_unverified"
    assert client.post("/api/patient-auth/verify-email", json={"identifier": pid, "code": "000000"}).status_code == 401
    ok = client.post("/api/patient-auth/verify-email", json={"identifier": NEW["email"], "code": latest_code(client, pid)})
    assert ok.status_code == 200 and ok.json()["profile"]["id"] == pid
    # sign in with the email in any capitals, or the patient ID
    assert login(client, "AYANDA@Example.com", NEW["password"]).status_code == 200
    assert login(client, pid.lower(), NEW["password"]).status_code == 200


def test_email_rules(client):
    taken = client.post("/api/patient-auth/register", json={**NEW, "email": "Ayanda@EXAMPLE.com"})
    assert taken.status_code == 409 and taken.json()["detail"]["code"] == "email_taken"
    bad = client.post("/api/patient-auth/register", json={**NEW, "email": "not-an-email"})
    assert bad.json()["detail"]["code"] == "email_invalid"


def test_password_reset(client):
    pid = client.post("/api/patient-auth/register", json={**NEW, "email": "reset@example.com"}).json()["patient_id"]
    # forgot-password gives the same answer for unknown accounts
    assert client.post("/api/patient-auth/forgot", json={"identifier": "nobody@example.com"}).json() == {"ok": True}
    client.post("/api/patient-auth/forgot", json={"identifier": "reset@example.com"})
    r = client.post("/api/patient-auth/reset", json={"identifier": pid, "code": latest_code(client, pid), "password": "Brand-New-2026"})
    assert r.status_code == 200
    assert login(client, pid, "Brand-New-2026").status_code == 200        # reset also confirms the email
    assert login(client, pid, NEW["password"]).status_code == 401


def test_codes_expire_after_five_wrong_tries(client):
    pid = client.post("/api/patient-auth/register", json={**NEW, "email": "tries@example.com"}).json()["patient_id"]
    code = latest_code(client, pid)
    for _ in range(5):
        client.post("/api/patient-auth/verify-email", json={"identifier": pid, "code": "111111" if code != "111111" else "222222"})
    assert client.post("/api/patient-auth/verify-email", json={"identifier": pid, "code": code}).status_code == 401

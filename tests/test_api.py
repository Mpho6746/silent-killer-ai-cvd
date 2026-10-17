"""API tests: run with `python -m pytest` from the project root."""
import os

import pytest
from fastapi.testclient import TestClient

os.environ["SKAI_DB"] = ":memory:"   # never touch the real patient database

from app.api import app  # noqa: E402
from app.auth import DEMO_PASSWORD  # noqa: E402

HIGH_RISK = dict(age=65, sex=1, cp=0, trestbps=160, chol=280, fbs=1, restecg=0,
                 thalach=110, exang=1, oldpeak=3.0, slope=1, ca=3, thal=3)
LOW_RISK = dict(age=40, sex=0, cp=2, trestbps=115, chol=190, fbs=0, restecg=1,
                thalach=175, exang=0, oldpeak=0.0, slope=2, ca=0, thal=2)
SMOKER = dict(age=45, sbp=150, cigarettes_per_day=10, years_smoked=2, family_history=True,
              height_cm=175, weight_kg=95, ldl=5.0)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        # Clinician endpoints need a signed-in clinician; patient endpoints ignore the header
        token = c.post("/api/auth/login", json={"username": "samuel.olajide", "password": DEMO_PASSWORD}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


def test_clinical_risk_points_the_right_way(client):
    high = client.post("/api/clinical/assess", json={"inputs": HIGH_RISK}).json()
    low = client.post("/api/clinical/assess", json={"inputs": LOW_RISK}).json()
    assert high["probability"] > 0.8 and high["tier"] in ("high", "critical")
    assert low["probability"] < 0.3 and low["tier"] == "low"


def test_clinical_explanation_and_plan(client):
    r = client.post("/api/clinical/assess", json={"inputs": HIGH_RISK}).json()
    assert len(r["drivers"]) == 15
    assert abs(r["drivers"][0]["impact"]) >= abs(r["drivers"][-1]["impact"])   # sorted by impact
    for step in r["plan"]:
        assert step["probability"] < r["probability"]                         # plan only lowers risk


def test_lifestyle_projection_and_estimates(client):
    r = client.post("/api/lifestyle/assess", json={"inputs": SMOKER}).json()
    assert r["projection"]["probability_if_unchanged"] >= r["probability"]   # older + more tobacco
    assert {"adiposity", "typea", "alcohol"} <= set(r["estimated"])
    assert any(p["action"] == "quit_smoking" for p in r["plan"])
    for step in r["plan"]:
        assert step["probability_in_10y"] <= r["projection"]["probability_if_unchanged"]


def test_lifestyle_caps_out_of_range_tobacco(client):
    heavy = {**SMOKER, "cigarettes_per_day": 40, "years_smoked": 30}
    r = client.post("/api/lifestyle/assess", json={"inputs": heavy}).json()
    assert "tobacco" in r["clipped"]
    assert r["probability"] < 0.999


@pytest.mark.parametrize("bad", [
    {"inputs": {**HIGH_RISK, "trestbps": 20}},
    {"inputs": {**HIGH_RISK, "sex": 2}},
    {"inputs": {k: v for k, v in HIGH_RISK.items() if k != "age"}},
])
def test_clinical_validation(client, bad):
    assert client.post("/api/clinical/assess", json=bad).status_code == 422


def test_years_smoked_cannot_exceed_age(client):
    r = client.post("/api/lifestyle/assess", json={"inputs": {"age": 30, "sbp": 120, "years_smoked": 40}})
    assert r.status_code == 422


def test_save_history_queue_and_pdf(client):
    patient = {"id": "TEST-1", "name": "Test Patient", "sex": "F"}
    first = client.post("/api/clinical/assess", json={"inputs": LOW_RISK, "patient": patient}).json()
    second = client.post("/api/clinical/assess", json={"inputs": HIGH_RISK, "patient": patient}).json()

    history = client.get("/api/patients/TEST-1").json()
    assert [a["id"] for a in history["assessments"]] == [first["assessment_id"], second["assessment_id"]]

    entry = next(q for q in client.get("/api/patients").json() if q["id"] == "TEST-1")
    assert "rising" in entry["flags"] and "high_risk" in entry["flags"]

    pdf = client.get(f"/api/assessments/{second['assessment_id']}/report.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_demo_patients_seeded_and_prioritised(client):
    queue = client.get("/api/patients").json()
    demo = [q for q in queue if q["id"].startswith("DEMO-")]
    assert len(demo) == 7
    flagged = [q for q in queue if q["flags"]]
    assert queue[:len(flagged)] == flagged          # flagged patients come first


def test_missing_things_return_404(client):
    assert client.get("/api/patients/NOPE").status_code == 404
    assert client.get("/api/assessments/999999/report.pdf").status_code == 404


def test_model_info(client):
    info = client.get("/api/model-info").json()
    assert 0.7 < info["clinical"]["test_metrics"]["roc_auc"] <= 1
    assert info["subgroups"]["uci_sex"]


def test_web_app_served(client):
    assert "SKAI" in client.get("/").text
    assert client.get("/app.js").status_code == 200


def test_live_server_origin_allowed(client):
    """The page can also be opened from VS Code Live Server (:5500) and call the API."""
    r = client.get("/api/health", headers={"Origin": "http://127.0.0.1:5500"})
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:5500"
    r = client.get("/api/health", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in r.headers

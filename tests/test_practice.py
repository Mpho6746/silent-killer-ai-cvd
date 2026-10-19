"""SATS triage, validated score, roles/facilities, bookings, referrals and reminders."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

os.environ["SKAI_DB"] = ":memory:"

from app.api import app  # noqa: E402
from app.auth import DEMO_PASSWORD  # noqa: E402
from app.messaging import normalise_phone  # noqa: E402
from app.scores import framingham  # noqa: E402
from app.triage import Vitals, colour_for, tews  # noqa: E402

CLINICAL = dict(age=58, sex=1, cp=0, trestbps=150, chol=270, fbs=1, restecg=1,
                thalach=130, exang=1, oldpeak=2.0, slope=1, ca=1, thal=3)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def staff(client, username):
    return {"Authorization": f"Bearer {client.post('/api/auth/login', json={'username': username, 'password': DEMO_PASSWORD}).json()['token']}"}


# ---------- SATS ----------

def test_tews_normal_adult_is_green():
    v = Vitals(mobility="walking", rr=14, hr=72, sbp=124, temp=36.8, avpu="alert")
    assert tews(v) == 0 and colour_for(0, []) == "green"


def test_tews_sick_adult_is_red():
    v = Vitals(mobility="stretcher", rr=32, hr=135, sbp=70, temp=39.5, avpu="pain", trauma=True)
    assert tews(v) == 2 + 3 + 3 + 3 + 2 + 2 + 1 and colour_for(tews(v), []) == "red"


@pytest.mark.parametrize("score,colour", [(2, "green"), (3, "yellow"), (4, "yellow"), (5, "orange"), (6, "orange"), (7, "red")])
def test_tews_bands(score, colour):
    assert colour_for(score, []) == colour


def test_discriminator_raises_colour_but_never_lowers_it():
    assert colour_for(0, ["chest_pain"]) == "orange"
    assert colour_for(8, ["moderate_pain"]) == "red"


def test_waiting_room_order(client):
    queue = client.get("/api/triage/queue", headers=staff(client, "nurse.thandi")).json()
    order = [(v["colour"], v["cardiac_flag"], v["name"]) for v in queue]
    colours = [c for c, _, _ in order]
    assert colours == sorted(colours, key=["red", "orange", "yellow", "green"].index)
    greens = [(flag, name) for c, flag, name in order if c == "green"]
    assert greens[0] == (1, "Thabo Ndlovu")          # SKAI high-risk first within green, even though he arrived later


def test_record_arrival_and_permissions(client):
    nurse = staff(client, "nurse.thandi")
    body = {"name": "Test Walk-in", "vitals": {"mobility": "walking", "rr": 22, "hr": 104, "sbp": 150, "temp": 36.9,
                                               "avpu": "alert"}, "discriminators": ["shortness_of_breath"]}
    r = client.post("/api/triage", headers=nurse, json=body).json()
    assert r["colour"] == "orange" and r["target_minutes"] == 10
    assert client.post("/api/triage", headers=staff(client, "manager.district"), json=body).status_code == 403
    bad = {**body, "discriminators": ["made_up"]}
    assert client.post("/api/triage", headers=nurse, json=bad).status_code == 422


# ---------- validated score ----------

def test_framingham_reproduces_published_examples():
    woman = framingham("F", 61, 124, False, True, False, tc_mg=180, hdl_mg=47)
    man = framingham("M", 53, 125, True, False, True, tc_mg=161, hdl_mg=55)
    assert round(woman["risk_10y"] * 100, 2) == 10.48
    assert round(man["risk_10y"] * 100, 2) == 15.62


def test_framingham_limits():
    assert framingham("F", 25, 120, False, False, False, bmi=22)["reason"] == "age_outside_30_74"
    assert framingham(None, 50, 120, False, False, False, bmi=22)["reason"] == "sex_unknown"
    assert framingham("M", 50, 120, False, False, False)["reason"] == "needs_cholesterol_or_bmi"


# ---------- roles and facilities ----------

def test_staff_only_see_their_facility(client):
    kopanong = {q["id"] for q in client.get("/api/patients", headers=staff(client, "sfiso.kekana")).json()}
    ubuntu = {q["id"] for q in client.get("/api/patients", headers=staff(client, "kamogelo")).json()}
    district = {q["id"] for q in client.get("/api/patients", headers=staff(client, "manager.district")).json()}
    assert "DEMO-001" in kopanong and "DEMO-001" not in ubuntu
    assert kopanong | ubuntu <= district
    assert client.get("/api/patients/DEMO-002", headers=staff(client, "sfiso.kekana")).status_code == 403


def test_role_permissions(client):
    nurse, manager = staff(client, "nurse.thandi"), staff(client, "manager.district")
    assert client.post("/api/documents/1/review", headers=nurse, json={"approve": True}).status_code == 403
    assert client.post("/api/clinical/assess", headers=manager,
                       json={"inputs": CLINICAL, "patient": {"id": "DEMO-001", "name": "Thabo Ndlovu"}}).status_code == 403
    me = client.get("/api/auth/me", headers=nurse).json()
    assert me["role"] == "nurse" and me["facility_name"] == "Kopanong Community Clinic"


# ---------- bookings and referrals ----------

def test_priority_booking(client):
    doc = staff(client, "sfiso.kekana")
    before = [p["id"] for p in client.get("/api/bookings/priority", headers=doc).json()]
    assert "DEMO-001" in before
    when = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert client.post("/api/appointments", headers=doc,
                       json={"patient_id": "DEMO-001", "starts_at": when, "reason": "Cardiac review", "priority": "urgent"}).status_code == 200
    assert "DEMO-001" not in [p["id"] for p in client.get("/api/bookings/priority", headers=doc).json()]
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert client.post("/api/appointments", headers=doc,
                       json={"patient_id": "DEMO-001", "starts_at": past, "reason": "x"}).status_code == 422


def test_referral_loop(client):
    clinic, hospital = staff(client, "mpho.mutengwe"), staff(client, "tshiamo.bokaba")
    assert client.get("/api/patients/DEMO-003", headers=hospital).status_code == 403      # not yet referred
    rid = client.post("/api/referrals", headers=clinic, json={"patient_id": "DEMO-003", "to_facility": "lesedi-hospital",
                                                              "reason": "Chest pain on exertion", "urgency": "urgent"}).json()["id"]
    assert client.get("/api/patients/DEMO-003", headers=hospital).status_code == 200      # referral grants access
    assert client.get(f"/api/referrals/{rid}/letter.pdf", headers=clinic).content.startswith(b"%PDF")
    # only the receiving facility can confirm arrival
    assert client.post(f"/api/referrals/{rid}/status", headers=clinic, json={"status": "arrived"}).status_code == 403
    assert client.post(f"/api/referrals/{rid}/status", headers=hospital, json={"status": "arrived"}).status_code == 200
    incoming = client.get("/api/referrals", headers=hospital).json()["incoming"]
    assert next(r for r in incoming if r["id"] == rid)["status"] == "arrived"
    assert client.post(f"/api/referrals/{rid}/status", headers=clinic, json={"status": "closed"}).status_code == 200
    assert client.get("/api/patients/DEMO-003", headers=hospital).status_code == 403      # closed: access ends


def test_overdue_referral_flagged_in_queue(client):
    queue = {q["id"]: q for q in client.get("/api/patients", headers=staff(client, "sfiso.kekana")).json()}
    assert "referral_overdue" in queue["DEMO-001"]["flags"]


# ---------- reminders ----------

@pytest.mark.parametrize("raw,expected", [("082 123 4567", "+27821234567"), ("+27 82 123 4567", "+27821234567"),
                                          ("27821234567", "+27821234567"), ("012 345 6789", None), ("hello", None)])
def test_phone_normalisation(raw, expected):
    assert normalise_phone(raw) == expected


def test_reminders_only_for_opted_in_patients(client):
    doc = staff(client, "kamogelo")
    when = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    client.post("/api/appointments", headers=doc, json={"patient_id": "DEMO-004", "starts_at": when, "reason": "Follow-up"})
    client.post("/api/messages/dispatch", headers=doc)
    outbox = client.get("/api/messages", headers=doc).json()
    assert not [m for m in outbox if m["patient_id"] == "DEMO-004"]        # Pieter has not opted in
    john = client.get("/api/messages", headers=staff(client, "sfiso.kekana")).json()
    assert any(m["patient_id"] == "DEMO-101" and m["status"] == "sent" for m in john)


# ---------- hospitals by province ----------

def test_at_least_five_hospitals_in_every_province(client):
    facilities = client.get("/api/facilities").json()
    by_province = {}
    for f in facilities:
        by_province.setdefault(f["province"], []).append(f)
    provinces = {"Gauteng", "Western Cape", "KwaZulu-Natal", "Eastern Cape", "Free State", "Limpopo",
                 "Mpumalanga", "North West", "Northern Cape"}
    assert provinces <= set(by_province)
    assert all(len(by_province[p]) >= 5 and all(f["kind"] == "hospital" for f in by_province[p]) for p in provinces)
    assert facilities[0]["province"].startswith("Demo")          # the demo district is listed first
    # a clinic can refer to one of them
    r = client.post("/api/referrals", headers=staff(client, "sfiso.kekana"),
                    json={"patient_id": "DEMO-101", "to_facility": "gp-steve-biko", "reason": "Cardiology review", "urgency": "routine"})
    assert r.status_code == 200

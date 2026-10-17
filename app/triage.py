"""
Arrival triage with the South African Triage Scale (SATS), adult version.

SATS is the national standard for emergency triage: a nurse scores the arrival
vitals (TEWS, Triage Early Warning Score) and checks for clinical "discriminator"
signs. The higher of the two decides the colour:

    colour   TEWS     see within
    red      7+       immediately
    orange   5-6      10 minutes
    yellow   3-4      60 minutes
    green    0-2      4 hours

SKAI does not override SATS. Within the same colour it moves patients with a
known high cardiovascular risk (their latest SKAI tier is high or critical)
ahead, then sorts by arrival time. This is the defensible version of
"prioritise the people at most risk": acute danger first, then silent risk.

The discriminator list below is a simplified subset of the SATS adult chart
for the demo; a deployment would use the full current SATS chart.

    GET  /api/triage/options        discriminator signs for the form
    POST /api/triage/preview        score without saving (live as the nurse types)
    POST /api/triage                record an arrival (nurse/doctor)
    GET  /api/triage/queue          today's waiting room for my facility, in SATS order
    POST /api/triage/{id}/status    in_consult / done / left
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.facilities import require, require_visible

router = APIRouter()

COLOURS = ["red", "orange", "yellow", "green"]
TARGET_MINUTES = {"red": 0, "orange": 10, "yellow": 60, "green": 240}
DISCRIMINATORS = {
    "red": ["airway_compromised", "not_breathing", "cardiac_arrest", "seizure_current", "hypoglycaemia",
            "burn_face_inhalation"],
    "orange": ["chest_pain", "shortness_of_breath", "focal_neurology", "reduced_consciousness", "haemorrhage_uncontrolled",
               "seizure_post_ictal", "severe_pain", "overdose_poisoning", "high_energy_trauma"],
    "yellow": ["moderate_pain", "persistent_vomiting", "abdominal_pain", "closed_fracture"],
}
DISCRIMINATOR_COLOUR = {d: c for c, ds in DISCRIMINATORS.items() for d in ds}


class Vitals(BaseModel):
    mobility: Literal["walking", "with_help", "stretcher"]
    rr: int = Field(ge=0, le=80, description="Respiratory rate, breaths/min")
    hr: int = Field(ge=0, le=250, description="Heart rate, beats/min")
    sbp: int = Field(ge=0, le=300, description="Systolic blood pressure, mmHg")
    temp: float = Field(ge=25, le=45, description="Temperature, degrees C")
    avpu: Literal["alert", "voice", "pain", "unresponsive"]
    trauma: bool = False
    dbp: Optional[int] = Field(None, ge=20, le=200, description="Diastolic blood pressure, mmHg (not part of TEWS)")
    # "device": read straight from a connected clinic BP monitor, so not retyped by hand
    bp_source: Literal["manual", "device"] = "manual"
    bp_device: Optional[str] = Field(None, max_length=80)


class Arrival(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    patient_id: Optional[str] = Field(None, max_length=40)    # if the patient is already registered
    vitals: Vitals
    discriminators: list[str] = Field(default_factory=list, max_length=20)


class Status(BaseModel):
    status: Literal["in_consult", "done", "left"]


def tews(v):
    """SATS Triage Early Warning Score (adult)."""
    score = {"walking": 0, "with_help": 1, "stretcher": 2}[v.mobility]
    score += 3 if v.rr < 9 else 0 if v.rr <= 14 else 1 if v.rr <= 20 else 2 if v.rr <= 29 else 3
    score += 3 if v.hr < 41 else 2 if v.hr <= 50 else 0 if v.hr <= 100 else 1 if v.hr <= 110 else 2 if v.hr <= 129 else 3
    score += 3 if v.sbp < 71 else 2 if v.sbp <= 80 else 1 if v.sbp <= 100 else 0 if v.sbp <= 199 else 2
    score += 2 if v.temp < 35 or v.temp > 38.4 else 0
    score += {"alert": 0, "voice": 1, "pain": 2, "unresponsive": 3}[v.avpu]
    score += 1 if v.trauma else 0
    return score


def colour_for(score, discriminators):
    by_score = "red" if score >= 7 else "orange" if score >= 5 else "yellow" if score >= 3 else "green"
    candidates = [by_score] + [DISCRIMINATOR_COLOUR[d] for d in discriminators if d in DISCRIMINATOR_COLOUR]
    return min(candidates, key=COLOURS.index)


def score(arrival):
    unknown = [d for d in arrival.discriminators if d not in DISCRIMINATOR_COLOUR]
    if unknown:
        raise HTTPException(422, f"Unknown discriminators: {unknown}")
    t = tews(arrival.vitals)
    return {"tews": t, "colour": colour_for(t, arrival.discriminators), "target_minutes": None}


def cardiac_flag(store, patient_id):
    """SKAI's 'silent risk' layer: the patient's latest risk tier is high or critical."""
    if not patient_id:
        return False
    row = store.conn.execute("SELECT tier FROM assessments WHERE patient_id = ? ORDER BY created_at DESC LIMIT 1",
                             (patient_id,)).fetchone()
    return bool(row and row["tier"] in ("high", "critical"))


def _now():
    return datetime.now(timezone.utc)


@router.get("/api/triage/options")
def options():
    return {"discriminators": DISCRIMINATORS, "targets": TARGET_MINUTES}


@router.post("/api/triage/preview")
def preview(arrival: Arrival, request: Request, clinician=Depends(require("triage"))):
    result = score(arrival)
    result["cardiac_flag"] = cardiac_flag(request.app.state.store, arrival.patient_id)
    result["target_minutes"] = TARGET_MINUTES[result["colour"]]
    return result


@router.post("/api/triage")
def record_arrival(arrival: Arrival, request: Request, clinician=Depends(require("triage"))):
    store = request.app.state.store
    if arrival.patient_id:
        if not store.get_profile(arrival.patient_id):
            raise HTTPException(404, "Patient not found")
        require_visible(store, clinician, arrival.patient_id)
    result = score(arrival)
    flag = cardiac_flag(store, arrival.patient_id)
    cur = store.conn.execute(
        """INSERT INTO triage_visits (facility_id, patient_id, name, arrived_at, vitals, discriminators, tews, colour,
                                      cardiac_flag, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (clinician["facility_id"], arrival.patient_id, arrival.name, _now().isoformat(timespec="seconds"),
         arrival.vitals.model_dump_json(), json.dumps(arrival.discriminators), result["tews"], result["colour"],
         int(flag), clinician["id"]))
    store.conn.commit()
    store.log(clinician["id"], "triage", arrival.patient_id or arrival.name)
    return {"id": cur.lastrowid, **result, "cardiac_flag": flag, "target_minutes": TARGET_MINUTES[result["colour"]]}


def waiting_room(store, facility_id=None, now=None):
    """Patients still in the unit, in SATS order: colour, then SKAI cardiac flag, then arrival time."""
    now = now or _now()
    since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    query = """SELECT t.*, f.name AS facility_name FROM triage_visits t JOIN facilities f ON f.id = t.facility_id
               WHERE t.status IN ('waiting', 'in_consult') AND t.arrived_at >= ?"""
    params = [since]
    if facility_id:
        query += " AND t.facility_id = ?"
        params.append(facility_id)
    visits = []
    for r in store.conn.execute(query, params).fetchall():
        v = dict(r)
        v["vitals"] = json.loads(v["vitals"])
        v["discriminators"] = json.loads(v["discriminators"])
        v["waited_minutes"] = int((now - datetime.fromisoformat(v["arrived_at"])).total_seconds() // 60)
        v["target_minutes"] = TARGET_MINUTES[v["colour"]]
        v["overdue"] = v["status"] == "waiting" and v["waited_minutes"] > v["target_minutes"]
        visits.append(v)
    visits.sort(key=lambda v: (v["status"] != "in_consult", COLOURS.index(v["colour"]), -v["cardiac_flag"], v["arrived_at"]))
    return visits


@router.get("/api/triage/queue")
def queue(request: Request, clinician=Depends(require("messages"))):
    facility = None if clinician.get("role") == "district_manager" else clinician.get("facility_id")
    return waiting_room(request.app.state.store, facility)


@router.post("/api/triage/{visit_id}/status")
def set_status(visit_id: int, body: Status, request: Request, clinician=Depends(require("triage"))):
    store = request.app.state.store
    row = store.conn.execute("SELECT facility_id FROM triage_visits WHERE id = ?", (visit_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Visit not found")
    if row["facility_id"] != clinician.get("facility_id"):
        raise HTTPException(403, detail={"code": "not_your_facility"})
    seen = _now().isoformat(timespec="seconds") if body.status == "in_consult" else None
    store.conn.execute("UPDATE triage_visits SET status = ?, seen_at = COALESCE(seen_at, ?) WHERE id = ?",
                       (body.status, seen, visit_id))
    store.conn.commit()
    return {"ok": True}


def seed_waiting_room(store):
    """A morning waiting room at Kopanong clinic for the demo (when nobody arrived in the last 12 hours)."""
    now = _now()
    cutoff = (now - timedelta(hours=12)).isoformat(timespec="seconds")
    # Visits still open from an earlier day: the clinic has closed, so they left
    store.conn.execute("UPDATE triage_visits SET status = 'left' WHERE status IN ('waiting', 'in_consult') AND arrived_at < ?", (cutoff,))
    store.conn.commit()
    if store.conn.execute("SELECT COUNT(*) FROM triage_visits WHERE arrived_at >= ?", (cutoff,)).fetchone()[0]:
        return
    arrivals = [
        # minutes ago, name, patient_id, vitals, discriminators
        (95, "Mary Khumalo", None, dict(mobility="walking", rr=16, hr=78, sbp=128, temp=36.6, avpu="alert"), ["moderate_pain"]),
        (80, "Thabo Ndlovu", "DEMO-001", dict(mobility="walking", rr=14, hr=72, sbp=150, temp=36.8, avpu="alert"), []),
        (70, "Joseph Sithole", None, dict(mobility="walking", rr=15, hr=70, sbp=124, temp=36.5, avpu="alert"), []),
        (6, "Grace Molefe", None, dict(mobility="walking", rr=22, hr=104, sbp=146, temp=36.9, avpu="alert"), ["chest_pain"]),
        (2, "Lerato Nkosi", None, dict(mobility="with_help", rr=24, hr=118, sbp=96, temp=38.9, avpu="voice"), []),
        (5, "Peter van Wyk", None, dict(mobility="walking", rr=16, hr=88, sbp=132, temp=36.7, avpu="alert"), ["abdominal_pain"]),
    ]
    for minutes, name, pid, v, discs in arrivals:
        vitals = Vitals(**v)
        t = tews(vitals)
        store.conn.execute(
            """INSERT INTO triage_visits (facility_id, patient_id, name, arrived_at, vitals, discriminators, tews, colour,
                                          cardiac_flag, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("kopanong-clinic", pid, name, (now - timedelta(minutes=minutes)).isoformat(timespec="seconds"),
             vitals.model_dump_json(), json.dumps(discs), t, colour_for(t, discs), int(cardiac_flag(store, pid)), "nurse.thandi"))
    store.conn.commit()

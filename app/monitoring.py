"""
Pilot study support: real outcomes, live performance and bias monitoring, and
a district view of how each facility is doing.

A model validated on old datasets (UCI Cleveland, SAHeart) has to be checked
again on the people it is actually used on. When a doctor confirms whether a
patient has cardiovascular disease (echo, angiogram, ECG, clinical diagnosis),
that outcome is recorded here and compared with what SKAI predicted:

    POST /api/patients/{id}/outcome   doctor records a confirmed outcome
    GET  /api/monitoring              recall, false alarms and calibration, overall and by
                                      sex, age group and model, with plain-language alerts
    GET  /api/district                per-facility operations (district/facility managers, doctors)

"Flagged" means SKAI placed the patient in the high or critical tier.
With fewer than MIN_N outcomes in a group, numbers are shown but not judged.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.facilities import require, require_visible, visible_patient_ids

router = APIRouter()

MIN_N = 30                 # below this a group's numbers are too uncertain to act on
RECALL_TARGET = 0.80       # missing more than 1 in 5 people with disease is an alert
GAP_ALERT = 0.15           # recall differing this much between groups is a bias alert
CALIBRATION_ALERT = 0.15   # predicted vs observed rate differing this much -> recalibrate


class Outcome(BaseModel):
    has_cvd: bool
    source: Literal["echocardiogram", "angiogram", "ecg", "stress_test", "clinical_diagnosis", "hospital_admission"]


def save_outcome(store, patient_id, has_cvd, source, clinician_id, at=None):
    store.conn.execute(
        """INSERT INTO outcomes (patient_id, has_cvd, source, recorded_by, recorded_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(patient_id) DO UPDATE SET has_cvd = excluded.has_cvd, source = excluded.source,
               recorded_by = excluded.recorded_by, recorded_at = excluded.recorded_at""",
        (patient_id, int(has_cvd), source, clinician_id, at or datetime.now(timezone.utc).isoformat(timespec="seconds")))
    store.conn.commit()


@router.post("/api/patients/{patient_id}/outcome")
def record_outcome(patient_id: str, body: Outcome, request: Request, clinician=Depends(require("record_outcome"))):
    store = request.app.state.store
    require_visible(store, clinician, patient_id)
    save_outcome(store, patient_id, body.has_cvd, body.source, clinician["id"])
    store.log(clinician["id"], "outcome", patient_id)
    return {"patient_id": patient_id, "has_cvd": body.has_cvd, "source": body.source}


def _age(row):
    if row["dob"]:
        born = date.fromisoformat(row["dob"])
        return (date.today() - born).days // 365
    return row["inputs_age"]


def _rows(store, clinician):
    """Each patient with an outcome and the last SKAI assessment made before (or at) it."""
    rows = store.conn.execute(
        """SELECT p.id, p.sex, p.dob, p.facility_id, o.has_cvd, a.probability, a.tier, a.kind,
                  json_extract(a.inputs, '$.age') AS inputs_age
           FROM outcomes o JOIN patients p ON p.id = o.patient_id
           JOIN assessments a ON a.id = (SELECT id FROM assessments WHERE patient_id = p.id AND created_at <= o.recorded_at
                                         ORDER BY created_at DESC LIMIT 1)""").fetchall()
    visible = visible_patient_ids(store, clinician)
    return [dict(r, age=_age(r)) for r in rows if visible is None or r["id"] in visible]


def _stats(rows):
    n = len(rows)
    sick = [r for r in rows if r["has_cvd"]]
    well = [r for r in rows if not r["has_cvd"]]
    flagged = lambda r: r["tier"] in ("high", "critical")
    out = {"n": n, "with_cvd": len(sick), "enough": n >= MIN_N,
           "recall": sum(map(flagged, sick)) / len(sick) if sick else None,
           "false_alarm_rate": sum(map(flagged, well)) / len(well) if well else None,
           "mean_predicted": sum(r["probability"] for r in rows) / n if n else None,
           "observed_rate": len(sick) / n if n else None}
    out["calibration_gap"] = (out["mean_predicted"] - out["observed_rate"]) if n else None
    return out


@router.get("/api/monitoring")
def monitoring(request: Request, clinician=Depends(require("dashboard"))):
    rows = _rows(request.app.state.store, clinician)
    groups = {
        "sex": {"Female": [r for r in rows if r["sex"] == "F"], "Male": [r for r in rows if r["sex"] == "M"]},
        "age": {"Under 50": [r for r in rows if r["age"] is not None and r["age"] < 50],
                "50 and over": [r for r in rows if r["age"] is not None and r["age"] >= 50]},
        "model": {"Clinical": [r for r in rows if r["kind"] == "clinical"],
                  "Lifestyle": [r for r in rows if r["kind"] == "lifestyle"]},
    }
    result = {"overall": _stats(rows), "groups": {g: {name: _stats(rs) for name, rs in parts.items()} for g, parts in groups.items()},
              "thresholds": {"min_n": MIN_N, "recall_target": RECALL_TARGET, "gap_alert": GAP_ALERT,
                             "calibration_alert": CALIBRATION_ALERT}}
    alerts = []
    o = result["overall"]
    if not o["enough"]:
        alerts.append({"level": "info", "text": f"Only {o['n']} confirmed outcomes so far. At least {MIN_N} are needed before these numbers can be trusted; keep recording outcomes."})
    else:
        if o["recall"] is not None and o["recall"] < RECALL_TARGET:
            alerts.append({"level": "danger", "text": f"SKAI flagged only {o['recall']:.0%} of patients who turned out to have heart disease (target {RECALL_TARGET:.0%}). Review the risk thresholds."})
        if abs(o["calibration_gap"]) > CALIBRATION_ALERT:
            direction = "over" if o["calibration_gap"] > 0 else "under"
            alerts.append({"level": "warning", "text": f"Predicted risk {direction}estimates what is happening here by {abs(o['calibration_gap']):.0%}. The model should be recalibrated on local data."})
    for g, parts in result["groups"].items():
        judged = [(name, s) for name, s in parts.items() if s["enough"] and s["recall"] is not None]
        if len(judged) == 2 and abs(judged[0][1]["recall"] - judged[1][1]["recall"]) > GAP_ALERT:
            low = min(judged, key=lambda x: x[1]["recall"])
            alerts.append({"level": "danger", "text": f"Possible bias by {g}: {low[0]} patients with heart disease are flagged less often ({low[1]['recall']:.0%})."})
    if o["enough"] and not alerts:
        alerts.append({"level": "good", "text": "Performance on local patients is within the targets."})
    result["alerts"] = alerts
    return result


@router.get("/api/district")
def district(request: Request, clinician=Depends(require("dashboard"))):
    store = request.app.state.store
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat(timespec="seconds")
    if clinician["role"] == "district_manager":
        facilities = store.conn.execute("SELECT * FROM facilities WHERE kind != 'district' ORDER BY kind, name").fetchall()
    else:
        facilities = store.conn.execute("SELECT * FROM facilities WHERE id = ?", (clinician["facility_id"],)).fetchall()
    out = []
    for f in facilities:
        fid = f["id"]
        one = lambda sql, *p: store.conn.execute(sql, (fid, *p)).fetchone()[0]
        latest = """SELECT a.tier FROM assessments a WHERE a.patient_id = p.id ORDER BY a.created_at DESC LIMIT 1"""
        out.append({
            "id": fid, "name": f["name"], "kind": f["kind"],
            "patients": one("SELECT COUNT(*) FROM patients WHERE facility_id = ?"),
            "high_risk": one(f"SELECT COUNT(*) FROM patients p WHERE p.facility_id = ? AND ({latest}) IN ('high', 'critical')"),
            "high_risk_unbooked": one(f"""SELECT COUNT(*) FROM patients p WHERE p.facility_id = ? AND ({latest}) IN ('high', 'critical')
                                          AND NOT EXISTS (SELECT 1 FROM appointments ap WHERE ap.patient_id = p.id AND ap.status = 'booked')"""),
            "waiting": one("SELECT COUNT(*) FROM triage_visits WHERE facility_id = ? AND status = 'waiting'"),
            "open_referrals_in": one("SELECT COUNT(*) FROM referrals WHERE to_facility = ? AND status IN ('sent', 'arrived')"),
            "overdue_referrals_out": one("SELECT COUNT(*) FROM referrals WHERE from_facility = ? AND status = 'sent' AND due_by < ?",
                                         now.isoformat(timespec="seconds")),
            "attended_30d": one("SELECT COUNT(*) FROM appointments WHERE facility_id = ? AND status = 'attended' AND starts_at >= ?", since),
            "missed_30d": one("SELECT COUNT(*) FROM appointments WHERE facility_id = ? AND status = 'missed' AND starts_at >= ?", since),
            "outcomes": one("SELECT COUNT(*) FROM outcomes o JOIN patients p ON p.id = o.patient_id WHERE p.facility_id = ?"),
        })
        row = out[-1]
        if clinician["role"] == "district_manager" and not any(v for key, v in row.items() if key not in ("id", "name", "kind")):
            out.pop()                        # no activity at this facility
    return out


def seed_outcomes(store):
    """A few confirmed outcomes so the monitoring screen has something to show (demo only)."""
    if store.conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]:
        return
    later = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(timespec="seconds")
    for pid, has_cvd, source, doctor in [("DEMO-001", True, "angiogram", "samuel.olajide"),
                                         ("DEMO-002", True, "echocardiogram", "kamogelo"),
                                         ("DEMO-003", False, "stress_test", "sfiso.kekana"),
                                         ("DEMO-004", True, "ecg", "neontle"),            # SKAI said moderate: a miss
                                         ("DEMO-005", False, "clinical_diagnosis", "tshiamo.bokaba")]:
        if store.get_profile(pid):
            save_outcome(store, pid, has_cvd, source, doctor, at=later)

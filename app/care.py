"""
Care coordination: priority booking and closed-loop referrals.

Priority booking moves "most at risk first" to where it is practical: the
appointment book, not the waiting-room queue. High-risk patients without an
upcoming appointment are listed first, with a suggested booking window.

Referrals are tracked until the receiving facility confirms the patient
arrived; a referral that is not completed by its due date is flagged, because
lost referrals are one of the biggest gaps between clinic and hospital care.

    GET  /api/bookings/priority           high-risk patients needing an appointment (most at risk first)
    POST /api/appointments                book {patient_id, starts_at, reason, priority}
    GET  /api/appointments                upcoming appointments at my facility
    POST /api/appointments/{id}/status    attended / missed / cancelled
    POST /api/referrals                   refer {patient_id, to_facility, reason, urgency}
    GET  /api/referrals                   outgoing and incoming referrals for my facility
    POST /api/referrals/{id}/status       arrived / seen / closed / cancelled
    GET  /api/referrals/{id}/letter.pdf   referral letter
"""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.facilities import facility_name, require, require_visible, visible_patient_ids
from app.report import _table

SAST = timezone(timedelta(hours=2), "SAST")       # South Africa: UTC+2 all year

router = APIRouter()

# How soon a high-risk patient should be seen, by their latest SKAI tier
BOOK_WITHIN_DAYS = {"critical": 7, "high": 14}
PRIORITY_FOR_TIER = {"critical": "urgent", "high": "soon"}
REFERRAL_DUE = {"emergency": timedelta(hours=6), "urgent": timedelta(days=2), "routine": timedelta(days=14)}


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


class Booking(BaseModel):
    patient_id: str = Field(max_length=40)
    starts_at: datetime
    reason: str = Field(min_length=1, max_length=200)
    priority: Literal["urgent", "soon", "routine"] = "routine"


class AppointmentStatus(BaseModel):
    status: Literal["attended", "missed", "cancelled"]


class Referral(BaseModel):
    patient_id: str = Field(max_length=40)
    to_facility: str = Field(max_length=40)
    reason: str = Field(min_length=1, max_length=500)
    urgency: Literal["emergency", "urgent", "routine"] = "routine"


class ReferralStatus(BaseModel):
    status: Literal["arrived", "seen", "closed", "cancelled"]
    notes: Optional[str] = Field(None, max_length=500)


# ---------- priority booking ----------

def priority_list(store, clinician):
    visible = visible_patient_ids(store, clinician)
    rows = store.conn.execute(
        """SELECT p.id, p.name, p.phone, p.facility_id, a.probability, a.tier, a.created_at AS assessed_at
           FROM patients p JOIN assessments a ON a.id = (SELECT id FROM assessments WHERE patient_id = p.id
                                                          ORDER BY created_at DESC LIMIT 1)
           WHERE a.tier IN ('high', 'critical')
             AND NOT EXISTS (SELECT 1 FROM appointments ap WHERE ap.patient_id = p.id AND ap.status = 'booked'
                             AND ap.starts_at >= ?)""", (_iso(_now()),)).fetchall()
    out = []
    for r in rows:
        if visible is not None and r["id"] not in visible:
            continue
        item = dict(r)
        item["priority"] = PRIORITY_FOR_TIER[r["tier"]]
        item["book_by"] = (_now() + timedelta(days=BOOK_WITHIN_DAYS[r["tier"]])).date().isoformat()
        out.append(item)
    out.sort(key=lambda x: -x["probability"])
    return out


@router.get("/api/bookings/priority")
def bookings_priority(request: Request, clinician=Depends(require("book"))):
    return priority_list(request.app.state.store, clinician)


@router.post("/api/appointments")
def book(body: Booking, request: Request, clinician=Depends(require("book"))):
    store = request.app.state.store
    if not store.get_profile(body.patient_id):
        raise HTTPException(404, "Patient not found")
    require_visible(store, clinician, body.patient_id)
    starts = body.starts_at if body.starts_at.tzinfo else body.starts_at.replace(tzinfo=timezone.utc)
    if starts < _now() - timedelta(minutes=5):
        raise HTTPException(422, "Appointment must be in the future")
    cur = store.conn.execute(
        """INSERT INTO appointments (patient_id, facility_id, starts_at, reason, priority, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (body.patient_id, clinician["facility_id"], _iso(starts), body.reason, body.priority, clinician["id"], _iso(_now())))
    store.conn.commit()
    store.log(clinician["id"], "book", body.patient_id)
    from app.messaging import queue_appointment_reminders   # avoids a circular import
    queue_appointment_reminders(store, cur.lastrowid)
    return {"id": cur.lastrowid}


@router.get("/api/appointments")
def appointments(request: Request, days: int = 14, clinician=Depends(require("book"))):
    store = request.app.state.store
    rows = store.conn.execute(
        """SELECT ap.*, p.name AS patient_name FROM appointments ap JOIN patients p ON p.id = ap.patient_id
           WHERE ap.facility_id = ? AND ap.starts_at BETWEEN ? AND ? ORDER BY ap.starts_at""",
        (clinician.get("facility_id"), _iso(_now() - timedelta(days=1)), _iso(_now() + timedelta(days=min(days, 90))))).fetchall()
    return [dict(r) for r in rows]


@router.post("/api/appointments/{appointment_id}/status")
def appointment_status(appointment_id: int, body: AppointmentStatus, request: Request, clinician=Depends(require("book"))):
    store = request.app.state.store
    row = store.conn.execute("SELECT facility_id FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Appointment not found")
    if row["facility_id"] != clinician.get("facility_id"):
        raise HTTPException(403, detail={"code": "not_your_facility"})
    store.conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (body.status, appointment_id))
    store.conn.commit()
    return {"ok": True}


def patient_appointments(store, patient_id):
    rows = store.conn.execute(
        """SELECT ap.id, ap.starts_at, ap.reason, ap.priority, ap.status, f.name AS facility_name
           FROM appointments ap JOIN facilities f ON f.id = ap.facility_id
           WHERE ap.patient_id = ? AND ap.status = 'booked' AND ap.starts_at >= ? ORDER BY ap.starts_at""",
        (patient_id, _iso(_now() - timedelta(hours=12)))).fetchall()
    return [dict(r) for r in rows]


# ---------- referrals ----------

def _referral_rows(store, where, params):
    rows = store.conn.execute(
        f"""SELECT r.*, p.name AS patient_name, ff.name AS from_name, tf.name AS to_name, c.name AS created_by_name
            FROM referrals r JOIN patients p ON p.id = r.patient_id
            JOIN facilities ff ON ff.id = r.from_facility JOIN facilities tf ON tf.id = r.to_facility
            JOIN clinicians c ON c.id = r.created_by WHERE {where} ORDER BY r.created_at DESC""", params).fetchall()
    now = _now()
    out = []
    for r in rows:
        item = dict(r)
        item["overdue"] = item["status"] == "sent" and datetime.fromisoformat(item["due_by"]) < now
        out.append(item)
    return out


@router.post("/api/referrals")
def refer(body: Referral, request: Request, clinician=Depends(require("refer"))):
    store = request.app.state.store
    if not store.get_profile(body.patient_id):
        raise HTTPException(404, "Patient not found")
    require_visible(store, clinician, body.patient_id)
    if not facility_name(store, body.to_facility):
        raise HTTPException(422, "Unknown facility")
    if body.to_facility == clinician.get("facility_id"):
        raise HTTPException(422, "Refer to a different facility")
    now = _now()
    cur = store.conn.execute(
        """INSERT INTO referrals (patient_id, from_facility, to_facility, reason, urgency, due_by, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (body.patient_id, clinician["facility_id"], body.to_facility, body.reason, body.urgency,
         _iso(now + REFERRAL_DUE[body.urgency]), clinician["id"], _iso(now), _iso(now)))
    store.conn.commit()
    store.log(clinician["id"], "refer", body.patient_id)
    from app.messaging import queue_referral_message
    queue_referral_message(store, cur.lastrowid)
    return {"id": cur.lastrowid}


@router.get("/api/referrals")
def referrals(request: Request, clinician=Depends(require("messages"))):
    store = request.app.state.store
    if clinician.get("role") == "district_manager":
        everything = _referral_rows(store, "1 = 1", ())
        return {"outgoing": everything, "incoming": []}
    f = clinician.get("facility_id")
    return {"outgoing": _referral_rows(store, "r.from_facility = ?", (f,)),
            "incoming": _referral_rows(store, "r.to_facility = ?", (f,))}


@router.post("/api/referrals/{referral_id}/status")
def referral_status(referral_id: int, body: ReferralStatus, request: Request, clinician=Depends(require("refer"))):
    store = request.app.state.store
    row = store.conn.execute("SELECT * FROM referrals WHERE id = ?", (referral_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Referral not found")
    f = clinician.get("facility_id")
    # The receiving facility confirms arrival/seen; the sending facility can close or cancel
    if body.status in ("arrived", "seen") and row["to_facility"] != f:
        raise HTTPException(403, detail={"code": "not_your_facility"})
    if body.status in ("closed", "cancelled") and f not in (row["from_facility"], row["to_facility"]):
        raise HTTPException(403, detail={"code": "not_your_facility"})
    store.conn.execute("UPDATE referrals SET status = ?, notes = COALESCE(?, notes), updated_at = ?, updated_by = ? WHERE id = ?",
                       (body.status, body.notes, _iso(_now()), clinician["id"], referral_id))
    store.conn.commit()
    store.log(clinician["id"], f"referral_{body.status}", row["patient_id"])
    return {"ok": True}


def patient_referrals(store, patient_id):
    return [{k: r[k] for k in ("id", "to_name", "reason", "urgency", "status", "due_by", "overdue")}
            for r in _referral_rows(store, "r.patient_id = ? AND r.status NOT IN ('closed', 'cancelled')", (patient_id,))]


def _sast(iso):
    return datetime.fromisoformat(iso).astimezone(SAST).strftime("%d %b %Y, %H:%M SAST")


@router.get("/api/referrals/{referral_id}/letter.pdf")
def referral_letter(referral_id: int, request: Request, clinician=Depends(require("messages"))):
    store = request.app.state.store
    rows = _referral_rows(store, "r.id = ?", (referral_id,))
    if not rows:
        raise HTTPException(404, "Referral not found")
    r = rows[0]
    require_visible(store, clinician, r["patient_id"])
    patient = store.get_profile(r["patient_id"])
    latest = store.conn.execute("SELECT * FROM assessments WHERE patient_id = ? ORDER BY created_at DESC LIMIT 1",
                                (r["patient_id"],)).fetchone()
    triage = store.conn.execute("SELECT * FROM triage_visits WHERE patient_id = ? ORDER BY arrived_at DESC LIMIT 1",
                                (r["patient_id"],)).fetchone()

    styles = getSampleStyleSheet()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            title=f"Referral - {patient['name']}")
    story = [
        Paragraph("<b>Referral letter</b>", styles["Title"]),
        Paragraph(f"From <b>{r['from_name']}</b> to <b>{r['to_name']}</b>", styles["Normal"]),
        Spacer(1, 4 * mm),
        _table([["Patient", "Patient ID", "Date of birth", "Urgency"],
                [patient["name"], patient["id"], patient.get("dob") or "-", r["urgency"].upper()]],
               [60 * mm, 35 * mm, 35 * mm, 44 * mm]),
        Spacer(1, 4 * mm),
        Paragraph("<b>Reason for referral</b>", styles["Heading4"]),
        Paragraph(r["reason"], styles["Normal"]),
        Spacer(1, 3 * mm),
    ]
    findings = [["Finding", "Value"]]
    if latest:
        findings.append(["SKAI cardiovascular risk", f"{latest['probability'] * 100:.1f}% ({latest['tier']}), {latest['created_at'][:10]}"])
    if triage:
        findings.append(["Last SATS triage", f"{triage['colour'].upper()} (TEWS {triage['tews']}), {triage['arrived_at'][:10]}"])
    if len(findings) > 1:
        story += [Paragraph("<b>Findings</b>", styles["Heading4"]), _table(findings, [70 * mm, 104 * mm]), Spacer(1, 4 * mm)]
    story += [
        Paragraph(f"Please see the patient by <b>{_sast(r['due_by'])}</b> and confirm arrival in SKAI "
                  "so the referring clinic knows the referral was completed.", styles["Normal"]),
        Spacer(1, 8 * mm),
        Paragraph(f"Referring clinician: <b>{r['created_by_name']}</b>, {r['from_name']}", styles["Normal"]),
        Paragraph(f"Referral #{r['id']} issued {_sast(r['created_at'])}", styles["Italic"]),
    ]
    doc.build(story)
    store.log(clinician["id"], "referral_letter", r["patient_id"])
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="Referral_{patient["id"]}_{r["id"]}.pdf"'})


def seed_care(store):
    """Demo bookings and referrals (only when there are none yet)."""
    if store.conn.execute("SELECT COUNT(*) FROM referrals").fetchone()[0]:
        return
    now = _now()
    # John has opted in to SMS reminders
    store.conn.execute("UPDATE patients SET phone = '+27820000101', sms_opt_in = 1 WHERE id = 'DEMO-101' AND phone IS NULL")
    # Thabo (critical, rising) was referred to the hospital 3 days ago and has not arrived: overdue
    store.conn.execute(
        """INSERT INTO referrals (patient_id, from_facility, to_facility, reason, urgency, status, due_by,
                                 created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'sent', ?, ?, ?, ?)""",
        ("DEMO-001", "kopanong-clinic", "lesedi-hospital",
         "Critical cardiovascular risk (SKAI 96.8%), exertional angina, ST depression. Please assess for coronary artery disease.",
         "urgent", _iso(now - timedelta(days=1)), "sfiso.kekana", _iso(now - timedelta(days=3)), _iso(now - timedelta(days=3))))
    # Nomvula was referred from Ubuntu to the hospital and has arrived
    store.conn.execute(
        """INSERT INTO referrals (patient_id, from_facility, to_facility, reason, urgency, status, due_by,
                                 created_by, created_at, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, 'arrived', ?, ?, ?, ?, ?)""",
        ("DEMO-102", "ubuntu-chc", "lesedi-hospital", "Uncontrolled hypertension (164/98) with family history; please review treatment.",
         "routine", _iso(now + timedelta(days=10)), "kamogelo", _iso(now - timedelta(days=4)), _iso(now - timedelta(days=1)), "tshiamo.bokaba"))
    # John has a follow-up booked next week
    starts = (now + timedelta(days=6)).astimezone(SAST).replace(hour=9, minute=0, second=0, microsecond=0)
    cur = store.conn.execute(
        """INSERT INTO appointments (patient_id, facility_id, starts_at, reason, priority, created_by, created_at)
           VALUES ('DEMO-101', 'kopanong-clinic', ?, 'Blood pressure and cholesterol follow-up', 'soon', 'mpho.mutengwe', ?)""",
        (_iso(starts), _iso(now)))
    store.conn.commit()
    from app.messaging import queue_appointment_reminders
    queue_appointment_reminders(store, cur.lastrowid)

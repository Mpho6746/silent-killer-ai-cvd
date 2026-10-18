"""
POPIA (Protection of Personal Information Act) support for patients.

Health information is "special personal information" under POPIA, so SKAI:
  - asks for explicit consent when a patient creates or activates an account,
    and records which version of the notice they agreed to and when;
  - lets the patient see everything held about them, including which staff
    opened their record (right of access, section 23);
  - lets the patient correct their contact details and choose whether to get
    SMS reminders (opt-in, never on by default).

    GET /api/privacy                        the current notice version
    PUT /api/patients/{id}/contact          phone, SMS opt-in, SA ID number
    GET /api/patients/{id}/export           all their data as JSON
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.care import patient_appointments, patient_referrals
from app.interop import check_sa_id_against
from app.messaging import normalise_phone
from app.patient_auth import CONSENT_VERSION, require_patient

router = APIRouter()

@router.get("/api/privacy")
def privacy():
    return {"consent_version": CONSENT_VERSION, "regulator": "Information Regulator (South Africa)",
            "law": "Protection of Personal Information Act 4 of 2013"}


class Contact(BaseModel):
    phone: Optional[str] = Field(None, max_length=20)
    sms_opt_in: bool = False
    sa_id: Optional[str] = Field(None, max_length=20)


@router.put("/api/patients/{patient_id}/contact")
def update_contact(patient_id: str, body: Contact, request: Request, _owner=Depends(require_patient)):
    store = request.app.state.store
    profile = store.get_profile(patient_id)
    phone = None
    if body.phone and body.phone.strip():
        phone = normalise_phone(body.phone)
        if not phone:
            raise HTTPException(422, detail={"code": "phone_invalid"})
    if body.sms_opt_in and not phone:
        raise HTTPException(422, detail={"code": "phone_needed"})
    sa_id = None
    if body.sa_id and body.sa_id.strip():
        parsed, error = check_sa_id_against(body.sa_id, profile)
        if error:
            raise HTTPException(422, detail={"code": error})
        sa_id = parsed["number"]
    store.conn.execute("UPDATE patients SET phone = ?, sms_opt_in = ?, sa_id = ? WHERE id = ?",
                       (phone, int(body.sms_opt_in), sa_id, patient_id))
    store.conn.commit()
    return {k: store.get_profile(patient_id)[k] for k in ("phone", "sms_opt_in", "sa_id")}


def _rows(store, sql, params):
    return [dict(r) for r in store.conn.execute(sql, params).fetchall()]


@router.get("/api/patients/{patient_id}/export")
def export(patient_id: str, request: Request, _owner=Depends(require_patient)):
    store = request.app.state.store
    data = {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notice": "Everything SKAI holds about you (POPIA section 23). Keep this file private.",
        "profile": store.get_profile(patient_id),
        "assessments": store.get_patient(patient_id)["assessments"],
        "documents": store.list_documents(patient_id),
        "medications": _rows(store, "SELECT * FROM medications WHERE patient_id = ?", (patient_id,)),
        "medication_log": _rows(store, "SELECT m.name, l.day, l.slot, l.taken_at FROM med_logs l JOIN medications m ON m.id = l.medication_id WHERE m.patient_id = ?", (patient_id,)),
        "checkins": _rows(store, "SELECT * FROM checkins WHERE patient_id = ?", (patient_id,)),
        "appointments": patient_appointments(store, patient_id),
        "referrals": patient_referrals(store, patient_id),
        "messages": _rows(store, "SELECT kind, channel, to_number, body, scheduled_for, status, sent_at FROM messages WHERE patient_id = ?", (patient_id,)),
        "outcome": _rows(store, "SELECT has_cvd, source, recorded_at FROM outcomes WHERE patient_id = ?", (patient_id,)),
        "who_opened_my_record": _rows(store, """SELECT c.name AS staff, c.role, a.action, a.at FROM audit_log a
                                                JOIN clinicians c ON c.id = a.clinician_id WHERE a.target = ? ORDER BY a.at DESC""", (patient_id,)),
    }
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="SKAI_my_data_{patient_id}.json"'})

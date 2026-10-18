"""
Patient accounts for the SKAI Health app.

Two ways to get an account:
  1. Self-registration: a new patient creates a profile and chooses a password.
  2. Clinic registration: a patient whose record a clinic created (no password
     yet) gets a one-time activation code from a signed-in clinician, valid
     for 24 hours and 5 tries, and uses it to set their own password.

A patient session only opens that patient's own record. Passwords, session
tokens and activation codes are stored as hashes only (see app/auth.py).

    POST /api/patient-auth/register     {patient_id, name, dob, sex, password}
    POST /api/patient-auth/login        {patient_id, password}
    POST /api/patient-auth/activate     {patient_id, code, password}
    POST /api/patient-auth/logout
    GET  /api/patient-auth/me
    POST /api/patients/{id}/activation-code   (signed-in clinician) -> {code, expires_at}
"""
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.auth import LOCK_MINUTES, MAX_ATTEMPTS, SESSION_HOURS, hash_password, token_hash, verify_password
from app.facilities import require, require_visible

router = APIRouter()

# Demo patient app accounts. Not shown anywhere in the app: share them with the team directly.
DEMO_PATIENT_PASSWORDS = {"DEMO-101": "JohnDoe2026", "DEMO-102": "Nomvula2026"}
DEMO_PATIENTS = list(DEMO_PATIENT_PASSWORDS)
DEMO_PATIENT_PASSWORD = DEMO_PATIENT_PASSWORDS["DEMO-101"]
OLD_DEMO_PATIENT_PASSWORD = "SkaiPatient2026!"     # replaced in databases created before October 18
CODE_HOURS = 24
CODE_TRIES = 5
ID_PATTERN = r"^[A-Za-z0-9_-]{1,40}$"


def _now():
    return datetime.now(timezone.utc)


def _age(dob):
    t = date.today()
    return t.year - dob.year - ((t.month, t.day) < (dob.month, dob.day))


def seed_patient_accounts(store):
    """Gives the demo health-tracker patients a password (only if they don't have one yet)."""
    for pid in DEMO_PATIENTS:
        creds = store.get_patient_credentials(pid)
        if creds and (not creds["password_hash"] or verify_password(OLD_DEMO_PATIENT_PASSWORD, creds["password_hash"])):
            store.update_patient_credentials(pid, password_hash=hash_password(DEMO_PATIENT_PASSWORDS[pid]),
                                             failed_attempts=0, locked_until=None)
        if creds and not (store.get_profile(pid) or {}).get("consent_at"):
            record_consent(store, pid)          # demo accounts count as having agreed to the notice


CONSENT_VERSION = "2026-10"     # version of the POPIA privacy notice (see app/privacy.py)


def record_consent(store, patient_id):
    store.conn.execute("UPDATE patients SET consent_at = ?, consent_version = ? WHERE id = ?",
                       (_now().isoformat(timespec="seconds"), CONSENT_VERSION, patient_id))
    store.conn.commit()


# ---------- dependencies ----------

def _bearer(authorization):
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def session_patient_id(request: Request, authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if not token:
        return None
    return request.app.state.store.session_patient(token_hash(token), _now().isoformat(timespec="seconds"))


def require_patient(patient_id: str, signed_in=Depends(session_patient_id)):
    """The caller must be signed in as exactly this patient."""
    if not signed_in:
        raise HTTPException(401, detail={"code": "patient_not_signed_in"})
    if signed_in != patient_id:
        raise HTTPException(403, detail={"code": "not_your_record"})
    return patient_id


# ---------- request bodies ----------

class Register(BaseModel):
    patient_id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    dob: date
    sex: Optional[Literal["M", "F"]] = None
    password: str = Field(min_length=8, max_length=200)
    facility_id: Optional[str] = Field(None, max_length=40)   # the clinic the patient attends
    consent: bool = False                                     # POPIA: agreed to the privacy notice

    @field_validator("dob")
    @classmethod
    def adult(cls, v):
        if not 18 <= _age(v) <= 100:
            raise ValueError("SKAI's models are for adults aged 18-100")
        return v


class Login(BaseModel):
    patient_id: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class Activate(BaseModel):
    patient_id: str = Field(min_length=1, max_length=40)
    code: str = Field(pattern=r"^\d{6}$")
    password: str = Field(min_length=8, max_length=200)
    consent: bool = False


def _session(store, patient_id):
    token = secrets.token_urlsafe(32)
    store.create_patient_session(token_hash(token), patient_id,
                                 (_now() + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds"))
    return {"token": token, "profile": store.get_profile(patient_id)}


# ---------- endpoints ----------

@router.post("/api/patient-auth/register")
def register(body: Register, request: Request):
    store = request.app.state.store
    if not body.consent:
        raise HTTPException(422, detail={"code": "consent_required"})
    if store.get_profile(body.patient_id):
        raise HTTPException(409, detail={"code": "id_taken"})
    store.upsert_patient(body.patient_id, body.name, body.sex, dob=body.dob.isoformat())
    store.update_patient_credentials(body.patient_id, password_hash=hash_password(body.password))
    facility = store.conn.execute("SELECT id FROM facilities WHERE id = ?", (body.facility_id,)).fetchone() if body.facility_id else None
    if not facility:   # unknown or missing: the first clinic
        facility = store.conn.execute("SELECT id FROM facilities WHERE kind = 'clinic' ORDER BY name LIMIT 1").fetchone()
    if facility:
        store.conn.execute("UPDATE patients SET facility_id = ? WHERE id = ?", (facility[0], body.patient_id))
        store.conn.commit()
    record_consent(store, body.patient_id)
    return _session(store, body.patient_id)


@router.post("/api/patient-auth/login")
def login(body: Login, request: Request):
    store = request.app.state.store
    creds = store.get_patient_credentials(store.resolve_patient_id(body.patient_id))
    now = _now()
    if creds and creds["locked_until"] and datetime.fromisoformat(creds["locked_until"]) > now:
        raise HTTPException(429, detail={"code": "locked"})
    if not creds or not creds["password_hash"] or not verify_password(body.password, creds["password_hash"]):
        if creds and creds["password_hash"]:
            attempts = (creds["failed_attempts"] or 0) + 1
            locked = (now + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds") if attempts >= MAX_ATTEMPTS else None
            store.update_patient_credentials(creds["id"], failed_attempts=0 if locked else attempts, locked_until=locked)
        raise HTTPException(401, detail={"code": "login_failed"})
    store.update_patient_credentials(creds["id"], failed_attempts=0, locked_until=None)
    return _session(store, creds["id"])


@router.post("/api/patient-auth/activate")
def activate(body: Activate, request: Request):
    store = request.app.state.store
    if not body.consent:
        raise HTTPException(422, detail={"code": "consent_required"})
    pid = store.resolve_patient_id(body.patient_id)
    code = store.get_activation_code(pid)
    valid = (code and code["attempts"] < CODE_TRIES
             and datetime.fromisoformat(code["expires_at"]) > _now()
             and secrets.compare_digest(code["code_hash"], token_hash(f"{pid}:{body.code}")))
    if not valid:
        if code:
            store.bump_activation_attempts(pid)
        raise HTTPException(401, detail={"code": "code_invalid"})
    store.update_patient_credentials(pid, password_hash=hash_password(body.password), failed_attempts=0, locked_until=None)
    store.delete_activation_code(pid)
    store.delete_patient_sessions(pid)          # any older sessions end when the password is (re)set
    record_consent(store, pid)
    return _session(store, pid)


@router.post("/api/patient-auth/logout")
def logout(request: Request, authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if token:
        request.app.state.store.delete_patient_session(token_hash(token))
    return {"ok": True}


@router.get("/api/patient-auth/me")
def me(request: Request, patient_id=Depends(session_patient_id)):
    if not patient_id:
        raise HTTPException(401, detail={"code": "patient_not_signed_in"})
    return request.app.state.store.get_profile(patient_id)


@router.post("/api/patients/{patient_id}/activation-code")
def issue_code(patient_id: str, request: Request, clinician=Depends(require("activation_code"))):
    """A clinician gives a clinic-registered patient a one-time code to set up their app account."""
    store = request.app.state.store
    if not store.get_profile(patient_id):
        raise HTTPException(404, "Patient not found")
    require_visible(store, clinician, patient_id)
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = (_now() + timedelta(hours=CODE_HOURS)).isoformat(timespec="seconds")
    # Hashed with the patient ID so the same 6 digits can't be reused across patients
    store.set_activation_code(patient_id, token_hash(f"{patient_id}:{code}"), clinician["id"], expires)
    store.log(clinician["id"], "activation_code", patient_id)
    return {"code": code, "expires_at": expires}

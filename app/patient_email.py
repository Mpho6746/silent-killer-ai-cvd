"""
Email for patient accounts: confirmation codes, welcome messages with the
patient ID, and password resets.

    POST /api/patient-auth/verify-email   {identifier, code}      confirm the address -> session
    POST /api/patient-auth/resend-code    {identifier}            new confirmation code
    POST /api/patient-auth/forgot         {identifier}            emails a reset code
    POST /api/patient-auth/reset          {identifier, code, password}
    GET  /api/demo/inbox?address=...      demo mode only: the emails SKAI "sent" to that address

"identifier" is the patient ID or the email address. Codes are 6 digits, valid
for 30 minutes and 5 tries, and stored only as hashes. Forgot-password always
answers the same way, so it can't be used to find out who has an account.

Like SMS (app/messaging.py), sending is pluggable. SKAI_EMAIL_PROVIDER=demo
(the default) records each email in the outbox without sending it, and the
patient app offers a clearly labelled demo inbox. A real deployment plugs in
an email service (SMTP or an API) and turns the demo inbox off.
"""
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import hash_password, token_hash

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
CODE_MINUTES = 30
CODE_TRIES = 5


def _now():
    return datetime.now(timezone.utc)


def provider():
    return os.environ.get("SKAI_EMAIL_PROVIDER", "demo")


def demo_inbox_enabled():
    return provider() == "demo" and os.environ.get("SKAI_DEMO", "1") == "1"


def normalise_email(raw):
    email = (raw or "").strip()
    return email if EMAIL_RE.match(email) and len(email) <= 120 else None


def find_by_email(store, email):
    row = store.conn.execute("SELECT id FROM patients WHERE lower(email) = lower(?)", (email,)).fetchone()
    return row[0] if row else None


def resolve(store, identifier):
    """Patient ID for an email address or a (forgivingly typed) patient ID, or None."""
    identifier = (identifier or "").strip()
    if "@" in identifier:
        return find_by_email(store, identifier)
    pid = store.resolve_patient_id(identifier)
    return pid if store.get_profile(pid) else None


def send(store, patient_id, to, kind, subject, body):
    # The demo provider only records the email; a real provider would send it here.
    store.conn.execute("INSERT INTO emails (patient_id, to_address, kind, subject, body, status, provider, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (patient_id, to, kind, subject, body, "sent", provider(), _now().isoformat(timespec="seconds")))
    store.conn.commit()


def _new_code(store, patient_id, purpose):
    code = f"{secrets.randbelow(1_000_000):06d}"
    store.conn.execute(
        """INSERT INTO email_codes (patient_id, purpose, code_hash, expires_at, attempts) VALUES (?, ?, ?, ?, 0)
           ON CONFLICT(patient_id, purpose) DO UPDATE SET code_hash = excluded.code_hash,
               expires_at = excluded.expires_at, attempts = 0""",
        (patient_id, purpose, token_hash(f"{patient_id}:{purpose}:{code}"),
         (_now() + timedelta(minutes=CODE_MINUTES)).isoformat(timespec="seconds")))
    store.conn.commit()
    return code


def _check_code(store, patient_id, purpose, code):
    row = store.conn.execute("SELECT * FROM email_codes WHERE patient_id = ? AND purpose = ?", (patient_id, purpose)).fetchone()
    ok = (row and row["attempts"] < CODE_TRIES and datetime.fromisoformat(row["expires_at"]) > _now()
          and secrets.compare_digest(row["code_hash"], token_hash(f"{patient_id}:{purpose}:{code}")))
    if ok:
        store.conn.execute("DELETE FROM email_codes WHERE patient_id = ? AND purpose = ?", (patient_id, purpose))
    elif row:
        store.conn.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE patient_id = ? AND purpose = ?", (patient_id, purpose))
    store.conn.commit()
    if not ok:
        raise HTTPException(401, detail={"code": "code_invalid"})


def new_patient_id(store):
    """A short, unique ID such as SKAI-482917 that patients can read out at the clinic."""
    while True:
        pid = f"SKAI-{secrets.randbelow(900_000) + 100_000}"
        if not store.get_profile(pid):
            return pid


def send_verification(store, patient_id):
    profile = store.get_profile(patient_id)
    code = _new_code(store, patient_id, "verify")
    first = profile["name"].split()[0]
    send(store, patient_id, profile["email"], "verify", "Confirm your SKAI Health account",
         f"Hi {first},\n\nWelcome to SKAI Health. Your confirmation code is {code} (valid for {CODE_MINUTES} minutes).\n\n"
         f"Your patient ID is {patient_id}. You can sign in with it or with this email address. "
         "Show it at your clinic so staff can find your record.\n\nIf you did not create this account, ignore this email.")


class Identify(BaseModel):
    identifier: str = Field(min_length=1, max_length=120)


class VerifyCode(Identify):
    code: str = Field(pattern=r"^\d{6}$")


class Reset(VerifyCode):
    password: str = Field(min_length=8, max_length=200)


@router.post("/api/patient-auth/verify-email")
def verify_email(body: VerifyCode, request: Request):
    from app.patient_auth import _session
    store = request.app.state.store
    pid = resolve(store, body.identifier)
    if not pid:
        raise HTTPException(401, detail={"code": "code_invalid"})
    _check_code(store, pid, "verify", body.code)
    store.conn.execute("UPDATE patients SET email_verified_at = ? WHERE id = ?", (_now().isoformat(timespec="seconds"), pid))
    store.conn.commit()
    return _session(store, pid)


@router.post("/api/patient-auth/resend-code")
def resend_code(body: Identify, request: Request):
    store = request.app.state.store
    pid = resolve(store, body.identifier)
    profile = store.get_profile(pid) if pid else None
    if profile and profile.get("email") and not profile.get("email_verified_at"):
        send_verification(store, pid)
    return {"ok": True}


@router.post("/api/patient-auth/forgot")
def forgot(body: Identify, request: Request):
    store = request.app.state.store
    pid = resolve(store, body.identifier)
    profile = store.get_profile(pid) if pid else None
    if profile and profile.get("email"):
        code = _new_code(store, pid, "reset")
        send(store, pid, profile["email"], "reset", "Reset your SKAI Health password",
             f"Your SKAI Health password reset code is {code} (valid for {CODE_MINUTES} minutes).\n\n"
             f"Patient ID: {pid}.\n\nIf you did not ask for this, you can ignore this email; your password has not changed.")
    # Same answer either way, so nobody can test which IDs or emails exist
    return {"ok": True}


@router.post("/api/patient-auth/reset")
def reset(body: Reset, request: Request):
    from app.patient_auth import _session
    store = request.app.state.store
    pid = resolve(store, body.identifier)
    if not pid:
        raise HTTPException(401, detail={"code": "code_invalid"})
    _check_code(store, pid, "reset", body.code)
    store.update_patient_credentials(pid, password_hash=hash_password(body.password), failed_attempts=0, locked_until=None)
    store.delete_patient_sessions(pid)          # sign out everywhere else
    # The reset code arrived by email, so the address is proven
    store.conn.execute("UPDATE patients SET email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?",
                       (_now().isoformat(timespec="seconds"), pid))
    store.conn.commit()
    return _session(store, pid)


@router.get("/api/demo/inbox")
def demo_inbox(identifier: str, request: Request):
    """Demo mode only: the latest emails for a patient ID or email address."""
    if not demo_inbox_enabled():
        raise HTTPException(404, "Not available")
    store = request.app.state.store
    pid = resolve(store, identifier)
    address = (store.get_profile(pid) or {}).get("email") if pid else identifier.strip()
    rows = store.conn.execute(
        "SELECT subject, body, created_at FROM emails WHERE lower(to_address) = lower(?) ORDER BY id DESC LIMIT 3", (address or "",)).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/config")
def config():
    return {"demo_inbox": demo_inbox_enabled()}

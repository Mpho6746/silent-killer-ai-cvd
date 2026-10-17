"""
Clinician accounts: sign-in, sessions and the audit trail.

Security choices
- Passwords are stored only as salted PBKDF2-SHA256 hashes (200 000 rounds)
  and compared in constant time.
- A sign-in returns a random session token. The database stores only its
  SHA-256 hash, so a copy of the database cannot be used to sign in.
- Sessions last 8 hours. After 5 wrong passwords an account is locked for
  5 minutes.
- Every clinical action (sign-in, opening a patient, saving an assessment,
  reviewing a document) is written to the audit log under the clinician's name.

Endpoints
    POST /api/auth/login            {username, password} -> {token, clinician}
    POST /api/auth/logout
    GET  /api/auth/me
    PUT  /api/clinicians/me/photo   {data_url}
    PUT  /api/clinicians/me/password {current, new}
    GET  /api/audit                 recent activity of all clinicians
"""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()

ITERATIONS = 200_000
SESSION_HOURS = 8
MAX_ATTEMPTS = 5
LOCK_MINUTES = 5

# Demo accounts (all share DEMO_PASSWORD; real deployments would issue
# individual passwords and force a change on first sign-in)
DEMO_PASSWORD = "SkaiDemo2026!"
DEMO_CLINICIANS = [
    ("samuel.olajide", "Dr Samuel Olajide", "Cardiologist"),
    ("sfiso.kekana", "Dr Sfiso Kekana", "General Practitioner"),
    ("mpho.mutengwe", "Dr Mpho Mutengwe", "Physician"),
    ("tshiamo.bokaba", "Dr Tshiamo Bokaba", "Cardiologist"),
    ("kamogelo", "Dr Kamogelo", "Clinic Doctor"),
    ("neontle", "Dr Neontle", "Clinic Doctor"),
]


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password, stored):
    try:
        _, rounds, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(rounds))
        return hmac.compare_digest(check, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def seed_clinicians(store):
    """Creates the demo accounts once (on a fresh database or one from before accounts existed)."""
    if store.has_clinicians():
        return
    for username, name, title in DEMO_CLINICIANS:
        store.add_clinician(username, name, title, hash_password(DEMO_PASSWORD))


def public(clinician):
    """What the app may see about a clinician (never the password hash)."""
    return {k: clinician.get(k) for k in ("id", "name", "title", "photo")}


# ---------- dependencies ----------

def _bearer(authorization):
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def optional_clinician(request: Request, authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
    if not token:
        return None
    return request.app.state.store.session_clinician(token_hash(token), _now().isoformat(timespec="seconds"))


def require_clinician(clinician=Depends(optional_clinician)):
    if not clinician:
        raise HTTPException(401, detail={"code": "not_signed_in"})
    return clinician


# ---------- endpoints ----------

class Login(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


class Photo(BaseModel):
    data_url: str = Field(max_length=400_000, pattern=r"^data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$")


class PasswordChange(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=8, max_length=200)


@router.post("/api/auth/login")
def login(body: Login, request: Request):
    store = request.app.state.store
    username = body.username.strip().lower()
    clinician = store.get_clinician(username)
    now = _now()
    if clinician and clinician["locked_until"] and datetime.fromisoformat(clinician["locked_until"]) > now:
        raise HTTPException(429, detail={"code": "locked"})
    if not clinician or not verify_password(body.password, clinician["password_hash"]):
        if clinician:
            attempts = clinician["failed_attempts"] + 1
            locked = (now + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds") if attempts >= MAX_ATTEMPTS else None
            store.update_clinician(username, failed_attempts=0 if locked else attempts, locked_until=locked)
        # Same answer whether the username exists or not, so accounts can't be guessed
        raise HTTPException(401, detail={"code": "login_failed"})

    store.update_clinician(username, failed_attempts=0, locked_until=None)
    token = secrets.token_urlsafe(32)
    store.create_session(token_hash(token), username, (now + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds"))
    store.log(username, "login")
    return {"token": token, "clinician": public(clinician), "expires_in_hours": SESSION_HOURS}


@router.post("/api/auth/logout")
def logout(request: Request, clinician=Depends(require_clinician), authorization: str | None = Header(default=None)):
    request.app.state.store.delete_session(token_hash(_bearer(authorization)))
    request.app.state.store.log(clinician["id"], "logout")
    return {"ok": True}


@router.get("/api/auth/me")
def me(clinician=Depends(require_clinician)):
    return public(clinician)


@router.put("/api/clinicians/me/photo")
def set_photo(body: Photo, request: Request, clinician=Depends(require_clinician)):
    request.app.state.store.update_clinician(clinician["id"], photo=body.data_url)
    request.app.state.store.log(clinician["id"], "photo")
    return {"ok": True}


@router.put("/api/clinicians/me/password")
def set_password(body: PasswordChange, request: Request, clinician=Depends(require_clinician),
                 authorization: str | None = Header(default=None)):
    if not verify_password(body.current, clinician["password_hash"]):
        raise HTTPException(403, detail={"code": "pw_wrong"})
    store = request.app.state.store
    store.update_clinician(clinician["id"], password_hash=hash_password(body.new))
    # Sign out every other session for this account
    store.delete_sessions_for(clinician["id"], keep=token_hash(_bearer(authorization)))
    store.log(clinician["id"], "password")
    return {"ok": True}


@router.get("/api/audit")
def audit(request: Request, limit: int = 50, clinician=Depends(require_clinician)):
    return request.app.state.store.recent_activity(min(max(limit, 1), 200))

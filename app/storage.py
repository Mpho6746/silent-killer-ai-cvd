"""
Local SQLite store (no cloud account needed) for patients, their risk
assessments, uploaded clinical documents, medication plans and daily check-ins.

The triage queue is built here too: each patient's latest assessment, the change
since their previous one, and proactive flags a clinician should see first.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from src.preprocessing import ROOT

DEFAULT_DB = ROOT / "data" / "app" / "skai.db"

# A jump of 10 percentage points or more between two assessments is flagged
RISING_THRESHOLD = 0.10
# High/critical patients not re-assessed within this many days are flagged
FOLLOW_UP_DAYS = 30
# Documents whose values may be used for a risk score (best first)
USABLE_STATUSES = ("verified", "approved", "pending_review")

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    sex         TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assessments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  TEXT NOT NULL REFERENCES patients(id),
    kind        TEXT NOT NULL CHECK (kind IN ('clinical', 'lifestyle')),
    inputs      TEXT NOT NULL,
    probability REAL NOT NULL,
    tier        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assessments_patient ON assessments(patient_id, created_at);
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL REFERENCES patients(id),
    filename      TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    content       BLOB NOT NULL,
    status        TEXT NOT NULL,              -- verified | approved | pending_review | rejected
    issuer        TEXT,
    document_date TEXT,
    vitals        TEXT NOT NULL,
    warnings      TEXT NOT NULL,
    reasons       TEXT NOT NULL,
    uploaded_at   TEXT NOT NULL,
    reviewed_at   TEXT,
    UNIQUE (patient_id, sha256)
);
CREATE TABLE IF NOT EXISTS medications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  TEXT NOT NULL REFERENCES patients(id),
    name        TEXT NOT NULL,
    dose        TEXT,
    times       TEXT NOT NULL,                -- JSON list of "HH:MM"
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS med_logs (
    medication_id INTEGER NOT NULL REFERENCES medications(id),
    day           TEXT NOT NULL,
    slot          TEXT NOT NULL,
    taken_at      TEXT NOT NULL,
    PRIMARY KEY (medication_id, day, slot)
);
CREATE TABLE IF NOT EXISTS checkins (
    patient_id       TEXT NOT NULL REFERENCES patients(id),
    day              TEXT NOT NULL,
    mood             INTEGER,
    activity_minutes INTEGER,
    water_glasses    INTEGER,
    symptoms         TEXT NOT NULL,           -- JSON list
    created_at       TEXT NOT NULL,
    PRIMARY KEY (patient_id, day)
);
CREATE TABLE IF NOT EXISTS clinicians (
    id              TEXT PRIMARY KEY,         -- username, e.g. samuel.olajide
    name            TEXT NOT NULL,
    title           TEXT,                     -- e.g. Cardiologist
    password_hash   TEXT NOT NULL,            -- pbkdf2_sha256$iterations$salt$hash, never the password
    photo           TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash    TEXT PRIMARY KEY,           -- SHA-256 of the token; the token itself is never stored
    clinician_id  TEXT NOT NULL REFERENCES clinicians(id),
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS patient_sessions (
    token_hash    TEXT PRIMARY KEY,           -- SHA-256 of the token; the token itself is never stored
    patient_id    TEXT NOT NULL REFERENCES patients(id),
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activation_codes (
    patient_id    TEXT PRIMARY KEY REFERENCES patients(id),   -- one live code per patient
    code_hash     TEXT NOT NULL,
    created_by    TEXT NOT NULL REFERENCES clinicians(id),
    expires_at    TEXT NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS facilities (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('clinic', 'hospital')),
    district    TEXT NOT NULL
);
-- Arrival triage using the South African Triage Scale (see app/triage.py)
CREATE TABLE IF NOT EXISTS triage_visits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    facility_id     TEXT NOT NULL REFERENCES facilities(id),
    patient_id      TEXT REFERENCES patients(id),      -- NULL for an unregistered walk-in
    name            TEXT NOT NULL,
    arrived_at      TEXT NOT NULL,
    vitals          TEXT NOT NULL,                     -- JSON: TEWS inputs
    discriminators  TEXT NOT NULL,                     -- JSON list
    tews            INTEGER NOT NULL,
    colour          TEXT NOT NULL CHECK (colour IN ('red', 'orange', 'yellow', 'green')),
    cardiac_flag    INTEGER NOT NULL DEFAULT 0,        -- SKAI "silent risk" flag within the colour
    status          TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'in_consult', 'done', 'left')),
    seen_at         TEXT,
    created_by      TEXT NOT NULL REFERENCES clinicians(id)
);
CREATE TABLE IF NOT EXISTS appointments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  TEXT NOT NULL REFERENCES patients(id),
    facility_id TEXT NOT NULL REFERENCES facilities(id),
    starts_at   TEXT NOT NULL,
    reason      TEXT NOT NULL,
    priority    TEXT NOT NULL CHECK (priority IN ('urgent', 'soon', 'routine')),
    status      TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'attended', 'missed', 'cancelled')),
    created_by  TEXT NOT NULL REFERENCES clinicians(id),
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS referrals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      TEXT NOT NULL REFERENCES patients(id),
    from_facility   TEXT NOT NULL REFERENCES facilities(id),
    to_facility     TEXT NOT NULL REFERENCES facilities(id),
    reason          TEXT NOT NULL,
    urgency         TEXT NOT NULL CHECK (urgency IN ('emergency', 'urgent', 'routine')),
    status          TEXT NOT NULL DEFAULT 'sent' CHECK (status IN ('sent', 'arrived', 'seen', 'closed', 'cancelled')),
    due_by          TEXT NOT NULL,
    notes           TEXT,
    created_by      TEXT NOT NULL REFERENCES clinicians(id),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    updated_by      TEXT REFERENCES clinicians(id)
);
-- Outgoing SMS / WhatsApp / USSD messages (see app/messaging.py)
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL REFERENCES patients(id),
    channel       TEXT NOT NULL CHECK (channel IN ('sms', 'whatsapp')),
    to_number     TEXT NOT NULL,
    kind          TEXT NOT NULL,
    body          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'failed', 'skipped')),
    scheduled_for TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    sent_at       TEXT,
    provider_ref  TEXT,
    UNIQUE (patient_id, kind, scheduled_for)
);
-- Confirmed outcomes for the pilot study (see app/monitoring.py)
CREATE TABLE IF NOT EXISTS outcomes (
    patient_id   TEXT PRIMARY KEY REFERENCES patients(id),
    has_cvd      INTEGER NOT NULL,                     -- confirmed by a clinician
    source       TEXT NOT NULL,                        -- e.g. echo, angiogram, clinical diagnosis
    recorded_by  TEXT NOT NULL REFERENCES clinicians(id),
    recorded_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    clinician_id  TEXT NOT NULL REFERENCES clinicians(id),
    action        TEXT NOT NULL,
    target        TEXT,
    at            TEXT NOT NULL
);
"""

# Columns added after the first release; created on older databases at startup
MIGRATIONS = {
    "patients": {"dob": "TEXT", "photo": "TEXT",
                 "password_hash": "TEXT", "failed_attempts": "INTEGER NOT NULL DEFAULT 0", "locked_until": "TEXT",
                 "facility_id": "TEXT", "phone": "TEXT", "sa_id": "TEXT", "sms_opt_in": "INTEGER NOT NULL DEFAULT 0",
                 "consent_at": "TEXT", "consent_version": "TEXT"},
    "clinicians": {"role": "TEXT NOT NULL DEFAULT 'doctor'", "facility_id": "TEXT"},
    "assessments": {"created_by": "TEXT"},     # clinician who saved it (NULL = patient self-check)
    "documents": {"reviewed_by": "TEXT"},       # clinician who approved/rejected it
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today():
    return date.today().isoformat()


class Store:
    def __init__(self, path=DEFAULT_DB):
        self.path = path
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        for table, columns in MIGRATIONS.items():
            existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for column, kind in columns.items():
                if column not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        self.conn.commit()

    def is_empty(self):
        return self.conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 0

    # ---------- patients ----------
    def upsert_patient(self, patient_id, name, sex=None, created_at=None, dob=None):
        self.conn.execute(
            """INSERT INTO patients (id, name, sex, dob, created_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name = excluded.name,
                   sex = COALESCE(excluded.sex, patients.sex), dob = COALESCE(excluded.dob, patients.dob)""",
            (patient_id, name, sex, dob, created_at or _now()))
        self.conn.commit()

    def set_photo(self, patient_id, data_url):
        self.conn.execute("UPDATE patients SET photo = ? WHERE id = ?", (data_url, patient_id))
        self.conn.commit()

    # Sign-in fields never leave the store through profile lookups
    PRIVATE = ("password_hash", "failed_attempts", "locked_until")

    def get_profile(self, patient_id):
        row = self.conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
        if not row:
            return None
        profile = {k: v for k, v in dict(row).items() if k not in self.PRIVATE}
        profile["has_account"] = row["password_hash"] is not None
        return profile

    def resolve_patient_id(self, typed):
        """
        The stored patient ID for what someone typed at sign-in. Exact match first;
        otherwise ignore capitals, spaces and dashes ("demo 101" -> "DEMO-101"),
        but only if that points to exactly one patient.
        """
        typed = (typed or "").strip()
        if self.conn.execute("SELECT 1 FROM patients WHERE id = ?", (typed,)).fetchone():
            return typed
        key = typed.upper().replace(" ", "").replace("-", "")
        if not key:
            return typed
        rows = self.conn.execute(
            "SELECT id FROM patients WHERE REPLACE(REPLACE(UPPER(id), ' ', ''), '-', '') = ?", (key,)).fetchall()
        return rows[0][0] if len(rows) == 1 else typed

    def get_patient_credentials(self, patient_id):
        row = self.conn.execute("SELECT id, name, password_hash, failed_attempts, locked_until FROM patients WHERE id = ?",
                                (patient_id,)).fetchone()
        return dict(row) if row else None

    def update_patient_credentials(self, patient_id, **fields):
        assert set(fields) <= {"password_hash", "failed_attempts", "locked_until"}
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE patients SET {sets} WHERE id = ?", (*fields.values(), patient_id))
        self.conn.commit()

    def create_patient_session(self, token_hash, patient_id, expires_at):
        self.conn.execute("INSERT INTO patient_sessions (token_hash, patient_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                          (token_hash, patient_id, _now(), expires_at))
        self.conn.commit()

    def session_patient(self, token_hash, now_iso):
        row = self.conn.execute("SELECT patient_id FROM patient_sessions WHERE token_hash = ? AND expires_at > ?",
                                (token_hash, now_iso)).fetchone()
        return row["patient_id"] if row else None

    def delete_patient_session(self, token_hash):
        self.conn.execute("DELETE FROM patient_sessions WHERE token_hash = ?", (token_hash,))
        self.conn.commit()

    def delete_patient_sessions(self, patient_id):
        self.conn.execute("DELETE FROM patient_sessions WHERE patient_id = ?", (patient_id,))
        self.conn.commit()

    def set_activation_code(self, patient_id, code_hash, created_by, expires_at):
        self.conn.execute("""INSERT INTO activation_codes (patient_id, code_hash, created_by, expires_at, attempts)
                             VALUES (?, ?, ?, ?, 0)
                             ON CONFLICT(patient_id) DO UPDATE SET code_hash = excluded.code_hash,
                                 created_by = excluded.created_by, expires_at = excluded.expires_at, attempts = 0""",
                          (patient_id, code_hash, created_by, expires_at))
        self.conn.commit()

    def get_activation_code(self, patient_id):
        row = self.conn.execute("SELECT * FROM activation_codes WHERE patient_id = ?", (patient_id,)).fetchone()
        return dict(row) if row else None

    def bump_activation_attempts(self, patient_id):
        self.conn.execute("UPDATE activation_codes SET attempts = attempts + 1 WHERE patient_id = ?", (patient_id,))
        self.conn.commit()

    def delete_activation_code(self, patient_id):
        self.conn.execute("DELETE FROM activation_codes WHERE patient_id = ?", (patient_id,))
        self.conn.commit()

    def get_patient(self, patient_id):
        profile = self.get_profile(patient_id)
        if not profile:
            return None
        history = self.conn.execute(
            "SELECT * FROM assessments WHERE patient_id = ? ORDER BY created_at", (patient_id,)).fetchall()
        assessments = [self._assessment(a) for a in history]
        for a in assessments:
            a["created_by_name"] = self.clinician_name(a.get("created_by"))
        return {**profile, "assessments": assessments,
                "documents": self.list_documents(patient_id)}

    # ---------- assessments ----------
    def add_assessment(self, patient_id, kind, inputs, probability, tier, created_at=None, created_by=None):
        cur = self.conn.execute(
            """INSERT INTO assessments (patient_id, kind, inputs, probability, tier, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (patient_id, kind, json.dumps(inputs), probability, tier, created_at or _now(), created_by))
        self.conn.commit()
        return cur.lastrowid

    def get_assessment(self, assessment_id):
        row = self.conn.execute("SELECT * FROM assessments WHERE id = ?", (assessment_id,)).fetchone()
        return self._assessment(row) if row else None

    def latest_assessment(self, patient_id, kind):
        row = self.conn.execute("SELECT * FROM assessments WHERE patient_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
                                (patient_id, kind)).fetchone()
        return self._assessment(row) if row else None

    # ---------- documents ----------
    def add_document(self, patient_id, filename, sha, content, result, uploaded_at=None):
        """Stores the document; uploading the same file twice returns the existing record."""
        existing = self.conn.execute("SELECT id FROM documents WHERE patient_id = ? AND sha256 = ?",
                                     (patient_id, sha)).fetchone()
        if existing:
            return existing["id"], True
        cur = self.conn.execute(
            """INSERT INTO documents (patient_id, filename, sha256, content, status, issuer, document_date,
                                      vitals, warnings, reasons, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (patient_id, filename, sha, content, result["status"], result["issuer"], result["document_date"],
             json.dumps(result["vitals"]), json.dumps(result["warnings"]), json.dumps(result["reasons"]),
             uploaded_at or _now()))
        self.conn.commit()
        return cur.lastrowid, False

    def get_document(self, document_id, with_content=False):
        row = self.conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return self._document(row, with_content) if row else None

    def list_documents(self, patient_id):
        rows = self.conn.execute("SELECT * FROM documents WHERE patient_id = ? ORDER BY uploaded_at DESC",
                                 (patient_id,)).fetchall()
        return [self._document(r) for r in rows]

    def review_document(self, document_id, approve, reviewer=None):
        self.conn.execute("""UPDATE documents SET status = ?, reviewed_at = ?, reviewed_by = ?
                             WHERE id = ? AND status = 'pending_review'""",
                          ("approved" if approve else "rejected", _now(), reviewer, document_id))
        self.conn.commit()

    def usable_vitals(self, patient_id):
        """
        The document whose values feed the patient's risk score: the newest
        verified/clinician-approved one, otherwise the newest pending one
        (giving a provisional result). None if the patient has no usable document.
        """
        docs = [d for d in self.list_documents(patient_id) if d["status"] in USABLE_STATUSES]
        if not docs:
            return None
        newest = lambda d: (d["document_date"] or "", d["uploaded_at"])
        trusted = [d for d in docs if d["status"] in ("verified", "approved")]
        pool = trusted or docs
        # The base is the newest document with a blood pressure (the score cannot run without
        # one); a lab report with only cholesterol or glucose adds to it rather than replacing it.
        with_bp = [d for d in pool if "sbp" in d["vitals"]]
        base = max(with_bp or pool, key=newest)
        merged, sources = {}, []
        for d in sorted(pool, key=newest):            # oldest first, so newer values win...
            if d is base or newest(d) > newest(base):
                merged.update(d["vitals"])
                sources.append(d["id"])
            else:                                     # ...and older ones only fill gaps
                gaps = {k: v for k, v in d["vitals"].items() if k not in merged and k not in base["vitals"]}
                if gaps:
                    merged.update(gaps)
                    sources.append(d["id"])
        return {**base, "vitals": merged, "source_documents": sources}

    # ---------- medications ----------
    def add_medication(self, patient_id, name, dose, times, created_at=None):
        cur = self.conn.execute("INSERT INTO medications (patient_id, name, dose, times, created_at) VALUES (?, ?, ?, ?, ?)",
                                (patient_id, name, dose, json.dumps(sorted(times)), created_at or _now()))
        self.conn.commit()
        return cur.lastrowid

    def remove_medication(self, patient_id, medication_id):
        self.conn.execute("UPDATE medications SET active = 0 WHERE id = ? AND patient_id = ?", (medication_id, patient_id))
        self.conn.commit()

    def log_dose(self, patient_id, medication_id, day, slot, taken):
        owner = self.conn.execute("SELECT patient_id FROM medications WHERE id = ?", (medication_id,)).fetchone()
        if not owner or owner["patient_id"] != patient_id:
            return False
        if taken:
            self.conn.execute("INSERT OR IGNORE INTO med_logs (medication_id, day, slot, taken_at) VALUES (?, ?, ?, ?)",
                              (medication_id, day, slot, _now()))
        else:
            self.conn.execute("DELETE FROM med_logs WHERE medication_id = ? AND day = ? AND slot = ?",
                              (medication_id, day, slot))
        self.conn.commit()
        return True

    def medication_plan(self, patient_id, day=None, days=7):
        """
        Today's schedule with taken/not taken, plus adherence over the `days`
        days before today (today's doses may simply not be due yet).
        """
        day = day or today()
        meds = self.conn.execute("SELECT * FROM medications WHERE patient_id = ? AND active = 1 ORDER BY created_at",
                                 (patient_id,)).fetchall()
        start = (date.fromisoformat(day) - timedelta(days=days)).isoformat()
        end = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        plan, scheduled, taken_total = [], 0, 0
        for m in meds:
            times = json.loads(m["times"])
            # Only count days since the medication was added
            first_day = max(start, m["created_at"][:10])
            past = self.conn.execute("SELECT day, slot FROM med_logs WHERE medication_id = ? AND day BETWEEN ? AND ?",
                                     (m["id"], first_day, end)).fetchall()
            n_days = max((date.fromisoformat(end) - date.fromisoformat(first_day)).days + 1, 0)
            scheduled += len(times) * n_days
            taken_total += len({(r["day"], r["slot"]) for r in past if r["slot"] in times})
            today_taken = {r["slot"] for r in self.conn.execute(
                "SELECT slot FROM med_logs WHERE medication_id = ? AND day = ?", (m["id"], day))}
            plan.append({"id": m["id"], "name": m["name"], "dose": m["dose"], "times": times,
                         "doses": [{"slot": s, "taken": s in today_taken} for s in times]})
        adherence = round(taken_total / scheduled, 3) if scheduled else None
        return {"day": day, "medications": plan, "adherence_7d": adherence}

    # ---------- daily check-ins ----------
    def save_checkin(self, patient_id, day, mood, activity_minutes, water_glasses, symptoms):
        self.conn.execute(
            """INSERT INTO checkins (patient_id, day, mood, activity_minutes, water_glasses, symptoms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(patient_id, day) DO UPDATE SET mood = excluded.mood, activity_minutes = excluded.activity_minutes,
                   water_glasses = excluded.water_glasses, symptoms = excluded.symptoms""",
            (patient_id, day, mood, activity_minutes, water_glasses, json.dumps(symptoms), _now()))
        self.conn.commit()

    def recent_checkins(self, patient_id, days=14, day=None):
        day = day or today()
        start = (date.fromisoformat(day) - timedelta(days=days - 1)).isoformat()
        rows = self.conn.execute("SELECT * FROM checkins WHERE patient_id = ? AND day BETWEEN ? AND ? ORDER BY day",
                                 (patient_id, start, day)).fetchall()
        return [{**dict(r), "symptoms": json.loads(r["symptoms"])} for r in rows]

    @staticmethod
    def checkin_streak(checkins, day=None):
        """Consecutive days with a check-in, ending today (or yesterday if today is not done yet)."""
        days = {c["day"] for c in checkins}
        current = date.fromisoformat(day or today())
        if current.isoformat() not in days:
            current -= timedelta(days=1)
        streak = 0
        while current.isoformat() in days:
            streak += 1
            current -= timedelta(days=1)
        return streak

    # ---------- clinicians, sessions, audit ----------
    def add_clinician(self, clinician_id, name, title, password_hash, created_at=None):
        self.conn.execute("""INSERT OR IGNORE INTO clinicians (id, name, title, password_hash, created_at)
                             VALUES (?, ?, ?, ?, ?)""", (clinician_id, name, title, password_hash, created_at or _now()))
        self.conn.commit()

    def has_clinicians(self):
        return self.conn.execute("SELECT COUNT(*) FROM clinicians").fetchone()[0] > 0

    def get_clinician(self, clinician_id):
        row = self.conn.execute("SELECT * FROM clinicians WHERE id = ?", (clinician_id,)).fetchone()
        return dict(row) if row else None

    def clinician_name(self, clinician_id):
        if not clinician_id:
            return None
        row = self.conn.execute("SELECT name FROM clinicians WHERE id = ?", (clinician_id,)).fetchone()
        return row["name"] if row else clinician_id

    def update_clinician(self, clinician_id, **fields):
        allowed = {"photo", "password_hash", "failed_attempts", "locked_until"}
        assert set(fields) <= allowed
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE clinicians SET {sets} WHERE id = ?", (*fields.values(), clinician_id))
        self.conn.commit()

    def create_session(self, token_hash, clinician_id, expires_at):
        self.conn.execute("INSERT INTO sessions (token_hash, clinician_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                          (token_hash, clinician_id, _now(), expires_at))
        self.conn.commit()

    def session_clinician(self, token_hash, now_iso):
        row = self.conn.execute("""SELECT c.* FROM sessions s JOIN clinicians c ON c.id = s.clinician_id
                                   WHERE s.token_hash = ? AND s.expires_at > ?""", (token_hash, now_iso)).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash):
        self.conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        self.conn.commit()

    def delete_sessions_for(self, clinician_id, keep=None):
        self.conn.execute("DELETE FROM sessions WHERE clinician_id = ? AND token_hash != ?", (clinician_id, keep or ""))
        self.conn.commit()

    def log(self, clinician_id, action, target=None, at=None):
        if action == "view_patient":
            # One screen can load a record more than once; count repeat views within a minute once
            last = self.conn.execute("SELECT action, target, at FROM audit_log WHERE clinician_id = ? ORDER BY id DESC LIMIT 1",
                                     (clinician_id,)).fetchone()
            if last and last["action"] == action and last["target"] == target and \
                    datetime.fromisoformat(_now()) - datetime.fromisoformat(last["at"]) < timedelta(minutes=1):
                return
        self.conn.execute("INSERT INTO audit_log (clinician_id, action, target, at) VALUES (?, ?, ?, ?)",
                          (clinician_id, action, target, at or _now()))
        self.conn.commit()

    def recent_activity(self, limit=50):
        rows = self.conn.execute("""SELECT a.*, c.name AS clinician_name, c.photo AS clinician_photo
                                    FROM audit_log a JOIN clinicians c ON c.id = a.clinician_id
                                    ORDER BY a.at DESC, a.id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------- triage ----------
    def triage_queue(self, now=None):
        """
        Every patient with at least one assessment, highest priority first.
        Priority: flagged patients, then by latest risk.
        """
        now = now or datetime.now(timezone.utc)
        queue = []
        for p in self.conn.execute("SELECT id, name, sex, dob, created_at FROM patients").fetchall():
            history = self.conn.execute(
                "SELECT * FROM assessments WHERE patient_id = ? ORDER BY created_at DESC", (p["id"],)).fetchall()
            if not history:
                continue
            latest = self._assessment(history[0])
            previous = next((self._assessment(a) for a in history[1:] if a["kind"] == latest["kind"]), None)
            change = latest["probability"] - previous["probability"] if previous else None

            flags = []
            if latest["tier"] in ("high", "critical"):
                flags.append("high_risk")
            if change is not None and change >= RISING_THRESHOLD:
                flags.append("rising")
            age_days = (now - datetime.fromisoformat(latest["created_at"])).days
            if latest["tier"] in ("high", "critical") and age_days > FOLLOW_UP_DAYS:
                flags.append("follow_up_due")
            pending = self.conn.execute("SELECT COUNT(*) FROM documents WHERE patient_id = ? AND status = 'pending_review'",
                                        (p["id"],)).fetchone()[0]
            if pending:
                flags.append("needs_verification")

            latest["created_by_name"] = self.clinician_name(latest.get("created_by"))
            queue.append({**dict(p), "latest": latest, "change": change,
                          "days_since": age_days, "flags": flags, "n_assessments": len(history)})

        queue.sort(key=lambda q: (-len(q["flags"]), -q["latest"]["probability"]))
        return queue

    @staticmethod
    def _assessment(row):
        a = dict(row)
        a["inputs"] = json.loads(a["inputs"])
        return a

    @staticmethod
    def _document(row, with_content=False):
        d = dict(row)
        for k in ("vitals", "warnings", "reasons"):
            d[k] = json.loads(d[k])
        if not with_content:
            d.pop("content", None)
        return d


def days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")

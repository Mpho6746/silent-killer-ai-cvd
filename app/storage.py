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
"""

# Columns added after the first release; created on older databases at startup
MIGRATIONS = {"patients": {"dob": "TEXT", "photo": "TEXT"}}


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

    def get_profile(self, patient_id):
        row = self.conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
        return dict(row) if row else None

    def get_patient(self, patient_id):
        profile = self.get_profile(patient_id)
        if not profile:
            return None
        history = self.conn.execute(
            "SELECT * FROM assessments WHERE patient_id = ? ORDER BY created_at", (patient_id,)).fetchall()
        return {**profile, "assessments": [self._assessment(a) for a in history],
                "documents": self.list_documents(patient_id)}

    # ---------- assessments ----------
    def add_assessment(self, patient_id, kind, inputs, probability, tier, created_at=None):
        cur = self.conn.execute(
            "INSERT INTO assessments (patient_id, kind, inputs, probability, tier, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (patient_id, kind, json.dumps(inputs), probability, tier, created_at or _now()))
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

    def review_document(self, document_id, approve):
        self.conn.execute("UPDATE documents SET status = ?, reviewed_at = ? WHERE id = ? AND status = 'pending_review'",
                          ("approved" if approve else "rejected", _now(), document_id))
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
        return max(trusted or docs, key=newest)

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

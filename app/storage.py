"""
Local SQLite store for patients and their assessments (no cloud account needed).

The triage queue is built here: each patient's latest assessment, the change
since their previous one, and proactive flags a clinician should see first.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from src.preprocessing import ROOT

DEFAULT_DB = ROOT / "data" / "app" / "skai.db"

# A jump of 10 percentage points or more between two assessments is flagged
RISING_THRESHOLD = 0.10
# High/critical patients not re-assessed within this many days are flagged
FOLLOW_UP_DAYS = 30

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
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path=DEFAULT_DB):
        self.path = path
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def is_empty(self):
        return self.conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 0

    def upsert_patient(self, patient_id, name, sex=None, created_at=None):
        self.conn.execute(
            """INSERT INTO patients (id, name, sex, created_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name = excluded.name, sex = COALESCE(excluded.sex, patients.sex)""",
            (patient_id, name, sex, created_at or _now()))
        self.conn.commit()

    def add_assessment(self, patient_id, kind, inputs, probability, tier, created_at=None):
        cur = self.conn.execute(
            "INSERT INTO assessments (patient_id, kind, inputs, probability, tier, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (patient_id, kind, json.dumps(inputs), probability, tier, created_at or _now()))
        self.conn.commit()
        return cur.lastrowid

    def get_assessment(self, assessment_id):
        row = self.conn.execute("SELECT * FROM assessments WHERE id = ?", (assessment_id,)).fetchone()
        return self._assessment(row) if row else None

    def get_patient(self, patient_id):
        row = self.conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
        if not row:
            return None
        history = self.conn.execute(
            "SELECT * FROM assessments WHERE patient_id = ? ORDER BY created_at", (patient_id,)).fetchall()
        return {**dict(row), "assessments": [self._assessment(a) for a in history]}

    def triage_queue(self, now=None):
        """
        Every patient with at least one assessment, highest priority first.
        Priority: flagged patients, then by latest risk.
        """
        now = now or datetime.now(timezone.utc)
        queue = []
        for p in self.conn.execute("SELECT * FROM patients").fetchall():
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

            queue.append({**dict(p), "latest": latest, "change": change,
                          "days_since": age_days, "flags": flags, "n_assessments": len(history)})

        queue.sort(key=lambda q: (-len(q["flags"]), -q["latest"]["probability"]))
        return queue

    @staticmethod
    def _assessment(row):
        a = dict(row)
        a["inputs"] = json.loads(a["inputs"])
        return a


def days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")

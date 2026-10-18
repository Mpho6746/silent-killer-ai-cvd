"""
Facilities and staff roles.

South African primary care is largely nurse-led, so access follows the role
and the facility, not "any doctor sees everyone":

  role               can do
  nurse              arrival triage, screening assessments, bookings, referrals, app codes
  doctor             all of the above + approve/reject documents, record confirmed outcomes
  facility_manager   bookings, app codes, facility dashboard, full activity log for the facility
  district_manager   read-only view across all facilities: district dashboard, monitoring, activity log

A staff member sees the patients registered at their own facility plus any
patient referred to it. District managers see every facility.

The demo facilities are fictional.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_clinician

router = APIRouter()

DEMO_FACILITIES = [
    ("kopanong-clinic", "Kopanong Community Clinic", "clinic", "Demo District"),
    ("ubuntu-chc", "Ubuntu Community Health Centre", "clinic", "Demo District"),
    ("lesedi-hospital", "Lesedi District Hospital", "hospital", "Demo District"),
]

# username -> (role, facility); the six doctors from the brief plus two nurses and a district manager
DEMO_STAFF = {
    "samuel.olajide": ("doctor", "lesedi-hospital"),
    "tshiamo.bokaba": ("doctor", "lesedi-hospital"),
    "sfiso.kekana": ("doctor", "kopanong-clinic"),
    "mpho.mutengwe": ("doctor", "kopanong-clinic"),
    "kamogelo": ("doctor", "ubuntu-chc"),
    "neontle": ("doctor", "ubuntu-chc"),
}
DEMO_EXTRA_STAFF = [
    # username, name, title, role, facility
    ("nurse.thandi", "Sr Thandi Mokoena", "Professional Nurse", "nurse", "kopanong-clinic"),
    ("nurse.lindiwe", "Sr Lindiwe Zulu", "Professional Nurse", "nurse", "ubuntu-chc"),
    ("manager.district", "Ms Refilwe Dube", "District Health Manager", "district_manager", None),
]
DEMO_PATIENT_FACILITY = {
    "DEMO-001": "kopanong-clinic", "DEMO-003": "kopanong-clinic", "DEMO-101": "kopanong-clinic",
    "DEMO-002": "ubuntu-chc", "DEMO-004": "ubuntu-chc", "DEMO-102": "ubuntu-chc",
    "DEMO-005": "lesedi-hospital",
}

PERMISSIONS = {
    "triage": {"nurse", "doctor"},
    "assess": {"nurse", "doctor"},
    "review_document": {"doctor"},
    "record_outcome": {"doctor"},
    "book": {"nurse", "doctor", "facility_manager"},
    "refer": {"nurse", "doctor"},
    "activation_code": {"nurse", "doctor", "facility_manager"},
    "dashboard": {"doctor", "facility_manager", "district_manager"},
    "messages": {"nurse", "doctor", "facility_manager", "district_manager"},
}


def seed_facilities(store, password_hash):
    """Creates demo facilities, gives staff roles and facilities, and places demo patients. Safe to run every start."""
    conn = store.conn
    conn.executemany("INSERT OR IGNORE INTO facilities (id, name, kind, district) VALUES (?, ?, ?, ?)", DEMO_FACILITIES)
    for username, (role, facility) in DEMO_STAFF.items():
        conn.execute("UPDATE clinicians SET role = ?, facility_id = COALESCE(facility_id, ?) WHERE id = ?",
                     (role, facility, username))
    for username, name, title, role, facility in DEMO_EXTRA_STAFF:
        store.add_clinician(username, name, title, password_hash)
        conn.execute("UPDATE clinicians SET role = ?, facility_id = ? WHERE id = ?", (role, facility, username))
    for pid, facility in DEMO_PATIENT_FACILITY.items():
        conn.execute("UPDATE patients SET facility_id = COALESCE(facility_id, ?) WHERE id = ?", (facility, pid))
    # Anyone registered before facilities existed belongs to the first clinic
    conn.execute("UPDATE patients SET facility_id = ? WHERE facility_id IS NULL", (DEMO_FACILITIES[0][0],))
    conn.execute("UPDATE clinicians SET facility_id = ? WHERE facility_id IS NULL AND role != 'district_manager'",
                 (DEMO_FACILITIES[0][0],))
    conn.commit()


def facility_name(store, facility_id):
    row = store.conn.execute("SELECT name FROM facilities WHERE id = ?", (facility_id,)).fetchone()
    return row["name"] if row else None


def allowed(clinician, action):
    return clinician.get("role", "doctor") in PERMISSIONS[action]


def require(action):
    """Dependency: a signed-in clinician whose role allows this action."""
    def check(clinician=Depends(require_clinician)):
        if not allowed(clinician, action):
            raise HTTPException(403, detail={"code": "role_not_allowed"})
        return clinician
    return check


def can_see_patient(store, clinician, patient_id):
    if clinician.get("role") == "district_manager":
        return True
    row = store.conn.execute(
        """SELECT 1 FROM patients p WHERE p.id = ? AND (p.facility_id = ? OR EXISTS (
               SELECT 1 FROM referrals r WHERE r.patient_id = p.id AND r.to_facility = ?
                      AND r.status NOT IN ('closed', 'cancelled')))""",
        (patient_id, clinician.get("facility_id"), clinician.get("facility_id"))).fetchone()
    return row is not None


def visible_patient_ids(store, clinician):
    if clinician.get("role") == "district_manager":
        return None   # everyone
    rows = store.conn.execute(
        """SELECT id FROM patients WHERE facility_id = ?
           UNION SELECT patient_id FROM referrals WHERE to_facility = ? AND status NOT IN ('closed', 'cancelled')""",
        (clinician.get("facility_id"), clinician.get("facility_id"))).fetchall()
    return {r[0] for r in rows}


def require_visible(store, clinician, patient_id):
    if not can_see_patient(store, clinician, patient_id):
        raise HTTPException(403, detail={"code": "not_your_facility"})


@router.get("/api/facilities")
def facilities(request: Request):
    """Public list, used when a patient registers and when choosing where to refer."""
    rows = request.app.state.store.conn.execute("SELECT * FROM facilities ORDER BY kind, name").fetchall()
    return [dict(r) for r in rows]

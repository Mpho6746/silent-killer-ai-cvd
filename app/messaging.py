"""
SMS / WhatsApp reminders through an outbox.

Most patients use basic phones, so reminders go by SMS (or WhatsApp when the
patient prefers it): appointment reminders the day before, referral
instructions, and medication dose reminders. Messages are only sent to
patients who gave a phone number and opted in (POPIA).

Every message is written to the `messages` outbox first and sent by a
dispatcher, so nothing is lost if the provider is down and every message is
auditable. The provider is pluggable:

    SKAI_SMS_PROVIDER=console    (default) records the message as sent without
                                 contacting anyone - safe for demos
    SKAI_SMS_PROVIDER=<gateway>  plug in a real SMS/WhatsApp gateway adapter
                                 (e.g. a South African bulk-SMS provider) with
                                 its API credentials in environment variables

    GET  /api/messages             outbox for my facility's patients
    POST /api/messages/dispatch    send everything that is due now (also runs every minute)
"""
import asyncio
import os
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from app.facilities import require, visible_patient_ids

router = APIRouter()

PHONE_RE = re.compile(r"^\+27[6-8]\d{8}$")      # South African mobile numbers in international format
SAST = timezone(timedelta(hours=2), "SAST")       # South Africa: UTC+2 all year (no daylight saving)


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def normalise_phone(raw):
    """082 123 4567 / 0821234567 / +27 82 123 4567 -> +27821234567 (None if not an SA mobile)."""
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "+27" + digits[1:]
    elif digits.startswith("27") and len(digits) == 11:
        digits = "+" + digits
    return digits if PHONE_RE.match(digits) else None


def _recipient(store, patient_id):
    row = store.conn.execute("SELECT name, phone, sms_opt_in FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if not row or not row["phone"] or not row["sms_opt_in"]:
        return None
    return row


def _queue(store, patient_id, kind, body, when, channel="sms"):
    who = _recipient(store, patient_id)
    if not who:
        return None
    store.conn.execute(
        """INSERT OR IGNORE INTO messages (patient_id, channel, to_number, kind, body, scheduled_for, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""", (patient_id, channel, who["phone"], kind, body, _iso(when), _iso(_now())))
    store.conn.commit()
    return True


def _first_name(store, patient_id):
    return store.get_profile(patient_id)["name"].split()[0]


def queue_appointment_reminders(store, appointment_id):
    ap = store.conn.execute("""SELECT ap.*, f.name AS facility_name FROM appointments ap
                               JOIN facilities f ON f.id = ap.facility_id WHERE ap.id = ?""", (appointment_id,)).fetchone()
    starts = datetime.fromisoformat(ap["starts_at"])
    when = starts.astimezone(SAST).strftime("%a %d %b at %H:%M")
    name = _first_name(store, ap["patient_id"])
    _queue(store, ap["patient_id"], f"appt_booked:{appointment_id}",
           f"SKAI: Hi {name}, your appointment at {ap['facility_name']} is booked for {when}. Reply STOP to opt out.", _now())
    _queue(store, ap["patient_id"], f"appt_reminder:{appointment_id}",
           f"SKAI: Reminder {name} - your appointment at {ap['facility_name']} is tomorrow, {when}. Please bring your clinic card.",
           max(starts - timedelta(days=1), _now()))


def queue_referral_message(store, referral_id):
    r = store.conn.execute("""SELECT r.*, f.name AS to_name FROM referrals r JOIN facilities f ON f.id = r.to_facility
                              WHERE r.id = ?""", (referral_id,)).fetchone()
    due = datetime.fromisoformat(r["due_by"]).astimezone(SAST).strftime("%a %d %b")
    _queue(store, r["patient_id"], f"referral:{referral_id}",
           f"SKAI: {_first_name(store, r['patient_id'])}, you have been referred to {r['to_name']}. "
           f"Please go by {due} and take your referral letter.", _now())


def queue_medication_reminders(store, day=None):
    """Today's dose reminders for opted-in patients (idempotent: one message per dose)."""
    day = day or _now().astimezone(SAST).date()
    meds = store.conn.execute("""SELECT m.id, m.patient_id, m.name, m.dose, m.times FROM medications m
                                 JOIN patients p ON p.id = m.patient_id WHERE m.active = 1 AND p.sms_opt_in = 1""").fetchall()
    import json
    for m in meds:
        for slot in json.loads(m["times"]):
            hour, minute = map(int, slot.split(":"))
            when = datetime(day.year, day.month, day.day, hour, minute, tzinfo=SAST)   # dose times are local
            _queue(store, m["patient_id"], f"med:{m['id']}:{day.isoformat()}:{slot}",
                   f"SKAI: Time for your {m['name']}{' ' + m['dose'] if m['dose'] else ''}.", when)


# ---------- providers ----------

class ConsoleProvider:
    """Default for demos: records the message as sent; nobody is contacted."""
    name = "console"

    def send(self, channel, to_number, body):
        return f"console-{abs(hash((to_number, body))) % 10**8}"


def provider():
    name = os.environ.get("SKAI_SMS_PROVIDER", "console")
    if name == "console":
        return ConsoleProvider()
    raise RuntimeError(f"SMS provider '{name}' is not configured in this build")


def dispatch(store, now=None):
    """Send every queued message that is due. Returns how many were sent."""
    now = now or _now()
    queue_medication_reminders(store, now.date())
    due = store.conn.execute("SELECT * FROM messages WHERE status = 'queued' AND scheduled_for <= ? ORDER BY scheduled_for",
                             (_iso(now),)).fetchall()
    sender, sent = provider(), 0
    for m in due:
        if not _recipient(store, m["patient_id"]):          # opted out since it was queued
            store.conn.execute("UPDATE messages SET status = 'skipped' WHERE id = ?", (m["id"],))
            continue
        try:
            ref = sender.send(m["channel"], m["to_number"], m["body"])
            store.conn.execute("UPDATE messages SET status = 'sent', sent_at = ?, provider_ref = ? WHERE id = ?",
                               (_iso(now), ref, m["id"]))
            sent += 1
        except Exception:                                    # keep it queued-as-failed for a retry/manual follow-up
            store.conn.execute("UPDATE messages SET status = 'failed' WHERE id = ?", (m["id"],))
    store.conn.commit()
    return sent


async def dispatcher_loop(store, every_seconds=60):
    while True:
        try:
            dispatch(store)
        except Exception:
            pass
        await asyncio.sleep(every_seconds)


@router.get("/api/messages")
def outbox(request: Request, clinician=Depends(require("messages"))):
    store = request.app.state.store
    visible = visible_patient_ids(store, clinician)
    rows = store.conn.execute("""SELECT m.*, p.name AS patient_name FROM messages m JOIN patients p ON p.id = m.patient_id
                                 ORDER BY m.scheduled_for DESC LIMIT 300""").fetchall()
    return [dict(r) for r in rows if visible is None or r["patient_id"] in visible][:100]


@router.post("/api/messages/dispatch")
def dispatch_now(request: Request, clinician=Depends(require("messages"))):
    return {"sent": dispatch(request.app.state.store), "provider": provider().name}

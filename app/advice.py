"""
Personal health advice for the patient dashboard.

Rules are based on the patient's own trusted data: vitals from their latest
document, their latest risk result, medication adherence and daily check-ins.
Each item is a translation key plus parameters, so the app shows it in the
patient's language. Thresholds follow common clinical guidance (AHA blood
pressure stages, WHO BMI bands, WHO 150 active minutes a week, NCEP LDL).

Levels: urgent (shown as a pop-up straight away), warning, tip, good.
"""
from datetime import date

URGENT_SYMPTOMS = {"chest_pain", "shortness_of_breath", "fainting"}
LEVEL_ORDER = {"urgent": 0, "warning": 1, "tip": 2, "good": 3}
CHECKUP_MONTHS = {"low": 12, "moderate": 6, "high": 3, "critical": 1}


def bmi(vitals):
    if vitals.get("height_cm") and vitals.get("weight_kg"):
        return round(vitals["weight_kg"] / (vitals["height_cm"] / 100) ** 2, 1)
    return None


def next_checkup(document_date, tier):
    """When the patient should next get measured, based on their risk tier."""
    if not document_date:
        return None
    d = date.fromisoformat(document_date)
    months = CHECKUP_MONTHS.get(tier or "moderate", 6)
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(d.day, 28)).isoformat()


def advice_for(document, latest_result, meds, checkins, today=None):
    today = today or date.today()
    items = []
    add = lambda key, level, **params: items.append({"key": key, "level": level, "params": params})

    today_checkin = next((c for c in checkins if c["day"] == today.isoformat()), None)
    if today_checkin and URGENT_SYMPTOMS & set(today_checkin["symptoms"]):
        add("symptoms_urgent", "urgent")

    if not document:
        add("upload_document", "warning")
    else:
        if document["status"] == "pending_review":
            add("awaiting_review", "tip")
        if document["document_date"] and (today - date.fromisoformat(document["document_date"])).days > 180:
            add("document_old", "tip")
        v = document["vitals"]
        sbp = v.get("sbp")
        if sbp is not None:
            if sbp >= 140:
                add("bp_high", "warning", value=int(sbp))
            elif sbp >= 130:
                add("bp_elevated", "tip", value=int(sbp))
            elif sbp < 120:
                add("bp_good", "good", value=int(sbp))
        b = bmi(v)
        if b is not None:
            if b >= 30:
                add("bmi_obese", "warning", value=b)
            elif b >= 25:
                add("bmi_over", "tip", value=b)
            elif b >= 18.5:
                add("bmi_good", "good", value=b)
        if v.get("ldl") is not None and v["ldl"] >= 4.1:
            add("ldl_high", "warning", value=v["ldl"])
        glucose = v.get("glucose")
        if glucose is not None:
            if glucose >= 7.0:
                add("glucose_high", "warning", value=glucose)
            elif glucose >= 5.6:
                add("glucose_borderline", "tip", value=glucose)

    if latest_result:
        if latest_result["tier"] in ("high", "critical"):
            add("risk_high", "warning")
        if latest_result["inputs"].get("cigarettes_per_day", 0) > 0:
            add("smoking", "warning")

    if meds["medications"]:
        due = sum(1 for m in meds["medications"] for d in m["doses"] if not d["taken"])
        if due:
            add("meds_due", "tip", n=due)
        if meds["adherence_7d"] is not None and meds["adherence_7d"] < 0.8:
            add("meds_adherence", "warning", value=round(meds["adherence_7d"] * 100))

    week = [c for c in checkins if (today - date.fromisoformat(c["day"])).days < 7]
    if week:
        minutes = sum(c["activity_minutes"] or 0 for c in week)
        if minutes >= 150:
            add("activity_good", "good", value=minutes)
        else:
            add("activity_low", "tip", value=minutes)
    if not today_checkin:
        add("checkin_today", "tip")
    elif (today_checkin["water_glasses"] or 0) < 6:
        add("water", "tip")

    items.sort(key=lambda i: LEVEL_ORDER[i["level"]])
    return items

"""
Working with the rest of the South African health system.

    South African ID numbers   checked with the Luhn check digit, and the date of
                               birth and sex they encode compared with the record.
    HL7 FHIR R4 export         GET  /api/patients/{id}/fhir    Patient, vitals as LOINC-coded
                               Observations and SKAI scores as RiskAssessments, for the
                               patient or a clinician at their facility.
    HL7 FHIR R4 lab import     POST /api/fhir/lab-results      a lab system (e.g. NHLS) sends a
                               Bundle of Observations. Two ways in:
                                 - machine feed with the X-Integration-Key header -> "verified"
                                 - a clinician importing a result file -> "approved" by them
                               Either way the values become a document the patient's risk
                               check can use, exactly like a signed PDF.

The integration key comes from SKAI_LAB_KEY. In demo mode it defaults to
"demo-lab-key" so the feed can be shown; set a real secret in production.
"""
import json
import os
import secrets
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import optional_clinician
from app.documents import RANGES, sha256
from app.facilities import allowed, require_visible
from app.patient_auth import session_patient_id

router = APIRouter()

LOINC = "http://loinc.org"
SKAI_ID = "urn:skai:patient-id"
SA_ID_SYSTEM = "urn:za:gov:dha:id-number"
# Our vital name -> (LOINC code, display, UCUM unit)
CODES = {
    "sbp": ("8480-6", "Systolic blood pressure", "mm[Hg]"),
    "dbp": ("8462-4", "Diastolic blood pressure", "mm[Hg]"),
    "heart_rate": ("8867-4", "Heart rate", "/min"),
    "total_cholesterol": ("2093-3", "Cholesterol [Moles/volume] in Serum or Plasma", "mmol/L"),
    "hdl": ("2085-9", "HDL cholesterol", "mmol/L"),
    "ldl": ("13457-7", "LDL cholesterol (calculated)", "mmol/L"),
    "glucose": ("2345-7", "Glucose [Moles/volume] in Serum or Plasma", "mmol/L"),
    "height_cm": ("8302-2", "Body height", "cm"),
    "weight_kg": ("29463-7", "Body weight", "kg"),
}
BY_CODE = {code: key for key, (code, _, _) in CODES.items()}
BY_CODE["18262-6"] = "ldl"        # LDL measured directly
MG_DL = {"total_cholesterol": 38.67, "hdl": 38.67, "ldl": 38.67, "glucose": 18.0}


# ---------- South African ID numbers ----------

def luhn_ok(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def parse_sa_id(raw, today=None):
    """{"valid": bool, "dob", "sex", "citizen", "reason"} for a 13-digit SA ID number."""
    number = "".join(ch for ch in str(raw) if not ch.isspace())
    if len(number) != 13 or not number.isdigit():
        return {"valid": False, "reason": "format"}
    if not luhn_ok(number):
        return {"valid": False, "reason": "checksum"}
    today = today or date.today()
    yy, mm, dd = int(number[:2]), int(number[2:4]), int(number[4:6])
    century = 2000 if 2000 + yy <= today.year else 1900
    try:
        dob = date(century + yy, mm, dd)
    except ValueError:
        return {"valid": False, "reason": "date"}
    if number[10] not in "01":
        return {"valid": False, "reason": "citizenship"}
    return {"valid": True, "dob": dob.isoformat(), "sex": "F" if int(number[6:10]) < 5000 else "M",
            "citizen": number[10] == "0", "number": number}


def check_sa_id_against(raw, profile):
    """Parsed ID, or a reason code if it is invalid or does not match the record."""
    parsed = parse_sa_id(raw)
    if not parsed["valid"]:
        return None, f"sa_id_{parsed['reason']}"
    if profile.get("dob") and profile["dob"] != parsed["dob"]:
        return None, "sa_id_dob_mismatch"
    if profile.get("sex") and profile["sex"] != parsed["sex"]:
        return None, "sa_id_sex_mismatch"
    return parsed, None


class IdCheck(BaseModel):
    number: str = Field(max_length=20)


@router.post("/api/sa-id/check")
def sa_id_check(body: IdCheck):
    return parse_sa_id(body.number)


# ---------- FHIR export ----------

def _observation(patient_ref, key, value, when, issuer):
    code, display, unit = CODES[key]
    return {"resourceType": "Observation", "status": "final",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                      "code": "vital-signs" if key in ("sbp", "dbp", "heart_rate", "height_cm", "weight_kg") else "laboratory"}]}],
            "code": {"coding": [{"system": LOINC, "code": code, "display": display}]},
            "subject": {"reference": patient_ref}, "effectiveDateTime": when,
            "performer": [{"display": issuer}] if issuer else [],
            "valueQuantity": {"value": value, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit}}


def fhir_bundle(store, patient_id, validated=None):
    profile = store.get_profile(patient_id)
    ref = f"Patient/{patient_id}"
    identifiers = [{"system": SKAI_ID, "value": patient_id}]
    if profile.get("sa_id"):
        identifiers.append({"system": SA_ID_SYSTEM, "value": profile["sa_id"]})
    patient = {"resourceType": "Patient", "id": patient_id, "identifier": identifiers, "name": [{"text": profile["name"]}]}
    if profile.get("sex"):
        patient["gender"] = {"M": "male", "F": "female"}[profile["sex"]]
    if profile.get("dob"):
        patient["birthDate"] = profile["dob"]
    if profile.get("phone"):
        patient["telecom"] = [{"system": "phone", "value": profile["phone"], "use": "mobile"}]
    entries = [patient]

    for doc in store.list_documents(patient_id):
        if doc["status"] in ("verified", "approved"):
            when = doc["document_date"] or doc["uploaded_at"][:10]
            entries += [_observation(ref, k, v, when, doc["issuer"]) for k, v in doc["vitals"].items() if k in CODES]

    for a in store.get_patient(patient_id)["assessments"]:
        entries.append({
            "resourceType": "RiskAssessment", "id": f"skai-{a['id']}", "status": "final",
            "subject": {"reference": ref}, "occurrenceDateTime": a["created_at"],
            "method": {"text": f"SKAI {a['kind']} model (screening aid, not a diagnosis)"},
            "prediction": [{"outcome": {"text": "Cardiovascular disease"},
                            "probabilityDecimal": round(a["probability"], 4),
                            "qualitativeRisk": {"text": a["tier"]}}],
        })
    if validated and validated.get("available"):
        entries.append({"resourceType": "RiskAssessment", "id": "framingham-latest", "status": "final",
                        "subject": {"reference": ref}, "method": {"text": validated["name"]},
                        "prediction": [{"outcome": {"text": "10-year cardiovascular disease"},
                                        "probabilityDecimal": round(validated["risk_10y"], 4),
                                        "qualitativeRisk": {"text": validated["category"]}, "whenRange": {"high": {"value": 10, "unit": "a"}}}]})
    return {"resourceType": "Bundle", "type": "collection",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entry": [{"fullUrl": f"urn:skai:{e['resourceType']}/{e.get('id', i)}", "resource": e} for i, e in enumerate(entries)]}


@router.get("/api/patients/{patient_id}/fhir")
def export_fhir(patient_id: str, request: Request, clinician=Depends(optional_clinician),
                signed_in=Depends(session_patient_id)):
    store = request.app.state.store
    if not store.get_profile(patient_id):
        raise HTTPException(404, "Patient not found")
    if clinician:
        require_visible(store, clinician, patient_id)
        store.log(clinician["id"], "fhir_export", patient_id)
    elif signed_in != patient_id:
        raise HTTPException(401 if not signed_in else 403, detail={"code": "not_your_record"})
    return fhir_bundle(store, patient_id)


# ---------- FHIR lab import ----------

def read_observations(bundle):
    """{patient_id: {"vitals": {...}, "date": ISO, "issuer": str}} from a FHIR Bundle of Observations."""
    if bundle.get("resourceType") != "Bundle":
        raise HTTPException(422, detail={"code": "not_a_bundle"})
    out = {}
    for entry in bundle.get("entry", []):
        obs = entry.get("resource", {})
        if obs.get("resourceType") != "Observation" or obs.get("status") not in ("final", "amended", "corrected"):
            continue
        ref = obs.get("subject", {}).get("reference", "")
        codes = [c.get("code") for c in obs.get("code", {}).get("coding", []) if c.get("system") == LOINC]
        key = next((BY_CODE[c] for c in codes if c in BY_CODE), None)
        quantity = obs.get("valueQuantity", {})
        if not ref.startswith("Patient/") or not key or not isinstance(quantity.get("value"), (int, float)):
            continue
        value = float(quantity["value"])
        if key in MG_DL and str(quantity.get("unit", "")).lower().startswith("mg"):
            value = round(value / MG_DL[key], 2)
        low, high = RANGES[key]
        if not low <= value <= high:
            continue
        record = out.setdefault(ref.split("/", 1)[1], {"vitals": {}, "date": None, "issuer": None})
        record["vitals"][key] = value
        when = (obs.get("effectiveDateTime") or "")[:10]
        record["date"] = max(filter(None, [record["date"], when]), default=None)
        record["issuer"] = record["issuer"] or next((p.get("display") for p in obs.get("performer", []) if p.get("display")), None)
    return out


def lab_key():
    key = os.environ.get("SKAI_LAB_KEY")
    if key:
        return key
    return "demo-lab-key" if os.environ.get("SKAI_DEMO", "1") == "1" else None


@router.post("/api/fhir/lab-results")
def import_lab_results(bundle: dict, request: Request, clinician=Depends(optional_clinician),
                       integration_key: str | None = Header(default=None, alias="X-Integration-Key")):
    store = request.app.state.store
    key = lab_key()
    machine = bool(integration_key and key and secrets.compare_digest(integration_key, key))
    if not machine:
        if not clinician:
            raise HTTPException(401, detail={"code": "not_signed_in"})
        if not allowed(clinician, "assess"):
            raise HTTPException(403, detail={"code": "role_not_allowed"})
    results = read_observations(bundle)
    if not results:
        raise HTTPException(422, detail={"code": "no_usable_observations"})
    imported = []
    for patient_id, rec in results.items():
        if not store.get_profile(patient_id):
            raise HTTPException(404, detail={"code": "unknown_patient", "patient_id": patient_id})
        if clinician and not machine:
            require_visible(store, clinician, patient_id)
        raw = json.dumps({"resourceType": "Bundle", "type": "collection",
                          "entry": [e for e in bundle.get("entry", [])
                                    if e.get("resource", {}).get("subject", {}).get("reference") == f"Patient/{patient_id}"]},
                         sort_keys=True).encode()
        issuer = f"{rec['issuer'] or 'Laboratory'} (FHIR)"
        doc_id, duplicate = store.add_document(patient_id, f"lab-results-{rec['date'] or 'undated'}.json", sha256(raw), raw, {
            "status": "verified" if machine else "approved", "issuer": issuer, "document_date": rec["date"],
            "vitals": rec["vitals"], "warnings": [], "reasons": []})
        if clinician and not machine and not duplicate:
            store.conn.execute("UPDATE documents SET reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                               (clinician["id"], datetime.now(timezone.utc).isoformat(timespec="seconds"), doc_id))
            store.conn.commit()
            store.log(clinician["id"], "fhir_import", patient_id)
        imported.append({"patient_id": patient_id, "document_id": doc_id, "vitals": rec["vitals"], "duplicate": duplicate,
                         "status": "verified" if machine else "approved"})
    return {"imported": imported}

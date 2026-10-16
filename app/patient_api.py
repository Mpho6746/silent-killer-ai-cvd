"""
Patient health-tracker endpoints (mounted by app/api.py).

Patients never type their own vitals. They upload the clinical document they
were given, and the self-check reads blood pressure, LDL, height and weight
from their latest accepted document (see app/documents.py). Only things no
clinic document records - smoking and family history - are self-declared.

    POST   /api/patients/{id}/profile                 create/update profile (name, date of birth, sex)
    PUT    /api/patients/{id}/photo                   profile picture (small JPEG/PNG data URL)
    POST   /api/patients/{id}/documents               upload a clinical PDF -> verified / pending_review / rejected
    GET    /api/patients/{id}/dashboard               everything the patient home screen needs
    POST   /api/patients/{id}/self-check              risk check using document vitals
    POST   /api/patients/{id}/medications             add a medication to the planner
    DELETE /api/patients/{id}/medications/{mid}       stop a medication
    POST   /api/patients/{id}/medications/{mid}/doses mark a dose taken / not taken
    POST   /api/patients/{id}/checkins                today's check-in (mood, activity, water, symptoms)
    GET    /api/documents/{id}/file                   the original PDF (for clinician review)
    POST   /api/documents/{id}/review                 clinician approves or rejects a pending document
"""
import re
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.advice import advice_for, bmi, next_checkup
from app.documents import MAX_BYTES, read_document, sha256
from app.storage import today

router = APIRouter()

SYMPTOMS = ["chest_pain", "shortness_of_breath", "fainting", "palpitations", "dizziness", "swelling", "fatigue"]
PHOTO_RE = re.compile(r"^data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$")
MAX_PHOTO_CHARS = 400_000     # ~300 KB image; the app resizes to 256x256 before upload


class Profile(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    dob: date
    sex: Optional[Literal["M", "F"]] = None

    @field_validator("dob")
    @classmethod
    def adult(cls, v):
        age = _age(v)
        if not 18 <= age <= 100:
            raise ValueError("SKAI's models are for adults aged 18-100")
        return v


class Photo(BaseModel):
    data_url: str = Field(max_length=MAX_PHOTO_CHARS)

    @field_validator("data_url")
    @classmethod
    def image(cls, v):
        if not PHOTO_RE.match(v):
            raise ValueError("photo must be a JPEG, PNG or WebP data URL")
        return v


class SelfCheck(BaseModel):
    """Only what a clinic document cannot tell us. Vitals come from the document."""
    model_config = ConfigDict(extra="forbid")   # sending sbp, ldl, weight... is rejected, not ignored
    cigarettes_per_day: float = Field(0, ge=0, le=80)
    years_smoked: float = Field(0, ge=0, le=80)
    family_history: bool = False


class Medication(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    dose: Optional[str] = Field(None, max_length=40)
    times: list[str] = Field(min_length=1, max_length=6)

    @field_validator("times")
    @classmethod
    def clock_times(cls, v):
        if not all(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t) for t in v):
            raise ValueError("times must be HH:MM")
        return sorted(set(v))


class Dose(BaseModel):
    slot: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    taken: bool
    day: Optional[date] = None


class CheckIn(BaseModel):
    mood: int = Field(ge=1, le=5)
    activity_minutes: int = Field(0, ge=0, le=600)
    water_glasses: int = Field(0, ge=0, le=30)
    symptoms: list[Literal[tuple(SYMPTOMS)]] = []


class Review(BaseModel):
    approve: bool


def _age(dob):
    t = date.today()
    return t.year - dob.year - ((t.month, t.day) < (dob.month, dob.day))


def _store(request):
    return request.app.state.store


def _profile_or_404(request, patient_id):
    profile = _store(request).get_profile(patient_id)
    if not profile:
        raise HTTPException(404, "Patient not found")
    return profile


# ---------- profile ----------

@router.post("/api/patients/{patient_id}/profile")
def save_profile(patient_id: str, profile: Profile, request: Request):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", patient_id):
        raise HTTPException(422, "Patient ID may only contain letters, numbers, - and _")
    _store(request).upsert_patient(patient_id, profile.name, profile.sex, dob=profile.dob.isoformat())
    return _store(request).get_profile(patient_id)


@router.put("/api/patients/{patient_id}/photo")
def save_photo(patient_id: str, photo: Photo, request: Request):
    _profile_or_404(request, patient_id)
    _store(request).set_photo(patient_id, photo.data_url)
    return {"ok": True}


# ---------- documents ----------

@router.post("/api/patients/{patient_id}/documents")
async def upload_document(patient_id: str, request: Request, file: UploadFile = File(...)):
    profile = _profile_or_404(request, patient_id)
    content = await file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "Document is larger than 5 MB")
    result = read_document(content, expected_name=profile["name"]).to_dict()
    doc_id, duplicate = _store(request).add_document(patient_id, file.filename or "document.pdf",
                                                     sha256(content), content, result)
    return {**_store(request).get_document(doc_id), "duplicate": duplicate}


@router.get("/api/documents/{document_id}/file")
def document_file(document_id: int, request: Request):
    doc = _store(request).get_document(document_id, with_content=True)
    if not doc:
        raise HTTPException(404, "Document not found")
    return Response(doc["content"], media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{doc["filename"]}"'})


@router.post("/api/documents/{document_id}/review")
def review_document(document_id: int, review: Review, request: Request):
    store = _store(request)
    doc = store.get_document(document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc["status"] != "pending_review":
        raise HTTPException(409, f"Document is already {doc['status']}")
    store.review_document(document_id, review.approve)
    return store.get_document(document_id)


# ---------- self-check (vitals from documents only) ----------

def lifestyle_inputs(profile, document, answers):
    """Build the model inputs: vitals from the document, age from date of birth, the rest self-declared."""
    v = document["vitals"]
    if "sbp" not in v:
        raise HTTPException(409, detail={"code": "document_missing_bp"})
    inputs = {"age": _age(date.fromisoformat(profile["dob"])), "sbp": round(v["sbp"]),
              **answers, "document_id": document["id"], "vitals_status": document["status"]}
    for key in ("ldl", "height_cm", "weight_kg"):
        if key in v:
            inputs[key] = v[key]
    return inputs


@router.post("/api/patients/{patient_id}/self-check")
def self_check(patient_id: str, answers: SelfCheck, request: Request):
    store = _store(request)
    profile = _profile_or_404(request, patient_id)
    if not profile.get("dob"):
        raise HTTPException(409, detail={"code": "profile_incomplete"})
    if answers.years_smoked > _age(date.fromisoformat(profile["dob"])):
        raise HTTPException(422, "years_smoked cannot be more than age")
    document = store.usable_vitals(patient_id)
    if not document:
        raise HTTPException(409, detail={"code": "no_document"})

    inputs = lifestyle_inputs(profile, document, answers.model_dump())
    result = request.app.state.lifestyle.assess(inputs)
    result["assessment_id"] = store.add_assessment(patient_id, "lifestyle", inputs, result["probability"], result["tier"])
    result["vitals_source"] = _source(document)
    result["inputs"] = inputs
    return result


def _source(document):
    return {k: document[k] for k in ("id", "status", "issuer", "document_date", "filename")}


# ---------- dashboard ----------

@router.get("/api/patients/{patient_id}/dashboard")
def dashboard(patient_id: str, request: Request):
    store = _store(request)
    profile = _profile_or_404(request, patient_id)
    document = store.usable_vitals(patient_id)
    meds = store.medication_plan(patient_id)
    checkins = store.recent_checkins(patient_id)
    streak = store.checkin_streak(checkins)

    latest = store.latest_assessment(patient_id, "lifestyle")
    result = None
    if latest:
        result = request.app.state.lifestyle.assess(latest["inputs"])   # re-run for the explanation
        result.update(assessment_id=latest["id"], created_at=latest["created_at"], inputs=latest["inputs"])
        doc = store.get_document(latest["inputs"].get("document_id")) if latest["inputs"].get("document_id") else None
        result["vitals_source"] = _source(doc) if doc else None

    history = [{"date": a["created_at"], "probability": a["probability"], "tier": a["tier"]}
               for a in store.get_patient(patient_id)["assessments"] if a["kind"] == "lifestyle"]
    vitals_history = [{"date": d["document_date"] or d["uploaded_at"][:10], **d["vitals"]}
                      for d in reversed(store.list_documents(patient_id)) if d["status"] != "rejected"]

    advice = advice_for(document, latest, meds, checkins)
    if streak >= 3:
        advice.append({"key": "streak", "level": "good", "params": {"n": streak}})

    vitals = dict(document["vitals"]) if document else {}
    if vitals:
        vitals["bmi"] = bmi(vitals)
    return {
        "profile": {**profile, "age": _age(date.fromisoformat(profile["dob"])) if profile.get("dob") else None},
        "vitals": vitals,
        "vitals_source": _source(document) if document else None,
        "documents": store.list_documents(patient_id),
        "result": result,
        "history": history,
        "vitals_history": vitals_history,
        "medications": meds,
        "checkins": checkins,
        "today_checkin": next((c for c in checkins if c["day"] == today()), None),
        "streak": streak,
        "advice": advice,
        "next_checkup": next_checkup(document["document_date"], result["tier"] if result else None) if document else None,
        "symptom_options": SYMPTOMS,
    }


# ---------- medication planner ----------

@router.post("/api/patients/{patient_id}/medications")
def add_medication(patient_id: str, med: Medication, request: Request):
    _profile_or_404(request, patient_id)
    _store(request).add_medication(patient_id, med.name, med.dose, med.times)
    return _store(request).medication_plan(patient_id)


@router.delete("/api/patients/{patient_id}/medications/{medication_id}")
def remove_medication(patient_id: str, medication_id: int, request: Request):
    _store(request).remove_medication(patient_id, medication_id)
    return _store(request).medication_plan(patient_id)


@router.post("/api/patients/{patient_id}/medications/{medication_id}/doses")
def log_dose(patient_id: str, medication_id: int, dose: Dose, request: Request):
    day = (dose.day or date.today()).isoformat()
    if not _store(request).log_dose(patient_id, medication_id, day, dose.slot, dose.taken):
        raise HTTPException(404, "Medication not found")
    return _store(request).medication_plan(patient_id)


# ---------- daily check-in ----------

@router.post("/api/patients/{patient_id}/checkins")
def check_in(patient_id: str, checkin: CheckIn, request: Request):
    _profile_or_404(request, patient_id)
    _store(request).save_checkin(patient_id, today(), checkin.mood, checkin.activity_minutes,
                                 checkin.water_glasses, list(checkin.symptoms))
    return {"urgent": bool({"chest_pain", "shortness_of_breath", "fainting"} & set(checkin.symptoms))}

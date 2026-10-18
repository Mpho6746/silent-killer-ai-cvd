"""
SKAI web API. Serves the web app (app/web/) and the JSON endpoints it calls.

    python run_app.py            # starts this on http://127.0.0.1:8000
    API docs: http://127.0.0.1:8000/docs

Endpoints
    POST /api/clinical/assess        clinical risk (+ optional save to a patient)
    POST /api/lifestyle/assess       lifestyle self-check (+ optional save)
    GET  /api/patients               triage queue with trends and alerts
    GET  /api/patients/{id}          one patient with full assessment history
    GET  /api/assessments/{id}/report.pdf   downloadable PDF summary
    GET  /api/model-info             model performance + bias audit results
    Patient health-tracker endpoints (documents, self-check, medications,
    check-ins): see app/patient_api.py
    Clinician sign-in, sessions and audit log: see app/auth.py

Clinician endpoints (clinical assessment, triage queue, patient records,
document review) need a signed-in clinician: header "Authorization: Bearer <token>".
"""
import asyncio
import json
import os
import warnings
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from app import demo_data
from app.auth import DEMO_PASSWORD, hash_password, optional_clinician, require_clinician, seed_clinicians
from app.facilities import allowed, require, require_visible, seed_facilities, visible_patient_ids
from app.facilities import router as facilities_router
from app.triage import router as triage_router
from app.triage import seed_waiting_room
from app.care import router as care_router
from app.care import seed_care
from app.messaging import router as messaging_router
from app.messaging import dispatcher_loop
from app.interop import router as interop_router
from app.privacy import router as privacy_router
from app.patient_email import router as patient_email_router
from app.monitoring import router as monitoring_router
from app.monitoring import seed_outcomes
from app.auth import router as auth_router
from app.patient_api import router as patient_router
from app.patient_auth import router as patient_auth_router
from app.patient_auth import seed_patient_accounts, session_patient_id
from app.report import build_pdf
from app.risk_models import ClinicalModel, LifestyleModel
from app.storage import DEFAULT_DB, Store
from src.evaluate import classification_metrics
from src.preprocessing import ROOT

# scikit-learn warns about feature names on every SHAP call; the inputs are correct
warnings.filterwarnings("ignore", category=UserWarning)

WEB_DIR = Path(__file__).parent / "web"
REPORT_DIR = ROOT / "reports" / "multi_dataset"


# ---------- request schemas (ranges double as input validation) ----------

class ClinicalInputs(BaseModel):
    age: int = Field(ge=18, le=100)
    sex: Literal[0, 1]
    cp: int = Field(ge=0, le=3, description="Chest pain type")
    trestbps: int = Field(ge=70, le=250, description="Resting blood pressure, mmHg")
    chol: int = Field(ge=100, le=600, description="Serum cholesterol, mg/dL")
    fbs: Literal[0, 1] = Field(description="Fasting blood sugar > 120 mg/dL")
    restecg: int = Field(ge=0, le=2)
    thalach: int = Field(ge=60, le=220, description="Max heart rate achieved")
    exang: Literal[0, 1] = Field(description="Exercise-induced angina")
    oldpeak: float = Field(ge=0, le=10)
    slope: int = Field(ge=0, le=2)
    ca: int = Field(ge=0, le=4)
    thal: int = Field(ge=0, le=3)


class LifestyleInputs(BaseModel):
    age: int = Field(ge=18, le=100)
    sbp: int = Field(ge=80, le=250, description="Systolic blood pressure, mmHg")
    cigarettes_per_day: float = Field(0, ge=0, le=80)
    years_smoked: float = Field(0, ge=0, le=80)
    family_history: bool = False
    height_cm: Optional[float] = Field(None, ge=100, le=230)
    weight_kg: Optional[float] = Field(None, ge=30, le=300)
    bmi: Optional[float] = Field(None, ge=12, le=70)
    ldl: Optional[float] = Field(None, ge=0.5, le=20, description="LDL cholesterol, mmol/L")
    alcohol: Optional[float] = Field(None, ge=0, le=200)
    adiposity: Optional[float] = Field(None, ge=5, le=60)
    typea: Optional[float] = Field(None, ge=0, le=100)

    @model_validator(mode="after")
    def smoking_fits_age(self):
        if self.years_smoked > self.age:
            raise ValueError("years_smoked cannot be more than age")
        return self


class PatientRef(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    sex: Optional[Literal["M", "F"]] = None


class ClinicalRequest(BaseModel):
    inputs: ClinicalInputs
    patient: Optional[PatientRef] = None   # given -> the assessment is saved


class LifestyleRequest(BaseModel):
    inputs: LifestyleInputs
    patient: Optional[PatientRef] = None


# ---------- app setup ----------

@asynccontextmanager
async def lifespan(app):
    app.state.clinical = ClinicalModel()
    app.state.lifestyle = LifestyleModel()
    app.state.store = Store(Path(os.environ.get("SKAI_DB", DEFAULT_DB)))
    demo = os.environ.get("SKAI_DEMO", "1") == "1"
    if demo:
        seed_clinicians(app.state.store)       # also adds accounts to databases created before sign-in existed
    if app.state.store.is_empty() and demo:
        demo_data.seed(app.state.store, app.state.clinical, app.state.lifestyle)
    if demo:
        seed_patient_accounts(app.state.store)
        seed_facilities(app.state.store, hash_password(DEMO_PASSWORD))
        seed_waiting_room(app.state.store)
        seed_care(app.state.store)
        seed_outcomes(app.state.store)
    app.state.model_info = _model_info(app.state.clinical)
    # Sends due SMS/WhatsApp reminders every minute (see app/messaging.py)
    dispatcher = asyncio.create_task(dispatcher_loop(app.state.store))
    yield
    dispatcher.cancel()


app = FastAPI(title="SKAI - Silent Killer AI", version="2.0", lifespan=lifespan)

# Lets the page also be opened from another local server (e.g. VS Code Live
# Server on :5500) while this API answers on :8000. Local origins only.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def always_fresh_app_files(request, call_next):
    """
    Browsers must check for a newer index.html / *.js / *.css on every visit
    (an unchanged file costs a tiny "304 Not Modified"). Otherwise, after an
    update, a browser can mix a new page with its old saved scripts.
    """
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
def health():
    return {"status": "ok"}


def _assess(kind, request, clinician):
    model = app.state.clinical if kind == "clinical" else app.state.lifestyle
    inputs = request.inputs.model_dump(exclude_none=True)
    result = model.assess(inputs)
    if request.patient:
        # Saving to a patient record is a clinical action: it must be signed, allowed and attributable
        if not clinician:
            raise HTTPException(401, detail={"code": "not_signed_in"})
        if not allowed(clinician, "assess"):
            raise HTTPException(403, detail={"code": "role_not_allowed"})
        store = app.state.store
        if store.get_profile(request.patient.id):
            require_visible(store, clinician, request.patient.id)
        else:
            # new patients are registered at the clinician's facility
            store.upsert_patient(request.patient.id, request.patient.name, request.patient.sex)
            store.conn.execute("UPDATE patients SET facility_id = ? WHERE id = ?", (clinician.get("facility_id"), request.patient.id))
        store.upsert_patient(request.patient.id, request.patient.name, request.patient.sex)
        result["assessment_id"] = store.add_assessment(
            request.patient.id, kind, inputs, result["probability"], result["tier"], created_by=clinician["id"])
        store.log(clinician["id"], "assess", request.patient.id)
    return result


@app.post("/api/clinical/assess")
def assess_clinical(request: ClinicalRequest, clinician=Depends(require_clinician)):
    return _assess("clinical", request, clinician)


@app.post("/api/lifestyle/assess")
def assess_lifestyle(request: LifestyleRequest, clinician=Depends(optional_clinician)):
    # Without a patient this is a stateless what-if (the patient app's sliders use it)
    return _assess("lifestyle", request, clinician)


@app.get("/api/patients")
def patients(clinician=Depends(require_clinician)):
    store = app.state.store
    visible = visible_patient_ids(store, clinician)
    queue = [q for q in store.triage_queue() if visible is None or q["id"] in visible]
    overdue = {r[0] for r in store.conn.execute(
        "SELECT patient_id FROM referrals WHERE status = 'sent' AND due_by < ?",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),))}
    for q in queue:
        if q["id"] in overdue:
            q["flags"].append("referral_overdue")
    queue.sort(key=lambda q: (-len(q["flags"]), -q["latest"]["probability"]))
    return queue


@app.get("/api/patients/{patient_id}")
def patient(patient_id: str, clinician=Depends(require_clinician)):
    p = app.state.store.get_patient(patient_id)
    if not p:
        raise HTTPException(404, "Patient not found")
    require_visible(app.state.store, clinician, patient_id)
    app.state.store.log(clinician["id"], "view_patient", patient_id)
    return p


@app.get("/api/assessments/{assessment_id}/report.pdf")
def report(assessment_id: int, clinician=Depends(optional_clinician), patient_id=Depends(session_patient_id)):
    store = app.state.store
    assessment = store.get_assessment(assessment_id)
    if not assessment:
        raise HTTPException(404, "Assessment not found")
    # Medical reports: a signed-in clinician, or the patient the report belongs to
    if not clinician and patient_id != assessment["patient_id"]:
        raise HTTPException(401 if not patient_id else 403, detail={"code": "not_your_record"})
    if clinician:
        require_visible(store, clinician, assessment["patient_id"])
    patient = store.get_patient(assessment["patient_id"])
    model = app.state.clinical if assessment["kind"] == "clinical" else app.state.lifestyle
    # Re-running the model on the stored inputs reproduces the original result exactly
    pdf = build_pdf(patient, assessment, model.assess(assessment["inputs"]))
    filename = f"SKAI_{patient['id']}_{assessment['created_at'][:10]}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/model-info")
def model_info():
    return app.state.model_info


app.include_router(patient_router)
app.include_router(auth_router)
app.include_router(patient_auth_router)
app.include_router(facilities_router)
app.include_router(triage_router)
app.include_router(care_router)
app.include_router(messaging_router)
app.include_router(interop_router)
app.include_router(privacy_router)
app.include_router(patient_email_router)
app.include_router(monitoring_router)


# The web app (index.html, app.js, i18n.js) at the site root. Mounted last so
# the /api routes above take priority.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def _model_info(clinical):
    """Performance of both deployed models plus the bias-audit tables from src/bias_audit.py."""
    test = pd.read_csv(ROOT / "data" / "processed" / "heart_test.csv")
    probs = [clinical.predict(r) for r in test.drop(columns="target").to_dict(orient="records")]
    sa_meta = json.loads((ROOT / "models" / "sa_heart" / "metadata.json").read_text())

    def csv(name):
        path = REPORT_DIR / name
        return pd.read_csv(path).round(3).to_dict(orient="records") if path.exists() else []

    profiles_path = REPORT_DIR / "dataset_profiles.json"
    return {
        "clinical": {
            "dataset": "UCI Cleveland Heart Disease (303 patients, US hospital referrals)",
            "model": "Random Forest (tuned for recall)",
            "test_metrics": {k: round(v, 3) for k, v in classification_metrics(test["target"], probs).items()},
        },
        "lifestyle": {
            "dataset": "South African Heart Disease study (462 men, Western Cape)",
            "model": "Logistic Regression",
            "test_metrics": sa_meta["test_metrics"]["logistic_regression"],
        },
        "profiles": json.loads(profiles_path.read_text()) if profiles_path.exists() else {},
        "subgroups": {
            "uci_sex": csv("subgroups_uci_heart_sex.csv"),
            "uci_age": csv("subgroups_uci_heart_age_band.csv"),
            "sa_age": csv("subgroups_sa_heart_age_band.csv"),
            "sa_family_history": csv("subgroups_sa_heart_family_history.csv"),
        },
        "transfer": csv("cross_dataset_transfer.csv"),
    }

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
"""
import json
import os
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from app import demo_data
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
    if app.state.store.is_empty() and os.environ.get("SKAI_DEMO", "1") == "1":
        demo_data.seed(app.state.store, app.state.clinical, app.state.lifestyle)
    app.state.model_info = _model_info(app.state.clinical)
    yield


app = FastAPI(title="SKAI - Silent Killer AI", version="2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


def _assess(kind, request):
    model = app.state.clinical if kind == "clinical" else app.state.lifestyle
    inputs = request.inputs.model_dump(exclude_none=True)
    result = model.assess(inputs)
    if request.patient:
        store = app.state.store
        store.upsert_patient(request.patient.id, request.patient.name, request.patient.sex)
        result["assessment_id"] = store.add_assessment(
            request.patient.id, kind, inputs, result["probability"], result["tier"])
    return result


@app.post("/api/clinical/assess")
def assess_clinical(request: ClinicalRequest):
    return _assess("clinical", request)


@app.post("/api/lifestyle/assess")
def assess_lifestyle(request: LifestyleRequest):
    return _assess("lifestyle", request)


@app.get("/api/patients")
def patients():
    return app.state.store.triage_queue()


@app.get("/api/patients/{patient_id}")
def patient(patient_id: str):
    p = app.state.store.get_patient(patient_id)
    if not p:
        raise HTTPException(404, "Patient not found")
    return p


@app.get("/api/assessments/{assessment_id}/report.pdf")
def report(assessment_id: int):
    store = app.state.store
    assessment = store.get_assessment(assessment_id)
    if not assessment:
        raise HTTPException(404, "Assessment not found")
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

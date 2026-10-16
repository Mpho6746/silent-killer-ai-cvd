"""
Builds the downloadable PDF summary for one assessment.

The report is in English (a clinical document for the patient's file); the
app's on-screen text follows the chosen language.
"""
from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FEATURE_LABELS = {
    # clinical (UCI)
    "age": "Age (years)", "sex": "Sex (1 = male)", "cp": "Chest pain type (0-3)",
    "trestbps": "Resting blood pressure (mmHg)", "chol": "Cholesterol (mg/dL)",
    "fbs": "Fasting blood sugar > 120 mg/dL", "restecg": "Resting ECG (0-2)",
    "thalach": "Max heart rate (bpm)", "exang": "Exercise-induced angina",
    "oldpeak": "ST depression (oldpeak)", "slope": "ST slope (0-2)",
    "ca": "Major vessels on fluoroscopy (0-4)", "thal": "Thallium stress test (0-3)",
    "hr_reserve": "Heart-rate reserve shortfall", "bp_risk_flag": "BP >= 130 mmHg",
    # lifestyle (SAHeart)
    "sbp": "Systolic blood pressure (mmHg)", "tobacco": "Lifetime tobacco (kg)",
    "ldl": "LDL cholesterol (mmol/L)", "adiposity": "Adiposity", "famhist": "Family history of heart disease",
    "typea": "Type-A behaviour score", "obesity": "BMI (kg/m2)", "alcohol": "Alcohol (study units)",
    "smoker": "Smoker", "ldl_high": "LDL >= 4.1 mmol/L", "obese": "BMI >= 30",
    # lifestyle form inputs
    "cigarettes_per_day": "Cigarettes per day", "years_smoked": "Years smoked",
    "family_history": "Family history of heart disease", "height_cm": "Height (cm)",
    "weight_kg": "Weight (kg)", "bmi": "BMI (kg/m2)",
}

ACTION_LABELS = {
    "lower_bp": "Lower blood pressure to 120 mmHg",
    "lower_cholesterol": "Lower total cholesterol to 200 mg/dL",
    "control_sugar": "Bring fasting blood sugar under 120 mg/dL",
    "quit_smoking": "Stop smoking",
    "lower_ldl": "Lower LDL cholesterol to 3.0 mmol/L",
    "healthy_weight": "Reach a BMI of 25",
    "all_changes": "All of the above together",
}

TIER_COLOURS = {"low": "#10b981", "moderate": "#eab308", "high": "#f97316", "critical": "#ef4444"}

MODEL_NAMES = {
    "clinical": "Clinical assessment - Random Forest trained on UCI Cleveland hospital data",
    "lifestyle": "Lifestyle self-check - Logistic Regression trained on South African Heart Disease study data",
}


def _table(rows, widths):
    t = Table(rows, colWidths=widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def build_pdf(patient, assessment, result):
    styles = getSampleStyleSheet()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"SKAI report - {patient['name']}")
    when = datetime.fromisoformat(assessment["created_at"]).strftime("%d %B %Y, %H:%M UTC")
    tier = result["tier"]
    story = [
        Paragraph("<b>SKAI - Silent Killer AI</b>", styles["Title"]),
        Paragraph("Cardiovascular risk assessment summary", styles["Heading3"]),
        Spacer(1, 4 * mm),
        _table([["Patient", "Patient ID", "Assessed"],
                [patient["name"], patient["id"], when]], [60 * mm, 45 * mm, 69 * mm]),
        Spacer(1, 5 * mm),
        Paragraph(f'Estimated risk: <font color="{TIER_COLOURS[tier]}"><b>{result["probability"] * 100:.1f}% '
                  f'({tier.upper()})</b></font>', styles["Heading2"]),
        Paragraph(MODEL_NAMES[result["model"]], styles["Italic"]),
        Spacer(1, 4 * mm),
        Paragraph("<b>What is driving this result</b>", styles["Heading4"]),
    ]

    unit = "probability points" if result["impact_unit"] == "probability" else "log-odds"
    drivers = [["Factor", "Value", f"Effect on risk ({unit})"]]
    for d in result["drivers"][:8]:
        arrow = "raises" if d["impact"] > 0 else "lowers"
        drivers.append([FEATURE_LABELS.get(d["feature"], d["feature"]), _fmt(d["value"]), f"{arrow} ({d['impact']:+.3f})"])
    story += [_table(drivers, [80 * mm, 30 * mm, 64 * mm]), Spacer(1, 5 * mm)]

    if result["plan"]:
        if result["model"] == "lifestyle":
            proj = result["projection"]
            story.append(Paragraph(
                f"<b>Risk-reduction plan</b> - in {proj['years']} years, if nothing changes, the model estimates "
                f"{proj['probability_if_unchanged'] * 100:.1f}%. With each change:", styles["Heading4"]))
            rows = [["Change", f"Estimated risk in {proj['years']} years"]]
            rows += [[ACTION_LABELS[p["action"]], f"{p['probability_in_10y'] * 100:.1f}%"] for p in result["plan"]]
        else:
            story.append(Paragraph("<b>Risk-reduction plan</b> - the model re-scored with each change:", styles["Heading4"]))
            rows = [["Change", "Estimated risk"]]
            rows += [[ACTION_LABELS[p["action"]], f"{p['probability'] * 100:.1f}%"] for p in result["plan"]]
        story += [_table(rows, [110 * mm, 64 * mm]), Spacer(1, 5 * mm)]

    inputs = [["Measurement", "Value"]] + [[FEATURE_LABELS.get(k, k), _fmt(v)] for k, v in assessment["inputs"].items()]
    story += [Paragraph("<b>Measurements entered</b>", styles["Heading4"]), _table(inputs, [110 * mm, 64 * mm])]
    if result.get("estimated"):
        story.append(Paragraph("Not provided, study average used: " +
                               ", ".join(FEATURE_LABELS.get(e, e) for e in result["estimated"]), styles["Italic"]))

    story += [Spacer(1, 6 * mm), Paragraph(
        "This is a screening aid produced by a machine-learning model trained on historical research data. "
        "It is not a diagnosis. Scores are most reliable for adults aged 45-65 and should be reviewed by a "
        "qualified clinician together with the full clinical picture.", styles["Italic"])]

    doc.build(story)
    return buf.getvalue()


def _fmt(value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)

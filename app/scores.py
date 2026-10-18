"""
Validated 10-year cardiovascular risk: the Framingham general CVD score
(D'Agostino et al., Circulation 2008), shown next to SKAI's machine-learning
estimate.

SKAI's models were trained on 1980s research data and still need local
validation; a published, externally validated score is what a clinic can rely
on today. Framingham-based risk is the basis of the South African lipid
guideline's risk charts. Two versions:

  lab   uses total and HDL cholesterol (when the patient's document has both)
  bmi   the office version: BMI instead of cholesterol (no blood test needed)

Valid for ages 30-74. Coefficients are from the paper; both of its worked
examples are reproduced exactly in tests/test_practice.py.
"""
import math

MG_PER_MMOL = 38.67
COEF = {
    ("lab", "F"): dict(age=2.32888, tc=1.20904, hdl=-0.70833, sbp_u=2.76157, sbp_t=2.82263, smk=0.52873, dm=0.69154, s0=0.95012, mean=26.1931),
    ("lab", "M"): dict(age=3.06117, tc=1.12370, hdl=-0.93263, sbp_u=1.93303, sbp_t=1.99881, smk=0.65451, dm=0.57367, s0=0.88936, mean=23.9802),
    ("bmi", "F"): dict(age=2.72107, bmi=0.51125, sbp_u=2.81291, sbp_t=2.88267, smk=0.61868, dm=0.77763, s0=0.94833, mean=26.0145),
    ("bmi", "M"): dict(age=3.11296, bmi=0.79277, sbp_u=1.85508, sbp_t=1.92672, smk=0.70953, dm=0.53160, s0=0.88431, mean=23.9388),
}


def category(risk):
    return "low" if risk < 0.10 else "intermediate" if risk < 0.20 else "high"


def framingham(sex, age, sbp, bp_treated, smoker, diabetic, tc_mg=None, hdl_mg=None, bmi=None):
    """10-year risk of a cardiovascular event, or None with a reason when it can't be calculated."""
    if sex not in ("M", "F"):
        return {"available": False, "reason": "sex_unknown"}
    if not 30 <= age <= 74:
        return {"available": False, "reason": "age_outside_30_74"}
    version = "lab" if tc_mg and hdl_mg else "bmi" if bmi else None
    if not version:
        return {"available": False, "reason": "needs_cholesterol_or_bmi"}
    c = COEF[(version, sex)]
    x = c["age"] * math.log(age) + (c["sbp_t"] if bp_treated else c["sbp_u"]) * math.log(sbp)
    x += c["smk"] * bool(smoker) + c["dm"] * bool(diabetic)
    x += c["tc"] * math.log(tc_mg) + c["hdl"] * math.log(hdl_mg) if version == "lab" else c["bmi"] * math.log(bmi)
    risk = 1 - c["s0"] ** math.exp(x - c["mean"])
    return {"available": True, "name": "Framingham 2008", "version": version,
            "risk_10y": round(risk, 4), "category": category(risk)}


def for_patient(profile_sex, inputs, vitals):
    """Framingham from a patient's self-check inputs and document vitals (cholesterol in mmol/L)."""
    bmi = None
    if vitals.get("height_cm") and vitals.get("weight_kg"):
        bmi = vitals["weight_kg"] / (vitals["height_cm"] / 100) ** 2
    tc, hdl = vitals.get("total_cholesterol"), vitals.get("hdl")
    diabetic = inputs.get("diabetes", False) or (vitals.get("glucose") or 0) >= 7.0
    return framingham(profile_sex, inputs["age"], inputs["sbp"], inputs.get("bp_treated", False),
                      inputs.get("cigarettes_per_day", 0) > 0, diabetic,
                      tc * MG_PER_MMOL if tc else None, hdl * MG_PER_MMOL if hdl else None, bmi)

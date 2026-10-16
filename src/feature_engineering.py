"""
Engineered features for each dataset, plus helpers shared by both.

Thresholds come from published clinical guidelines rather than from the data,
so the model gets clinically meaningful cut-offs instead of ones tuned to this
small sample.
"""
import pandas as pd

AGE_BANDS = [0, 45, 60, 200]
AGE_LABELS = ["<45", "45-59", "60+"]


def age_band(age):
    """Bucket ages into the bands used for the bias audit."""
    return pd.cut(age, bins=AGE_BANDS, labels=AGE_LABELS, right=False).astype(str)


def bp_risk_flag(systolic_bp):
    """1 if systolic BP is at or above AHA Stage 1 Hypertension (130 mmHg)."""
    return (systolic_bp >= 130).astype(int)


def add_uci_features(df):
    """Same two features as 01_preprocessing.ipynb, so models stay comparable with the app."""
    df = df.copy()
    # How far the patient fell short of their age-predicted max heart rate
    df["hr_reserve"] = (220 - df["age"]) - df["thalach"]
    df["bp_risk_flag"] = bp_risk_flag(df["trestbps"])
    return df


def add_sa_features(df):
    """
    SAHeart has lifestyle measurements the UCI data lacks (tobacco, alcohol,
    BMI, LDL), so its engineered features are flags on those:
      - bp_risk_flag: systolic BP >= 130 mmHg (AHA Stage 1)
      - smoker: any cumulative tobacco use (kg) - separates never-smokers from the rest
      - ldl_high: LDL >= 4.1 mmol/L, the NCEP ATP III "high" threshold (160 mg/dL)
      - obese: BMI >= 30, the WHO obesity threshold (`obesity` column is BMI)
    """
    df = df.copy()
    df["bp_risk_flag"] = bp_risk_flag(df["sbp"])
    df["smoker"] = (df["tobacco"] > 0).astype(int)
    df["ldl_high"] = (df["ldl"] >= 4.1).astype(int)
    df["obese"] = (df["obesity"] >= 30).astype(int)
    return df

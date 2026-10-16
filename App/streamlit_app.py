import streamlit as st
import pandas as pd
import joblib
import shap
import matplotlib.pyplot as plt
from pathlib import Path
from utils import calculate_risk_tier, process_patient_data
from translations import LANGUAGES, RISK_TIER_KEYS, get_translator

# --- 1. LOAD ARTIFACTS ---
@st.cache_resource
def load_assets():
    # Automatically resolves the path to the root 'models' folder
    root_dir = Path(__file__).parent.parent
    rf = joblib.load(root_dir / 'models' / 'rf_model.pkl')
    scaler = joblib.load(root_dir / 'models' / 'scaler.pkl')
    return rf, scaler

model, scaler = load_assets()

# These are the exact features from your heart dataset, plus the engineered
# hr_reserve and bp_risk_flag features (see 01_preprocessing.ipynb) - order
# matches training data
feature_names = ['age', 'sex', 'cp', 'trestbps', 'chol', 'fbs', 'restecg',
                 'thalach', 'exang', 'oldpeak', 'slope', 'ca', 'thal',
                 'hr_reserve', 'bp_risk_flag']

# --- 2. BUILD THE UI ---
# Language picker: English plus South Africa's nine official indigenous languages
language = st.sidebar.selectbox("🌍 Language / Ulimi / Puo", list(LANGUAGES.keys()))
t = get_translator(LANGUAGES[language])

# Option labels for binary inputs - the stored values stay 0/1 for the model
sex_labels = {1: t("male"), 0: t("female")}
yes_no_labels = {1: t("yes"), 0: t("no")}

st.title(t("title"))
st.markdown(t("subtitle"))

# Create columns for the input form to make it look clean
col1, col2, col3 = st.columns(3)

with col1:
    age = st.number_input(t("age"), min_value=1, max_value=120, value=50)
    sex = st.selectbox(t("sex"), [1, 0], format_func=sex_labels.get)
    cp = st.selectbox(t("cp"), [0, 1, 2, 3])
    trestbps = st.number_input(t("trestbps"), min_value=50, max_value=250, value=120)
    chol = st.number_input(t("chol"), min_value=100, max_value=600, value=200)

with col2:
    fbs = st.selectbox(t("fbs"), [0, 1], format_func=yes_no_labels.get)
    restecg = st.selectbox(t("restecg"), [0, 1, 2])
    thalach = st.number_input(t("thalach"), min_value=50, max_value=220, value=150)
    exang = st.selectbox(t("exang"), [0, 1], format_func=yes_no_labels.get)

with col3:
    oldpeak = st.number_input(t("oldpeak"), min_value=0.0, max_value=10.0, value=1.0, step=0.1)
    slope = st.selectbox(t("slope"), [0, 1, 2])
    ca = st.selectbox(t("ca"), [0, 1, 2, 3, 4])
    thal = st.selectbox(t("thal"), [0, 1, 2, 3])

# --- 3. PREDICTION & RISK LOGIC ---
if st.button(t("assess")):
    # Gather inputs into a dictionary
    patient_data = {
        'age': age, 'sex': sex, 'cp': cp, 'trestbps': trestbps, 'chol': chol,
        'fbs': fbs, 'restecg': restecg, 'thalach': thalach, 'exang': exang,
        'oldpeak': oldpeak, 'slope': slope, 'ca': ca, 'thal': thal,
        # Same derived features used in training - see 01_preprocessing.ipynb
        'hr_reserve': (220 - age) - thalach,
        'bp_risk_flag': int(trestbps >= 130),
    }
    
    # Process and scale using Sfiso's logic
    processed_df = process_patient_data(patient_data, scaler, feature_names)

    # Probability of heart disease. The raw heart.csv labels disease as 0;
    # 01_preprocessing.ipynb flips it so the model's class 1 = heart disease.
    probability = model.predict_proba(processed_df)[0][1]

    # Get Risk Tier
    risk_label, icon = calculate_risk_tier(probability)
    risk_label = t(RISK_TIER_KEYS[risk_label])

    # Display Results
    st.divider()
    st.header(f"{t('result')}: {risk_label} {icon}")
    st.subheader(f"{t('probability')}: {probability * 100:.1f}%")

    # --- 4. EXPLAINABILITY (WHAT-IF & SHAP) ---
    st.markdown(f"### {t('why')}")
    
    # Generate SHAP Waterfall plot
    explainer = shap.Explainer(model)
    shap_values = explainer(processed_df)

    fig, ax = plt.subplots(figsize=(10, 4))
    # Class 1 (heart disease), matching the probability above
    shap.plots.waterfall(shap_values[0, :, 1], show=False)
    st.pyplot(fig)
import streamlit as st
import pandas as pd
import joblib
import shap
import matplotlib.pyplot as plt
from pathlib import Path
from utils import calculate_risk_tier, process_patient_data

# --- 1. LOAD ARTIFACTS ---
@st.cache_resource
def load_assets():
    # Automatically resolves the path to the root 'models' folder
    root_dir = Path(__file__).parent.parent
    rf = joblib.load(root_dir / 'models' / 'rf_model.pkl')
    scaler = joblib.load(root_dir / 'models' / 'scaler.pkl')
    return rf, scaler

model, scaler = load_assets()

# These are the exact features from your heart dataset
feature_names = ['age', 'sex', 'cp', 'trestbps', 'chol', 'fbs', 'restecg', 
                 'thalach', 'exang', 'oldpeak', 'slope', 'ca', 'thal']

# --- 2. BUILD THE UI ---
st.title("Silent Killer AI: Cardiovascular Risk Assessment")
st.markdown("Enter patient clinical data to predict cardiovascular risk and generate SHAP explanations.")

# Create columns for the input form to make it look clean
col1, col2, col3 = st.columns(3)

with col1:
    age = st.number_input("Age", min_value=1, max_value=120, value=50)
    sex = st.selectbox("Sex (1=Male, 0=Female)", [1, 0])
    cp = st.selectbox("Chest Pain Type (0-3)", [0, 1, 2, 3])
    trestbps = st.number_input("Resting Blood Pressure", min_value=50, max_value=250, value=120)
    chol = st.number_input("Cholesterol", min_value=100, max_value=600, value=200)

with col2:
    fbs = st.selectbox("Fasting Blood Sugar > 120 (1=True, 0=False)", [0, 1])
    restecg = st.selectbox("Resting ECG (0-2)", [0, 1, 2])
    thalach = st.number_input("Max Heart Rate Achieved", min_value=50, max_value=220, value=150)
    exang = st.selectbox("Exercise Induced Angina (1=Yes, 0=No)", [0, 1])

with col3:
    oldpeak = st.number_input("ST Depression (Oldpeak)", min_value=0.0, max_value=10.0, value=1.0, step=0.1)
    slope = st.selectbox("Slope of Peak Exercise ST Segment (0-2)", [0, 1, 2])
    ca = st.selectbox("Number of Major Vessels (0-4)", [0, 1, 2, 3, 4])
    thal = st.selectbox("Thalassemia (0-3)", [0, 1, 2, 3])

# --- 3. PREDICTION & RISK LOGIC ---
if st.button("Assess Patient Risk"):
    # Gather inputs into a dictionary
    patient_data = {
        'age': age, 'sex': sex, 'cp': cp, 'trestbps': trestbps, 'chol': chol,
        'fbs': fbs, 'restecg': restecg, 'thalach': thalach, 'exang': exang,
        'oldpeak': oldpeak, 'slope': slope, 'ca': ca, 'thal': thal
    }
    
    # Process and scale using Sfiso's logic
    processed_df = process_patient_data(patient_data, scaler, feature_names)
    
    # Get Probability from the Random Forest model
    probability = model.predict_proba(processed_df)[0][1]
    
    # Get Risk Tier
    risk_label, icon = calculate_risk_tier(probability)
    
    # Display Results
    st.divider()
    st.header(f"Assessment Result: {risk_label} {icon}")
    st.subheader(f"Calculated Probability: {probability * 100:.1f}%")
    
    # --- 4. EXPLAINABILITY (WHAT-IF & SHAP) ---
    st.markdown("### Why did the model make this decision?")
    
    # Generate SHAP Waterfall plot
    explainer = shap.Explainer(model)
    shap_values = explainer(processed_df)
    
    fig, ax = plt.subplots(figsize=(10, 4))
    shap.plots.waterfall(shap_values[0, :, 1], show=False)
    st.pyplot(fig)
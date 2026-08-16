import pandas as pd

def calculate_risk_tier(probability):
    """
    Converts a raw machine learning probability into a clinical risk category.
    """
    if probability < 0.30:
        return "Low Risk", "🟢"
    elif probability < 0.60:
        return "Moderate Risk", "🟡"
    elif probability < 0.85:
        return "High Risk", "🟠"
    else:
        return "Critical Risk", "🔴"

def process_patient_data(patient_dict, scaler, feature_names):
    """
    Converts Streamlit user inputs into a scaled dataframe ready for the model.
    """
    # Convert dictionary to DataFrame
    df = pd.DataFrame([patient_dict])
    
    # Ensure columns match the exact order the model was trained on
    df = df[feature_names]
    
    # Scale the data
    scaled_data = scaler.transform(df)
    scaled_df = pd.DataFrame(scaled_data, columns=feature_names)
    
    return scaled_df
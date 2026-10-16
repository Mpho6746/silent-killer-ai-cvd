# Silent Killer AI: Cardiovascular Risk Assessment

AI-driven proactive cardiovascular risk assessment. Heart disease is often called the "silent killer" because clinically significant risk can build up with no obvious symptoms until an acute event occurs. This project trains and compares machine learning models on standard clinical measurements (age, blood pressure, cholesterol, ECG results, etc.) to flag a patient's cardiovascular risk early, and ships that model behind an interactive Streamlit app that also explains *why* it made a given prediction.

## Problem Statement

Given a set of routinely-collected clinical measurements for a patient, predict the probability that they have heart disease, in a way that:

- prioritizes **not missing at-risk patients** (a false negative — telling an at-risk patient they're fine — is far more costly in this setting than a false positive that just triggers a follow-up test), and
- **explains its reasoning** per-patient, so a clinician isn't asked to trust a black box.

## Methodology Summary

The pipeline is a sequence of five notebooks, each consuming the previous stage's output:

1. **[01_preprocessing.ipynb](notebooks/01_preprocessing.ipynb)** — loads the raw dataset, flips the label so `target=1` means heart disease (the raw export encodes disease as 0), makes and justifies an explicit outlier-handling decision (see below), engineers two features (`hr_reserve`, `bp_risk_flag`), splits into train/held-out-test (80/20, stratified), fits a `StandardScaler` on the training split only, and saves the scaled training data, the raw held-out test data, and the scaler.
2. **[02_eda.ipynb](notebooks/02_eda.ipynb)** — exploratory analysis: dataset overview, target balance, a correlation heatmap against `target`, and distribution/relationship plots for age, sex, chest pain type, cholesterol, resting blood pressure, and max heart rate.
3. **[03_training.ipynb](notebooks/03_training.ipynb)** — trains Logistic Regression, Random Forest, and XGBoost; compares all three with 5-fold `StratifiedKFold` cross-validation across accuracy/precision/recall/F1/ROC-AUC; then runs `GridSearchCV` (also `StratifiedKFold`, 5-fold) to tune the Random Forest, optimizing specifically for **recall**, and saves the tuned model.
4. **[04_evaluation.ipynb](notebooks/04_evaluation.ipynb)** — evaluates all three trained models on the genuinely held-out test set: per-model accuracy/precision/recall/F1, full classification reports, confusion matrices, ROC curves, and a consolidated "Results Summary" narrative walking through the whole project end-to-end.
5. **[05_xai.ipynb](notebooks/05_xai.ipynb)** — explainability via SHAP: a global beeswarm plot of feature importance, a per-patient waterfall plot, and a written analysis connecting the top SHAP features back to specific findings from the EDA.

The trained models power **SKAI**, a web app ([app/](app/)) for clinicians and patients, described below. A simpler Streamlit tool ([app/streamlit_app.py](app/streamlit_app.py)) is kept as a backup.

### Outlier Handling: We Chose to Keep Them

An IQR check flags ~20 outlier values across `trestbps` (9, up to 200 mmHg), `chol` (5, up to 564 mg/dL), and `oldpeak` (5, up to 6.2) out of 303 rows. We deliberately kept all of them rather than dropping or capping: they're clinically plausible extreme readings (a hypertensive crisis, severe hypercholesterolemia), not data errors — and in a risk-screening model, extreme readings are themselves strong risk signals, so removing them would mean suppressing signal for exactly the highest-risk patients. Dropping ~6.6% of an already-small (303-row) dataset for marginal benefit wasn't worth it either. Random Forest and XGBoost are inherently robust to outlier magnitude; `StandardScaler` tempers (without discarding) their leverage on Logistic Regression. Full reasoning in [01_preprocessing.ipynb](notebooks/01_preprocessing.ipynb).

### Feature Engineering

- **`hr_reserve` = `(220 - age) - thalach`** — `age` and `thalach` (max heart rate achieved) are each individually correlated with `target`, but neither alone captures whether a patient's heart rate response was *appropriate for their age* — a 65-year-old reaching 150 bpm and a 30-year-old reaching 150 bpm are cardiovascularly very different. A large positive value indicates the patient fell well short of their age-predicted max — a recognized marker of reduced cardiovascular fitness.
- **`bp_risk_flag` = 1 if `trestbps` ≥ 130 else 0** — the AHA's Stage 1 Hypertension threshold, given to the model explicitly rather than left for it to rediscover from a continuous column.

Being honest about the outcome (see [05_xai.ipynb](notebooks/05_xai.ipynb)'s SHAP analysis): `hr_reserve` added modest value (8th of 15 features by SHAP importance, above the raw `age` it's built from), while `bp_risk_flag` added almost none (14th of 15 — the model gets nothing from the binarized threshold it doesn't already get from continuous `trestbps`). Not every engineered feature earns its place, and we report that rather than only keeping the ones that "worked."

We didn't drop any of the original 13 features — every one is an established clinical risk factor, and the EDA correlation heatmap showed no pair correlated with each other strongly enough to suggest redundancy. We let SHAP (run on the trained model) make the actual importance call rather than pre-emptively guessing.

### Why These Three Models

- **Logistic Regression** — an interpretable, coefficient-readable baseline; with only ~300 rows and largely monotonic clinical risk factors, it's a genuinely competitive model, not just a strawman.
- **Random Forest** — captures non-linear relationships and feature interactions (e.g. how `hr_reserve` matters differently across age ranges) while staying relatively robust to the outliers we chose to keep.
- **XGBoost** — a stronger, regularized boosting alternative to benchmark Random Forest against.

All three are compared under identical cross-validation rather than picking one upfront — see [03_training.ipynb](notebooks/03_training.ipynb) for the full rationale.

### Why Recall as the Tuning Metric

In a cardiovascular screening tool, a **false negative** (telling an at-risk patient they're low-risk) is far more costly than a false positive — a missed diagnosis can mean a missed opportunity to intervene before a cardiac event, while a false alarm just costs a follow-up test. The Random Forest is therefore tuned via `GridSearchCV` scoring on **recall**, not accuracy or F1.

## Key Results

> For the full narrative connecting every finding below back to the data and the problem statement, see the **"Results Summary: From Data to Deployment"** section at the end of [04_evaluation.ipynb](notebooks/04_evaluation.ipynb) — this section is just the numbers.

**5-fold Stratified CV comparison** (on the training split, [03_training.ipynb](notebooks/03_training.ipynb)):

| Model | Accuracy | Precision | Recall | F1 | ROC-AUC |
|---|---|---|---|---|---|
| Logistic Regression | 0.805 | 0.829 | 0.736 | 0.768 | 0.896 |
| Random Forest | 0.822 | 0.838 | 0.782 | 0.801 | 0.909 |
| XGBoost | 0.806 | 0.805 | 0.764 | 0.781 | 0.894 |

**Held-out test set** (61 patients never seen during training/tuning, [04_evaluation.ipynb](notebooks/04_evaluation.ipynb)):

| Model | Accuracy | Precision | Recall | F1 |
|---|---|---|---|---|
| Logistic Regression | 0.852 | 0.852 | 0.821 | 0.836 |
| **Random Forest (tuned)** | 0.852 | 0.880 | **0.786** | 0.830 |
| XGBoost | 0.820 | 0.815 | 0.786 | 0.800 |

All recall/precision/F1 figures are for the **heart-disease** class. `GridSearchCV` best recall (5-fold CV): **0.809**. Random Forest is the deployed model because it had the best recall *and* ROC-AUC across cross-validation. On this 61-row test split Logistic Regression caught one more sick patient (23 vs 22 of 28), which is too small a difference to overturn the CV result. Missing 6 of 28 is still too many for screening: [06_multi_dataset_and_bias.ipynb](notebooks/06_multi_dataset_and_bias.ipynb) shows a 0.4 decision threshold lifts recall to about 0.86.

> **Correction:** earlier versions of this README reported recall 0.875 and tuned-recall 0.888. Those were measured on the *healthy* class, because the raw `heart.csv` encodes disease as `target=0` and scikit-learn scores class 1 by default. [01_preprocessing.ipynb](notebooks/01_preprocessing.ipynb) now flips the label, and all models were retrained.

**Top SHAP features** (what actually drives the model's high-risk predictions, [05_xai.ipynb](notebooks/05_xai.ipynb)): `thal` (thallium stress-test result), `ca` (number of major vessels), `cp` (chest pain type). `ca` and `cp` are among the five features the EDA's correlation heatmap flagged as most associated with the target, and `thal` is close behind, so the model's learned behavior lines up with the exploratory analysis.

## Multiple Datasets & Bias Audit

The system is no longer tied to one dataset. [src/datasets.py](src/datasets.py) is a **dataset registry**: each dataset is one entry naming its loader, engineered features, bias-audit groups and the features it shares with other datasets. Training and evaluation run on any registered dataset unchanged. Two are registered:

| Dataset | Who | Rows | Best model | CV ROC-AUC |
|---|---|---|---|---|
| UCI Cleveland ([data/raw/heart.csv](data/raw/heart.csv)) | US hospital patients referred for angiography, 68% male | 303 | Random Forest | 0.914 |
| South African Heart Disease ([data/raw/SAHeart.csv](data/raw/SAHeart.csv)) | Men from a rural Western Cape community, case-control sample | 462 | Logistic Regression | 0.743 |

The two datasets measure different things (hospital test results vs community risk factors), so each gets its own model. They share only age and systolic BP. A model trained on those two features in one population ranks risk about as well in the other, but its probabilities must be re-calibrated per population.

**Bias audit** (full analysis and bias statement in [06_multi_dataset_and_bias.ipynb](notebooks/06_multi_dataset_and_bias.ipynb)):
- **Age is the main bias.** Young patients with heart disease are missed most often (recall for under-45s: 0.64 UCI, 0.41 SAHeart), while over-60s get many false alarms. Lowering the threshold to 0.4 raises UCI under-45 recall to 0.86.
- **Family history:** SAHeart men without a known family history are under-detected (recall 0.53 vs 0.90).
- **Sex:** no measurable recall gap on UCI (women 0.79, men 0.75), but only 24 women in the data have disease, and SAHeart has no women at all.
- **Data bias:** both datasets are 1980s, single-region, referral/case-control samples with inflated disease rates.

> Every loader in `src/preprocessing.py` maps its dataset's label to `at_risk = 1` (heart disease), so recall always means "share of sick patients caught".

```bash
python -m src.train          # train LR / RF / XGBoost on every registered dataset -> models/<dataset>/
python -m src.bias_audit     # subgroup audit + cross-dataset test -> reports/multi_dataset/
```

## Limitations & Next Steps

This is a prototype trained on a single, static ~300-row dataset — see the "Scalability & Next Steps" section at the end of [04_evaluation.ipynb](notebooks/04_evaluation.ipynb) for a full discussion of what it would take to move toward something production-ready (more/broader data, scheduled retraining, drift monitoring).

## The SKAI App

A web app (FastAPI backend + HTML/JS front end) built on the trained models. Every number it shows comes from the models; nothing is simulated.

**Clinician dashboard**
- **Triage queue**: every patient's latest risk, sorted so the most urgent come first, with proactive alerts: *High risk*, *Rising* (risk up 10+ points since the last assessment) and *Follow-up due* (high-risk patient not re-assessed in 30 days).
- **Clinical assessment** with the tuned Random Forest: risk %, risk tier and a per-patient SHAP chart of what drives the result.
- **What-if sliders** (BP, cholesterol, max heart rate, ST depression) that re-run the model live.
- **Risk-reduction plan**: the model re-scored with each guideline target (BP 120, cholesterol 200, fasting sugar < 120). It only lists changes that actually lower the risk.
- **Risk over time** chart and a **downloadable PDF report** for every saved assessment.

**Patient self-check**: uses the South African Heart Disease model and only things a person can know without hospital tests (age, blood pressure, smoking, family history, height/weight, LDL if known).
- **10-year projection**: the risk if nothing changes vs. with each change (stop smoking, BP 120, LDL 3.0, BMI 25).
- Unknown values fall back to the study average, and inputs outside the study's range are capped. Both are shown to the user.

**For everyone**
- **SKAI advisor**: an offline assistant that explains *why* the risk is what it is, *how to lower it* and *when to see a doctor*, using the real model output.
- **Model & bias page**: held-out performance of both models and the bias-audit charts from notebook 06.
- **10 languages**: English plus all nine official indigenous South African languages. Translations need native-speaker review before use with real patients.

| Endpoint | Purpose |
|---|---|
| `POST /api/clinical/assess` | clinical risk + SHAP drivers + plan (saved if a patient is given) |
| `POST /api/lifestyle/assess` | lifestyle risk + 10-year projection + plan |
| `GET /api/patients` | triage queue with trends and alerts |
| `GET /api/patients/{id}` | one patient's full history |
| `GET /api/assessments/{id}/report.pdf` | PDF summary |
| `GET /api/model-info` | model metrics + bias audit tables |

## How to Run

**Requirements:** Python 3.11, packages in [requirements.txt](requirements.txt).

```bash
pip install -r requirements.txt
```

**Reproduce the full pipeline** (run in order — each notebook depends on the previous one's output):

```bash
jupyter nbconvert --to notebook --execute --inplace notebooks/01_preprocessing.ipynb
jupyter nbconvert --to notebook --execute --inplace notebooks/02_eda.ipynb
jupyter nbconvert --to notebook --execute --inplace notebooks/03_training.ipynb
jupyter nbconvert --to notebook --execute --inplace notebooks/04_evaluation.ipynb
jupyter nbconvert --to notebook --execute --inplace notebooks/05_xai.ipynb
```

Or open them in Jupyter/VS Code and run all cells in order.

**Launch the SKAI web app** (needs the models from the pipeline above; they are committed, so this works straight away):

```bash
python run_app.py
```

This opens http://127.0.0.1:8000. Interactive API docs are at `/docs`. On first run the app creates a local patient database (`data/app/skai.db`, not committed) and fills it with clearly-labelled `DEMO-` patients. Delete that file to start fresh.

**Run the tests:**

```bash
python -m pytest
```

**Backup Streamlit tool:**

```bash
streamlit run app/streamlit_app.py
```

## Project Structure

```
data/
  raw/heart.csv              raw clinical dataset (UCI Cleveland)
  raw/SAHeart.csv            South African Heart Disease dataset
  processed/heart_cleaned.csv   scaled training split (+ engineered hr_reserve, bp_risk_flag)
  processed/heart_test.csv      held-out test split (unscaled)
models/                      deployed clinical model (rf_model.pkl + scaler.pkl), per-dataset models in models/<dataset>/
notebooks/
  01_preprocessing.ipynb     cleaning, feature engineering, train/test split, scaling
  02_eda.ipynb                exploratory data analysis
  03_training.ipynb           model training, CV comparison, hyperparameter tuning
  04_evaluation.ipynb         held-out test evaluation, metrics, plots
  05_xai.ipynb                 SHAP explainability
  06_multi_dataset_and_bias.ipynb  second dataset, bias audit, bias statement
app/
  api.py                      FastAPI app: serves the web UI and the JSON API
  risk_models.py              both models: prediction, SHAP drivers, what-if, risk-reduction plans
  storage.py                  SQLite patients/assessments + triage queue and alerts
  report.py                   PDF report
  demo_data.py                demo patients seeded on first run
  web/                        index.html, app.js, i18n.js (10 languages)
  streamlit_app.py            backup Streamlit tool
  utils.py, translations.py   shared risk tiers; Streamlit translations
tests/                        API tests and translation completeness checks
run_app.py                    starts the web app
src/
  preprocessing.py            one loader per raw dataset, all mapped to at_risk = 1
  feature_engineering.py      per-dataset engineered features (clinical thresholds)
  datasets.py                 dataset registry - add new datasets here
  train.py                    trains the 3 models on any registered dataset
  evaluate.py                 metrics, subgroup bias report, cross-dataset transfer test
  bias_audit.py               runs the full multi-dataset + bias analysis
reports/multi_dataset/        tables and plots from the bias audit
reports/figures/              EDA figures
```

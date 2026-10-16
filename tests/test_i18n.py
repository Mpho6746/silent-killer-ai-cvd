"""
Every language must have every interface text the web app uses, so switching
language never shows a raw key or silently falls back to English.
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "app" / "web"
I18N = (WEB / "i18n.js").read_text(encoding="utf-8")
LANGS = ["en", "af", "zu", "xh", "nr", "nso", "st", "tn", "ss", "ve", "ts"]


def keys_for(lang):
    block = re.search(rf"\n    {lang}: \{{(.*?)\n    \}},", I18N, re.S).group(1)
    # keys sit at the start of a line or after a comma (not inside text like "the result:")
    return set(re.findall(r"(?:^|,)\s*(\w+):\s*[\"']", block, re.M))


def keys_used_by_ui():
    from app.advice import __file__ as advice_py
    from app.documents import RANGES, __file__ as documents_py
    from app.patient_api import SYMPTOMS

    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8") + (WEB / "patient.js").read_text(encoding="utf-8")
    used = set(re.findall(r'data-i18n(?:-placeholder|-title)?="(\w+)"', html))
    used |= set(re.findall(r"\bt\('(\w+)'", js))
    used |= {f"f_{k}" for k in re.findall(r"\{ key: '(\w+)'", js)}       # form field labels
    used |= {f"tier_{t}" for t in ["low", "moderate", "high", "critical"]}
    used |= {f"flag_{f}" for f in ["high_risk", "rising", "follow_up_due", "needs_verification"]}
    used |= {f"q_{q}" for q in ["why", "lower", "doctor"]}
    used |= {f"action_{a}" for a in ["lower_bp", "lower_cholesterol", "control_sugar", "quit_smoking",
                                     "lower_ldl", "healthy_weight", "all_changes"]}
    # Keys built from API values: advice, symptoms, document statuses/reasons/warnings, vitals
    advice_src = Path(advice_py).read_text(encoding="utf-8")
    used |= {f"advice_{k}" for k in re.findall(r'add\("(\w+)"', advice_src)} | {"advice_streak"}
    used |= {f"sym_{s}" for s in SYMPTOMS}
    statuses = ["verified", "approved", "pending_review", "rejected"]
    used |= {f"doc_{s}" for s in statuses} | {f"res_{s}" for s in statuses}
    documents_src = Path(documents_py).read_text(encoding="utf-8")
    used |= {f"reason_{r}" for r in re.findall(r'reasons(?:=\[|\.append\()"(\w+)"', documents_src)}
    used |= {f"warn_{w}" for w in re.findall(r'warnings(?:=\[|\.append\()"(\w+)"', documents_src)}
    used |= {f"v_{k}" for k in RANGES} | {"v_bp", "v_bmi"}
    return used


def test_all_languages_registered():
    assert re.findall(r"code: '(\w+)'", I18N) == LANGS


def test_every_language_has_every_english_key():
    english = keys_for("en")
    for lang in LANGS[1:]:
        missing = english - keys_for(lang)
        assert not missing, f"{lang} is missing {sorted(missing)}"


def test_ui_only_uses_defined_keys():
    undefined = keys_used_by_ui() - keys_for("en")
    assert not undefined, f"used but not defined: {sorted(undefined)}"


def test_model_features_have_labels():
    """Every feature the models can report as a driver needs a label."""
    from app.risk_models import ClinicalModel, LifestyleModel
    english = keys_for("en")
    for feature in ClinicalModel.FEATURES + LifestyleModel.FEATURES:
        assert f"f_{feature}" in english, feature

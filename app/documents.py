"""
Turns an uploaded clinical document into vitals the system can trust.

In a medical app the numbers behind a risk score must come from a measured
source, not from what a patient types. Patients therefore upload the PDF their
clinic, pharmacy clinic or lab gave them, and SKAI reads the vitals from it.

Trust levels
  verified        The PDF carries a signed data block from an issuer listed in
                  trusted_issuers.json and the Ed25519 signature matches. Values
                  come from the signed block, so editing the visible text of the
                  PDF changes nothing.
  pending_review  No signature, but vitals could be read from the text. Usable
                  for a provisional result; a clinician must confirm it.
  rejected        Invalid signature (tampered), unknown issuer, dated in the
                  future, wrong patient on a signed document, or nothing readable.

In production the signed block would be replaced by a standard PDF digital
signature (PAdES) or a direct lab/clinic integration (HL7 FHIR); the trust
model is the same: only values traceable to a trusted issuer are "verified".
"""
import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pypdf import PdfReader
from pypdf.errors import PdfReadError

ISSUERS_FILE = Path(__file__).parent / "trusted_issuers.json"
MAX_AGE_DAYS = 365          # older documents still count, but are flagged as outdated
MAX_BYTES = 5 * 1024 * 1024

# Plausible ranges; anything outside is treated as a misread and dropped
RANGES = {
    "sbp": (70, 260), "dbp": (40, 160), "heart_rate": (30, 220),
    "ldl": (0.5, 20), "hdl": (0.2, 5), "total_cholesterol": (1, 20), "glucose": (1, 40),
    "height_cm": (100, 230), "weight_kg": (30, 300),
}
MG_PER_MMOL = {"ldl": 38.67, "hdl": 38.67, "total_cholesterol": 38.67, "glucose": 18.0}


def payload_bytes(payload):
    """The exact bytes an issuer signs: canonical JSON."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def load_issuers(path=ISSUERS_FILE):
    if not path.exists():
        return {}
    return {i["id"]: i for i in json.loads(path.read_text(encoding="utf-8"))}


@dataclass
class DocumentResult:
    status: str                                   # verified | pending_review | rejected
    vitals: dict = field(default_factory=dict)
    issuer: str | None = None                     # issuer display name
    document_date: str | None = None              # ISO date
    patient_name: str | None = None
    reasons: list = field(default_factory=list)   # why rejected
    warnings: list = field(default_factory=list)  # accepted, but worth knowing

    def to_dict(self):
        return self.__dict__.copy()


def read_document(pdf_bytes, expected_name=None, today=None, issuers=None):
    """Read, verify and extract vitals from an uploaded PDF."""
    today = today or date.today()
    issuers = load_issuers() if issuers is None else issuers
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        meta = reader.metadata or {}
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, ValueError, KeyError):
        return DocumentResult("rejected", reasons=["not_a_pdf"])

    if "/SKAIPayload" in meta:
        result = _verify_signed(meta, issuers)
    else:
        result = _parse_text(text, issuers)

    if result.status != "rejected":
        _check_date(result, today)
        _check_name(result, expected_name)
    return result


def _verify_signed(meta, issuers):
    try:
        payload = json.loads(meta["/SKAIPayload"])
        signature = base64.b64decode(meta["/SKAISignature"])
    except (KeyError, ValueError):
        return DocumentResult("rejected", reasons=["signature_invalid"])

    issuer = issuers.get(payload.get("issuer"))
    if not issuer:
        return DocumentResult("rejected", reasons=["unknown_issuer"])
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(issuer["public_key"]))
        key.verify(signature, payload_bytes(payload))
    except (InvalidSignature, ValueError):
        return DocumentResult("rejected", issuer=issuer["name"], reasons=["signature_invalid"])

    vitals = {k: float(v) for k, v in payload.get("vitals", {}).items()
              if k in RANGES and _in_range(k, float(v))}
    if not vitals:
        return DocumentResult("rejected", issuer=issuer["name"], reasons=["no_vitals_found"])
    return DocumentResult("verified", vitals=vitals, issuer=issuer["name"],
                          document_date=payload.get("issued"),
                          patient_name=payload.get("patient", {}).get("name"))


# ---------- unsigned documents: read values from the text ----------

NUM = r"(\d{1,3}(?:[.,]\d{1,2})?)"
UNIT = r"\s*(mmol/?l|mg/?dl)?"
# Label and value are often on separate lines (table cells), so the gap may contain newlines
PATTERNS = {
    "bp": rf"(?:blood\s*pressure|\bB\.?P\.?\b)[^\d]{{0,30}}(\d{{2,3}})\s*/\s*(\d{{2,3}})",
    "heart_rate": rf"(?:heart\s*rate|pulse)[^\d]{{0,30}}{NUM}",
    "ldl": rf"\bLDL\b[^\d]{{0,30}}{NUM}{UNIT}",
    "total_cholesterol": rf"total\s*cholesterol[^\d]{{0,30}}{NUM}{UNIT}",
    "hdl": rf"HDL[^\d]{{0,30}}{NUM}{UNIT}",
    "glucose": rf"(?:glucose|blood\s*sugar)[^\d]{{0,30}}{NUM}{UNIT}",
    "height_cm": rf"height[^\d]{{0,20}}{NUM}\s*cm",
    "weight_kg": rf"weight[^\d]{{0,20}}{NUM}\s*kg",
}
# "Date of birth" must not be mistaken for the test date
DATE_PATTERN = r"(?:date(?!\s*of\s*birth)|issued|tested|collected)[^\d\n]{0,25}(\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})"
NAME_PATTERN = r"patient(?:\s*name)?\s*[:\-]\s*([A-Za-z][A-Za-z' \-]{1,60}?)\s*(?:\n|$|\bID\b|\bDOB\b|\bDate\b)"


def _num(s):
    return float(s.replace(",", "."))


def _in_range(key, value):
    lo, hi = RANGES[key]
    return lo <= value <= hi


def _parse_text(text, issuers):
    vitals = {}
    m = re.search(PATTERNS["bp"], text, re.I)
    if m:
        vitals["sbp"], vitals["dbp"] = float(m.group(1)), float(m.group(2))
    for key in ["heart_rate", "ldl", "hdl", "total_cholesterol", "glucose", "height_cm", "weight_kg"]:
        m = re.search(PATTERNS[key], text, re.I)
        if not m:
            continue
        value = _num(m.group(1))
        unit = (m.group(2) or "").lower() if m.lastindex and m.lastindex >= 2 else ""
        if key in MG_PER_MMOL and unit.startswith("mg"):
            value = round(value / MG_PER_MMOL[key], 2)
        vitals[key] = value
    vitals = {k: v for k, v in vitals.items() if _in_range(k, v)}
    if not vitals:
        return DocumentResult("rejected", reasons=["no_vitals_found"])

    # A recognised issuer name in the text is noted, but without a signature it proves nothing
    issuer = next((i["name"] for i in issuers.values() if i["name"].lower() in text.lower()), None)
    dm = re.search(DATE_PATTERN, text, re.I)
    nm = re.search(NAME_PATTERN, text, re.I)
    return DocumentResult("pending_review", vitals=vitals, issuer=issuer,
                          document_date=_parse_date(dm.group(1)) if dm else None,
                          patient_name=nm.group(1).strip() if nm else None,
                          warnings=["unsigned"])


def _parse_date(s):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _check_date(result, today):
    if not result.document_date:
        result.warnings.append("no_date")
        return
    issued = date.fromisoformat(result.document_date)
    if issued > today:
        result.status, result.vitals = "rejected", {}
        result.reasons.append("future_date")
    elif (today - issued).days > MAX_AGE_DAYS:
        result.warnings.append("outdated")


def _check_name(result, expected_name):
    if not expected_name or not result.patient_name:
        return
    if _norm(expected_name) != _norm(result.patient_name):
        if result.status == "verified":
            # A signed document for someone else must never be attached to this patient
            result.status, result.vitals = "rejected", {}
            result.reasons.append("name_mismatch")
        else:
            result.warnings.append("name_mismatch")


def _norm(name):
    return " ".join(sorted(re.sub(r"[^a-z ]", "", name.lower()).split()))


def sha256(data):
    return hashlib.sha256(data).hexdigest()

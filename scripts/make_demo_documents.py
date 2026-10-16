"""
Creates the demo document issuer and sample clinical documents.

    python scripts/make_demo_documents.py

- Generates an Ed25519 key pair for a FICTIONAL issuer ("SKAI Partner Demo
  Clinic"). The private key stays in data/demo_issuer_private_key.pem (git-
  ignored); the public key is written to app/trusted_issuers.json.
- Writes sample PDFs to sample_documents/ for demos and tests:
    john_doe_verified.pdf          signed by the demo issuer -> verified
    john_doe_tampered.pdf          signed, then values edited -> rejected
    nomvula_khumalo_unsigned.pdf   plain clinic printout     -> pending review
    john_doe_older_unsigned.pdf    older printout (approved by a clinician in the demo data)
    jane_smith_verified.pdf        valid, but another person -> rejected for John

A real deployment would register real clinics/labs, each signing with its own
key (or a PAdES certificate), and SKAI would never hold their private keys.
"""
import base64
import copy
import json
import sys
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.documents import ISSUERS_FILE, payload_bytes  # noqa: E402

KEY_FILE = ROOT / "data" / "demo_issuer_private_key.pem"
OUT = ROOT / "sample_documents"
ISSUER = {"id": "skai-demo-clinic", "name": "SKAI Partner Demo Clinic", "type": "clinic"}

LABELS = {
    "sbp": ("Blood pressure (systolic)", "mmHg"), "dbp": ("Blood pressure (diastolic)", "mmHg"),
    "heart_rate": ("Heart rate", "bpm"), "ldl": ("LDL cholesterol", "mmol/L"),
    "total_cholesterol": ("Total cholesterol", "mmol/L"), "glucose": ("Fasting glucose", "mmol/L"),
    "height_cm": ("Height", "cm"), "weight_kg": ("Weight", "kg"),
}


def load_or_create_key():
    if KEY_FILE.exists():
        return serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return key


def register_issuer(key):
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    issuers = [{**ISSUER, "public_key": base64.b64encode(public).decode(),
                "note": "Fictional issuer for demonstration only."}]
    ISSUERS_FILE.write_text(json.dumps(issuers, indent=2), encoding="utf-8")


def render(issuer_name, patient, issued, vitals, signed):
    styles = getSampleStyleSheet()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            title=f"Health screening - {patient['name']}")
    rows = [["Measurement", "Result"]]
    for k, v in vitals.items():
        label, unit = LABELS[k]
        rows.append([label, f"{v:g} {unit}"])
    if "sbp" in vitals and "dbp" in vitals:
        rows.insert(1, ["Blood pressure", f"{vitals['sbp']:g}/{vitals['dbp']:g} mmHg"])
    table = Table(rows, colWidths=[90 * mm, 70 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#94a3b8")), ("FONTSIZE", (0, 0), (-1, -1), 10),
    ]))
    story = [
        Paragraph(f"<b>{issuer_name}</b>", styles["Title"]),
        Paragraph("Fictional organisation - sample document for the SKAI demo", styles["Italic"]),
        Spacer(1, 6 * mm),
        Paragraph(f"Patient: {patient['name']}", styles["Normal"]),
        Paragraph(f"Date of birth: {patient['dob']}", styles["Normal"]),
        Paragraph(f"Date tested: {issued}", styles["Normal"]),
        Spacer(1, 5 * mm), table, Spacer(1, 8 * mm),
        Paragraph("Digitally signed by the issuing clinic. Altering this document invalidates the signature."
                  if signed else "Printed screening summary. Not digitally signed.", styles["Italic"]),
    ]
    doc.build(story)
    return buf.getvalue()


def write(path, pdf, metadata=None):
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(pdf)))
    if metadata:
        writer.add_metadata(metadata)
    with open(path, "wb") as f:
        writer.write(f)


def signed_document(key, path, patient, issued, vitals, tamper=None):
    payload = {"issuer": ISSUER["id"], "document_id": path.stem, "issued": issued,
               "patient": patient, "vitals": vitals}
    signature = base64.b64encode(key.sign(payload_bytes(payload))).decode()
    if tamper:   # change the values after signing, like someone editing the PDF
        payload = copy.deepcopy(payload)
        payload["vitals"].update(tamper)
    write(path, render(ISSUER["name"], patient, issued, payload["vitals"], True),
          {"/SKAIPayload": json.dumps(payload), "/SKAISignature": signature})


def main():
    key = load_or_create_key()
    register_issuer(key)
    OUT.mkdir(exist_ok=True)
    issued = (date.today() - timedelta(days=14)).isoformat()

    john = {"name": "John Doe", "dob": "1979-03-14"}
    john_vitals = {"sbp": 134, "dbp": 86, "heart_rate": 74, "ldl": 3.9, "total_cholesterol": 5.8,
                   "glucose": 5.4, "height_cm": 176, "weight_kg": 86}
    signed_document(key, OUT / "john_doe_verified.pdf", john, issued, john_vitals)
    signed_document(key, OUT / "john_doe_tampered.pdf", john, issued, john_vitals, tamper={"sbp": 112, "ldl": 2.1})
    signed_document(key, OUT / "jane_smith_verified.pdf", {"name": "Jane Smith", "dob": "1985-07-02"}, issued,
                    {"sbp": 118, "dbp": 76, "height_cm": 165, "weight_kg": 61})

    # An older clinic printout for John (approved by a clinician in the demo data)
    older = (date.today() - timedelta(days=100)).isoformat()
    write(OUT / "john_doe_older_unsigned.pdf",
          render("Ubuntu Community Health Clinic", john, older,
                 {"sbp": 152, "dbp": 94, "heart_rate": 82, "ldl": 4.8, "height_cm": 176, "weight_kg": 92}, False))

    nomvula = {"name": "Nomvula Khumalo", "dob": "1970-05-21"}
    write(OUT / "nomvula_khumalo_unsigned.pdf",
          render("Ubuntu Community Health Clinic", nomvula, issued,
                 {"sbp": 164, "dbp": 98, "heart_rate": 81, "ldl": 4.6, "height_cm": 162, "weight_kg": 84}, False))
    print(f"Issuer registered in {ISSUERS_FILE.relative_to(ROOT)}; documents written to {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()

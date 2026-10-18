"""
Builds the Windows app: dist/SKAI/SKAI.exe (plus dist/SKAI-Windows.zip to share).

    python packaging/build_exe.py

Run it from the project folder (in VS Code: Terminal > Run Task > "Build SKAI.exe").
The folder build is used instead of a single file because it starts in seconds
rather than unpacking ~400 MB on every launch.
"""
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# Files the app reads at runtime, kept at the same relative paths (src/preprocessing.ROOT)
DATA = [
    ("app/web", "app/web"),
    ("app/trusted_issuers.json", "app"),
    ("models", "models"),
    ("data/processed", "data/processed"),
    ("data/raw", "data/raw"),
    ("reports/multi_dataset", "reports/multi_dataset"),
    ("sample_documents", "sample_documents"),
]

args = [
    str(ROOT / "packaging" / "skai_desktop.py"),
    "--name", "SKAI",
    "--noconfirm", "--clean",
    "--distpath", str(DIST), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
    "--paths", str(ROOT),
    "--icon", str(ROOT / "packaging" / "skai.ico"),
    "--collect-submodules", "uvicorn",
    "--collect-submodules", "app",
    "--collect-submodules", "src",
    "--collect-data", "shap",
    "--exclude-module", "streamlit",
    "--exclude-module", "tkinter",
    "--exclude-module", "IPython",
    "--exclude-module", "jupyter",
    "--exclude-module", "notebook",
]
for src, dest in DATA:
    args += ["--add-data", f"{ROOT / src}{';' if sys.platform == 'win32' else ':'}{dest}"]

PyInstaller.__main__.run(args)

# Never ship private keys or anyone's patient database
for leftover in (DIST / "SKAI" / "_internal" / "data").rglob("*"):
    if leftover.suffix in (".pem", ".db"):
        leftover.unlink()

zip_base = DIST / "SKAI-Windows"
shutil.make_archive(str(zip_base), "zip", DIST, "SKAI")
print(f"\nBuilt {DIST / 'SKAI' / 'SKAI.exe'}\nShare {zip_base}.zip")

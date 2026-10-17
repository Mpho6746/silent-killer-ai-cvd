"""
Builds dist/SKAI.apk, the Android app (no Android Studio or Gradle needed).

    python packaging/android/build_apk.py https://your-space.hf.space
    python packaging/android/build_apk.py            (no address: the app asks on first launch)

Needs JDK 17 and the Android SDK (platform 34, build-tools 34.0.0).
The signing key is created once in packaging/android/keystore/ and is not committed:
keep it, because updates to the app must be signed with the same key.
"""
import os
import secrets
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BUILD = ROOT / "build" / "android"
OUT = ROOT / "dist" / "SKAI.apk"
SDK = Path(os.environ.get("ANDROID_HOME") or Path.home() / "Android" / "Sdk")
TOOLS = SDK / "build-tools" / "34.0.0"
ANDROID_JAR = SDK / "platforms" / "android-34" / "android.jar"
JDK = Path(os.environ.get("JAVA_HOME") or next(Path(r"C:\Program Files\Microsoft").glob("jdk-17*")))
KEYS = HERE / "keystore"
server = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else ""


def run(*cmd):
    print(">", " ".join(str(c) for c in cmd[:3]), "...")
    subprocess.run([str(c) for c in cmd], check=True)


def tool(name):
    for ext in (".exe", ".bat", ""):
        p = TOOLS / f"{name}{ext}"
        if p.exists():
            return p
    raise SystemExit(f"missing {name} in {TOOLS}")


shutil.rmtree(BUILD, ignore_errors=True)
for d in ("res", "gen", "classes", "dex", "src"):
    (BUILD / d).mkdir(parents=True)

# 1. launcher icons from packaging/skai.ico
icon = Image.open(ROOT / "packaging" / "skai.ico").convert("RGBA")
for folder, size in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
    d = BUILD / "res" / f"mipmap-{folder}"
    d.mkdir()
    icon.resize((size, size), Image.LANCZOS).save(d / "ic_launcher.png")

# 2. source with the server address filled in
src = BUILD / "src" / "za" / "skai" / "health"
src.mkdir(parents=True)
java = (HERE / "src" / "za" / "skai" / "health" / "MainActivity.java").read_text(encoding="utf-8")
(src / "MainActivity.java").write_text(java.replace("@SERVER_URL@", server), encoding="utf-8")

# 3. resources and manifest
run(tool("aapt2"), "compile", "--dir", BUILD / "res", "-o", BUILD / "res.zip")
run(tool("aapt2"), "link", "-o", BUILD / "unsigned.apk", "-I", ANDROID_JAR, "--manifest", HERE / "AndroidManifest.xml",
    "--java", BUILD / "gen", "--min-sdk-version", "29", "--target-sdk-version", "34",
    "--version-code", "1", "--version-name", "1.0", BUILD / "res.zip")

# 4. compile Java and convert to Android bytecode
sources = [str(p) for p in BUILD.joinpath("src").rglob("*.java")] + [str(p) for p in BUILD.joinpath("gen").rglob("*.java")]
run(JDK / "bin" / "javac.exe", "-source", "8", "-target", "8", "-nowarn",
    "-bootclasspath", f"{ANDROID_JAR}{os.pathsep}{TOOLS / 'core-lambda-stubs.jar'}",
    "-d", BUILD / "classes", *sources)
classes = [str(p) for p in BUILD.joinpath("classes").rglob("*.class")]
run(tool("d8"), "--release", "--lib", ANDROID_JAR, "--min-api", "29", "--output", BUILD / "dex", *classes)
with zipfile.ZipFile(BUILD / "unsigned.apk", "a", zipfile.ZIP_DEFLATED) as apk:
    apk.write(BUILD / "dex" / "classes.dex", "classes.dex")

# 5. align and sign
run(tool("zipalign"), "-p", "-f", "4", BUILD / "unsigned.apk", BUILD / "aligned.apk")
KEYS.mkdir(exist_ok=True)
keystore, pw_file = KEYS / "skai.jks", KEYS / "password.txt"
if not keystore.exists():
    pw_file.write_text(secrets.token_urlsafe(18), encoding="utf-8")
    run(JDK / "bin" / "keytool.exe", "-genkeypair", "-keystore", keystore, "-alias", "skai", "-keyalg", "RSA",
        "-keysize", "2048", "-validity", "10000", "-storepass", pw_file.read_text(), "-keypass", pw_file.read_text(),
        "-dname", "CN=SKAI Student Project, O=SKAI, C=ZA")
password = pw_file.read_text().strip()
OUT.parent.mkdir(exist_ok=True)
env = {**os.environ, "JAVA_HOME": str(JDK), "PATH": f"{JDK / 'bin'};{os.environ['PATH']}", "SKAI_KS_PASS": password}
subprocess.run([str(tool("apksigner")), "sign", "--ks", str(keystore), "--ks-key-alias", "skai",
                "--ks-pass", "env:SKAI_KS_PASS", "--out", str(OUT), str(BUILD / "aligned.apk")], check=True, env=env)
subprocess.run([str(tool("apksigner")), "verify", str(OUT)], check=True, env=env)
print(f"\nBuilt {OUT} ({OUT.stat().st_size // 1024} KB), server: {server or '(asks on first launch)'}")

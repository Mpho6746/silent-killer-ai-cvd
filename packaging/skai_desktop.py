"""
SKAI desktop launcher: the entry point of SKAI.exe.

Starts the SKAI server on this computer and opens the app in the default
browser. Patient records are kept in %LOCALAPPDATA%\\SKAI\\skai.db, so they
survive updates of the .exe and are never stored inside it.
Close this window to stop SKAI.
"""
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def data_dir():
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "SKAI"
    base.mkdir(parents=True, exist_ok=True)
    return base


def free_port(preferred=8000):
    for port in range(preferred, preferred + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit("No free port between 8000 and 8019.")


def open_when_ready(url):
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            webbrowser.open(url)
            print(f"\n  SKAI is running at {url}\n  Close this window to stop SKAI.\n")
            return
        except Exception:
            time.sleep(0.5)
    print("SKAI did not start. Please send the messages above to the team.")


if __name__ == "__main__":
    os.environ.setdefault("SKAI_DB", str(data_dir() / "skai.db"))
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    print("  Starting SKAI (Silent Killer AI)... this takes up to a minute (longer the first time).")
    print(f"  Patient records: {os.environ['SKAI_DB']}")

    import uvicorn
    from app.api import app

    threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        sys.exit(0)

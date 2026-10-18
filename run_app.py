"""
Starts the SKAI web app and opens it in the browser once it is ready.

    python run_app.py              -> http://127.0.0.1:8000
    python run_app.py --no-browser
    python run_app.py --port 8080

Loading the models takes up to a minute (longer after a restart), so the
address is announced, and the browser opened, only when SKAI answers.

The Streamlit tool is still available separately: streamlit run app/streamlit_app.py
"""
import argparse
import threading
import time
import urllib.request
import webbrowser

import uvicorn


def announce_when_ready(url, open_browser):
    for _ in range(240):                       # up to 2 minutes
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            print(f"\nSKAI is ready at {url}  (API docs: {url}/docs)\n", flush=True)
            if open_browser:
                webbrowser.open(url)
            return
        except Exception:
            time.sleep(0.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the SKAI web app")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    print("Starting SKAI... loading the models takes up to a minute. Wait for 'SKAI is ready'.", flush=True)
    threading.Thread(target=announce_when_ready, args=(url, not args.no_browser), daemon=True).start()
    uvicorn.run("app.api:app", host="127.0.0.1", port=args.port)

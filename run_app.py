"""
Starts the SKAI web app and opens it in the browser.

    python run_app.py              -> http://127.0.0.1:8000
    python run_app.py --no-browser
    python run_app.py --port 8080

The Streamlit tool is still available separately: streamlit run app/streamlit_app.py
"""
import argparse
import threading
import webbrowser

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the SKAI web app")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    print(f"SKAI running at {url}  (API docs: {url}/docs)")
    uvicorn.run("app.api:app", host="127.0.0.1", port=args.port)

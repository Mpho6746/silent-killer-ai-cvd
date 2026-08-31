import sys
from streamlit.web import cli as stcli

if __name__ == "__main__":
    sys.argv = ["streamlit", "run", "App/streamlit_app.py", "--global.developmentMode=false"]
    sys.exit(stcli.main())
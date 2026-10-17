# SKAI web server for free cloud hosting (Render, Koyeb or any Docker host).
# Uses only the server requirements; the host sets $PORT.
FROM python:3.11-slim

RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY --chown=user packaging/requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY --chown=user app ./app
COPY --chown=user src ./src
COPY --chown=user models ./models
COPY --chown=user data/processed ./data/processed
COPY --chown=user data/raw ./data/raw
COPY --chown=user reports/multi_dataset ./reports/multi_dataset
COPY --chown=user sample_documents ./sample_documents
USER user

# Demo mode: fictional patients and staff. The database lives in /tmp, so it
# resets whenever the server restarts; never enter real patient information.
ENV SKAI_DEMO=1 \
    SKAI_DB=/tmp/skai.db \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib

EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.api:app --host 0.0.0.0 --port ${PORT:-10000}"]

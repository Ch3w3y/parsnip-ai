FROM python:3.13-slim

WORKDIR /app

# age (for encrypting backup secrets) and curl (for installing gcloud)
RUN apt-get update \
    && apt-get install -y --no-install-recommends age curl ca-certificates gnupg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

# Install ingestion deps + APScheduler
COPY ingestion/requirements.txt .
RUN echo "APScheduler==3.10.4" >> requirements.txt && \
    echo "google-cloud-storage>=2.14.0" >> requirements.txt && \
    echo "pyarrow>=14.0.0" >> requirements.txt && \
    echo "pandas>=2.2.0" >> requirements.txt && \
    uv pip install --system -r requirements.txt

# Copy ingestion scripts (single source of truth)
COPY ingestion/ ./ingestion/
COPY storage/ ./storage/
COPY scripts/ ./scripts/
COPY scheduler/ .

CMD ["python", "scheduler.py"]

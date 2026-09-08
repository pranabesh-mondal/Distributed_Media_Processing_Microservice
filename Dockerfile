# ---------------------------------------------------------------------------
# Distributed Media Processing Microservice
# Single image used by both the FastAPI service and the Celery worker.
# Build:  docker build -t media-processing .
# Run:    see docker-compose.yml (api + worker services)
# ---------------------------------------------------------------------------

FROM python:3.12-slim

# FFmpeg is required by the video processor; curl for container health checks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so Docker layer caching works on code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

# Application code.
COPY app ./app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

EXPOSE 8000

# Default: run the API. The worker service overrides the command with celery.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
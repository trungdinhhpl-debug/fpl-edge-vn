# FPL Edge VN — backend
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# system deps for psycopg build (binary wheel usually suffices)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential coinor-cbc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8000

# On first boot with an empty DB, the app auto-syncs live FPL data (in background).
# Respect $PORT when the platform (Railway/Render/Fly) injects one; default 8000.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

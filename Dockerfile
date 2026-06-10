# NEWTOWT mynewtowt — runtime image
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libmagic1 \
        libpango-1.0-0 libpangoft2-1.0-0 \
        libcairo2 \
        libffi-dev \
        gnupg \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

RUN groupadd -r app && useradd -r -g app app \
    && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "2", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM python:3.13-slim

# RDRS — defensive ransomware detection and response system.
#
# The base image satisfies the project's `requires-python = ">=3.11"` and the
# Build Guide's "3.12 or newer". It is left at the version the project already
# declared rather than moved to the local development interpreter (3.14.3): the
# pinned version is not a blocker, and changing it would be an unverifiable
# change with no benefit.
#
# The image carries the application only. All runtime state (SQLite database,
# quarantine evidence, rotating logs, generated reports) lives on mounted
# volumes so the image never contains real environment data.

LABEL org.opencontainers.image.title="RDRS" \
      org.opencontainers.image.description="Defensive ransomware detection and response system" \
      org.opencontainers.image.licenses="Proprietary"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Runtime dependencies only. The manifest is copied on its own so this layer is
# cached across source changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application source and the canonical configuration (FSD FR-1).
COPY pyproject.toml ./
COPY src ./src
COPY config.yaml ./
COPY README.md ./

# A dedicated unprivileged account. The application never needs root: it binds
# a high port (8000) and writes only under /app/data and /app/logs.
RUN groupadd --system --gid 1001 rdrs \
    && useradd --system --uid 1001 --gid rdrs --home-dir /app --shell /usr/sbin/nologin rdrs \
    && mkdir -p /app/data /app/logs /app/data/reports /app/data/quarantine \
    && chown -R rdrs:rdrs /app

USER rdrs

EXPOSE 8000

# Real readiness probe: it calls the application's own /health endpoint and
# fails on a non-2xx response. It does not report a hardcoded status.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; \
r = urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); \
sys.exit(0 if r.status == 200 else 1)" || exit 1

# The real entrypoint: uvicorn serving the FastAPI app defined in rdrs.api.
# 0.0.0.0 is required so the port is reachable from outside the container.
CMD ["uvicorn", "rdrs.api:app", "--host", "0.0.0.0", "--port", "8000"]

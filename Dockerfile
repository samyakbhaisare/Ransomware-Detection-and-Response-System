FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

# Runtime dependencies only; the manifest is copied first so this layer is
# cached across source changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml .
COPY src ./src
COPY config.yaml .
COPY README.md .

# Runtime state (SQLite database, quarantine evidence, logs) is created at
# start-up and mounted as volumes - never baked into the image, so the image
# stays free of any real environment data.
RUN mkdir -p /app/data /app/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()" || exit 1

CMD ["uvicorn", "rdrs.api:app", "--host", "0.0.0.0", "--port", "8000"]

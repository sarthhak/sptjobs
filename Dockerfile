FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
COPY requirements.txt .
# libatomic1 is needed by the Node runtime bundled with Playwright.
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium
COPY app ./app
COPY scripts ./scripts

EXPOSE 8000
# Hosts like Railway set PORT; default to 8000 elsewhere.
CMD ["sh", "-c", "uvicorn app.main:app_factory --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]

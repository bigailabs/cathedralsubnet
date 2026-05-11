# Cathedral verified-runtime image.
#
# Polaris's marketplace runtime-evaluate endpoint deploys this image and
# POSTs a task to /task. The container fetches the miner's encrypted
# bundle from a presigned URL, decrypts with the KEK injected by
# Polaris, reads the miner's soul.md as system prompt, calls the LLM
# provider, and returns a Card JSON.
#
# Built by .github/workflows/cathedral-runtime-image.yml and pushed to
# ghcr.io/cathedralai/cathedral-runtime:<version>. Bump
# `CATHEDRAL_RUNTIME_VERSION` below to cut a new release.

FROM python:3.12-slim

ARG CATHEDRAL_RUNTIME_VERSION=v1.0.0
ENV CATHEDRAL_RUNTIME_VERSION=${CATHEDRAL_RUNTIME_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user matching Polaris's Hermes image so the two runtimes can
# share volume layouts and permissions handoffs without root-flip dances.
RUN groupadd --gid 1000 cathedral && \
    useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash cathedral

WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py /app/

USER cathedral

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]

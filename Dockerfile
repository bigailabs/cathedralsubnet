FROM python:3.11-slim

WORKDIR /app

# System deps for Bittensor SDK + crypto
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Persistent SQLite for the publisher's submission ledger.
# Railway provides volume mounts via the dashboard rather than VOLUME directives;
# the publisher writes to whatever CATHEDRAL_DB_PATH points at.
RUN mkdir -p /data
ENV CATHEDRAL_DB_PATH=/data/publisher.db

EXPOSE 8080

# Force unbuffered Python output so Railway captures stack traces if the
# publisher crashes at startup. Without this, Python buffers stdout/stderr
# until the process flushes, which never happens on a hard crash.
ENV PYTHONUNBUFFERED=1
ENV PYTHONFAULTHANDLER=1

# Bootstrap on every container start:
# 1. seed-cards: idempotent INSERT-or-UPDATE of the launch card_definitions
#    row (EU AI Act). Cheap; runs even if the row already exists.
# 2. load-eval-spec: pulls real EU AI Act content from the public
#    cathedral-eval-spec GitHub repo and updates the row. Idempotent.
#    Failure is non-fatal (logs to stderr, container still starts); we'd
#    rather serve placeholder content than refuse to start.
# 3. archive-cards: marks the 4 deprecated launch-plan card IDs as
#    status='archived' so submit and eval-spec endpoints return 404 for
#    them. Idempotent; no-op on a fresh DB that never seeded them.
# 4. serve: start uvicorn + the FastAPI app + background eval orchestrator.
CMD ["sh", "-c", "\
  echo '[startup] seed-cards' && \
  cathedral-publisher seed-cards --db ${CATHEDRAL_DB_PATH} && \
  echo '[startup] load-eval-spec' && \
  cathedral-publisher load-eval-spec --db ${CATHEDRAL_DB_PATH} || echo '[startup] load-eval-spec failed; continuing with placeholder content' && \
  echo '[startup] archive-cards (deprecated launch-plan IDs)' && \
  cathedral-publisher archive-cards us-ai-eo uk-ai-whitepaper singapore-pdpc japan-meti-mic --db ${CATHEDRAL_DB_PATH} && \
  echo '[startup] serve --db '${CATHEDRAL_DB_PATH}' --port '${PORT:-8080} && \
  cathedral-publisher serve --db ${CATHEDRAL_DB_PATH} --port ${PORT:-8080} --host 0.0.0.0 \
"]

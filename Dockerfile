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

# cathedral-publisher CLI: serve subcommand starts the FastAPI app.
# Railway's PORT env wins over hardcoded 8080.
CMD ["sh", "-c", "cathedral-publisher serve --db ${CATHEDRAL_DB_PATH} --port ${PORT:-8080} --host 0.0.0.0"]

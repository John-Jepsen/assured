# Insurance AI — API / agent / speech / telephony backend (single async service).
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf \
    PIPER_VOICE_DIR=/models/piper

WORKDIR /app

# System deps: curl for healthcheck; libsndfile for soundfile (speech extra).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libsndfile1 && rm -rf /var/lib/apt/lists/*

# Install Python deps first for layer caching. Speech/ML extras are OPTIONAL and are
# controlled by build arg INSTALL_SPEECH (off by default → small, fast image).
COPY apps/api/pyproject.toml /app/apps/api/pyproject.toml
ARG INSTALL_SPEECH=false
RUN pip install --upgrade pip && \
    cd /app/apps/api && \
    if [ "$INSTALL_SPEECH" = "true" ]; then pip install ".[dev,speech,stripe,telephony]"; \
    else pip install ".[dev]"; fi || true

# App source + knowledge base.
COPY apps/api /app/apps/api
COPY knowledge /app/knowledge
COPY evals /app/evals

# Reinstall now that the package source is present (editable for tooling).
RUN cd /app/apps/api && pip install -e ".[dev]"

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /app/apps/api
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD curl -fsS http://localhost:8000/ready | grep -qE '"ready"[[:space:]]*:[[:space:]]*true' || exit 1

ENTRYPOINT ["/entrypoint.sh"]

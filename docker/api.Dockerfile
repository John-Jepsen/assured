# Insurance AI — API / agent / speech / telephony backend (single async service).
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS builder

# Keep the resolver itself immutable as well as the dependency graph it consumes.
COPY --from=ghcr.io/astral-sh/uv:0.11.6@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754 /uv /uvx /bin/

# Build the environment at the exact path it will occupy in the runtime stage.
# uv writes absolute interpreter shebangs into the console scripts it installs, so
# a venv built elsewhere and copied would leave `uvicorn`/`alembic` unexecutable.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build/apps/api

# uv.lock records exact versions and artifact hashes for every supported profile.
# The runtime receives only the completed, non-editable virtual environment.
COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/uv.lock ./uv.lock
COPY apps/api/insurance_ai ./insurance_ai

ARG INSTALL_SPEECH=false
RUN case "${INSTALL_SPEECH}" in \
        true) extras="--extra runtime --extra speech --extra stripe --extra telephony" ;; \
        false) extras="--extra runtime" ;; \
        *) echo "INSTALL_SPEECH must be 'true' or 'false'" >&2; exit 2 ;; \
    esac && \
    uv sync --frozen --no-dev --no-editable ${extras} && \
    uv pip check --python /opt/venv/bin/python

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    HF_HOME=/models/hf \
    PIPER_VOICE_DIR=/models/piper \
    XDG_CACHE_HOME=/models/cache

# curl supports the image healthcheck; libsndfile is installed only when speech
# support is baked in.
ARG INSTALL_SPEECH=false
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl \
    && if [ "${INSTALL_SPEECH}" = "true" ]; then \
        apt-get install -y --no-install-recommends libsndfile1; \
    fi \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 assured \
    && useradd --uid 10001 --gid assured --create-home --home-dir /home/assured assured \
    && install -d -o assured -g assured /models/hf /models/piper /models/cache

COPY --from=builder /opt/venv /opt/venv

# Alembic and the bootstrap command intentionally remain available for the local
# entrypoint. Application code itself is installed from the non-editable wheel.
WORKDIR /app/apps/api
COPY apps/api/alembic.ini ./alembic.ini
COPY apps/api/migrations ./migrations
COPY apps/api/scripts ./scripts
COPY knowledge /app/knowledge
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 0555 /entrypoint.sh \
    && chmod -R a-w /app

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD curl -fsS http://localhost:8000/ready | grep -qE '"ready"[[:space:]]*:[[:space:]]*true' || exit 1

ENTRYPOINT ["/entrypoint.sh"]

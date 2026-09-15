# Insurance AI — API / agent / speech / telephony backend (single async service).
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build/apps/api

# Build a wheelhouse so the runtime stage never needs compilers, package metadata,
# editable source installs, or access to a package index.
COPY apps/api/pyproject.toml ./pyproject.toml
COPY apps/api/insurance_ai ./insurance_ai

ARG INSTALL_SPEECH=false
RUN case "${INSTALL_SPEECH}" in \
        true) extras=".[runtime,speech,stripe,telephony]" ;; \
        false) extras=".[runtime]" ;; \
        *) echo "INSTALL_SPEECH must be 'true' or 'false'" >&2; exit 2 ;; \
    esac && \
    python -m pip wheel --wheel-dir /wheels "${extras}"

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf \
    PIPER_VOICE_DIR=/models/piper \
    XDG_CACHE_HOME=/models/cache

# curl supports the image healthcheck; libsndfile is installed only when speech
# support is baked in.
ARG INSTALL_SPEECH=false
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && if [ "${INSTALL_SPEECH}" = "true" ]; then \
        apt-get install -y --no-install-recommends libsndfile1; \
    fi \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 assured \
    && useradd --uid 10001 --gid assured --create-home --home-dir /home/assured assured \
    && install -d -o assured -g assured /models/hf /models/piper /models/cache

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

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

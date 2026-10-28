# syntax=docker/dockerfile:1.7
# Pocket Finance backend image. One image for two processes:
#   api    — uvicorn app.main:app (runs `alembic upgrade head` first)
#   worker — python -m app.worker (scheduler, docs/ARCH.md §9)
# Build context = repository root:
#   docker build -f infra/docker/backend.Dockerfile -t pocket-backend .
# Base images are pinned by digest (reproducible builds); bump tag + digest together.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0

FROM ${UV_IMAGE} AS uv

# ---------- build: resolve and install dependencies into /opt/venv ----------
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Dependencies first (cached layer). uv.lock is optional: frozen install when present.
COPY backend/pyproject.toml backend/uv.lock* ./
RUN --mount=type=cache,id=pf-uv,target=/root/.cache/uv \
    if [ -f uv.lock ]; then uv sync --frozen --no-dev --no-install-project; \
    else uv sync --no-dev --no-install-project; fi

# ---------- runtime ----------
FROM ${PYTHON_IMAGE} AS runtime
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --system --gid "${APP_GID}" pocket \
 && useradd --system --uid "${APP_UID}" --gid pocket --home-dir /app --no-create-home \
      --shell /usr/sbin/nologin pocket
ENV PATH=/opt/venv/bin:${PATH} \
    PYTHONPATH=/app:/usr/local/lib/pf \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
# Sources are owned by root and read-only for the app user.
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY --chmod=0755 infra/docker/backend-entrypoint.sh /usr/local/bin/pf-entrypoint
COPY --chmod=0755 infra/docker/pf-env-normalize.py /usr/local/lib/pf/pf-env-normalize.py
# JSON logs for uvicorn/alembic, trusted-proxy list, migrations runner (see backend-entrypoint.sh).
COPY --chmod=0644 infra/docker/pf_logging.py infra/docker/pf_migrate.py infra/docker/pf_proxies.py \
     infra/docker/uvicorn-log.json /usr/local/lib/pf/
RUN python -m compileall -q /app/app /app/alembic /usr/local/lib/pf || true
ARG PF_VERSION=dev
LABEL org.opencontainers.image.title="pocket-backend" \
      org.opencontainers.image.source="https://github.com/d3grando/pocket-finance" \
      org.opencontainers.image.version="${PF_VERSION}"
USER pocket:pocket
EXPOSE 8000
ENTRYPOINT ["pf-entrypoint"]
CMD ["api"]

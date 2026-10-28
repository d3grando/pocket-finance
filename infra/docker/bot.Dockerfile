# syntax=docker/dockerfile:1.7
# Pocket Finance Telegram bot image. Long polling, HTTP client of the API.
# Build context = repository root:
#   docker build -f infra/docker/bot.Dockerfile -t pocket-bot .
# Base images are pinned by digest (reproducible builds); bump tag + digest together.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY bot/pyproject.toml bot/uv.lock* ./
RUN --mount=type=cache,id=pf-uv,target=/root/.cache/uv \
    if [ -f uv.lock ]; then uv sync --frozen --no-dev --no-install-project; \
    else uv sync --no-dev --no-install-project; fi

FROM ${PYTHON_IMAGE} AS runtime
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --system --gid "${APP_GID}" pocket \
 && useradd --system --uid "${APP_UID}" --gid pocket --home-dir /app --no-create-home \
      --shell /usr/sbin/nologin pocket
ENV PATH=/opt/venv/bin:${PATH} \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY bot/pocket_bot ./pocket_bot
COPY --chmod=0755 infra/docker/bot-entrypoint.sh /usr/local/bin/pf-bot-entrypoint
COPY --chmod=0755 infra/docker/pf-env-normalize.py /usr/local/lib/pf/pf-env-normalize.py
RUN python -m compileall -q /app/pocket_bot || true
ARG PF_VERSION=dev
LABEL org.opencontainers.image.title="pocket-bot" \
      org.opencontainers.image.source="https://github.com/d3grando/pocket-finance" \
      org.opencontainers.image.version="${PF_VERSION}"
USER pocket:pocket
ENTRYPOINT ["pf-bot-entrypoint"]
CMD ["python", "-m", "pocket_bot"]

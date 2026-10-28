# syntax=docker/dockerfile:1.7
# Pocket Finance web image: Vite/PWA build (node:22) → static files served by
# Caddy on :8080, which also proxies /api/* to the API (one port for the external proxy).
# Build context = repository root:
#   docker build -f infra/docker/web.Dockerfile -t pocket-web .
# Base images are pinned by digest (reproducible builds); bump tag + digest together.
ARG NODE_IMAGE=node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914
ARG CADDY_IMAGE=caddy:2@sha256:504a44d7d74bd2bc28a66afc4774912fc21223ca8a67734af1c54ab2f999b489

# ---------- build ----------
FROM ${NODE_IMAGE} AS build
ARG PNPM_VERSION=12.15.0
ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:${PATH} \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    CI=true
RUN corepack enable && corepack prepare "pnpm@${PNPM_VERSION}" --activate
WORKDIR /web
# Manifests first (cached layer). Lockfile/workspace settings are optional.
COPY web/package.json web/pnpm-lock.yaml* web/pnpm-workspace.yaml* web/.npmrc* ./
RUN --mount=type=cache,id=pf-pnpm,target=/pnpm/store \
    if [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile --store-dir /pnpm/store; \
    else pnpm install --store-dir /pnpm/store; fi
COPY web/ ./
# Same-origin API by default (the web container proxies /api/*).
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
# Full build = type check + vite build (web/package.json "build"). Override only for diagnostics.
ARG WEB_BUILD_CMD="pnpm run build"
RUN sh -c "${WEB_BUILD_CMD}" && test -f dist/index.html

# ---------- runtime ----------
FROM ${CADDY_IMAGE} AS runtime
COPY infra/caddy/web.Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist /srv/www
ENV API_UPSTREAM=api:8000 \
    TRUSTED_PROXIES=127.0.0.1/32 \
    WEB_LOG_LEVEL=INFO \
    XDG_CONFIG_HOME=/tmp/caddy/config \
    XDG_DATA_HOME=/tmp/caddy/data
# 8080: published port; 8081: internal listener for the standalone edge Caddy.
EXPOSE 8080 8081
# The official binary carries file capability cap_net_bind_service; exec of such a binary fails
# under cap_drop: ALL. :8080/:8081 need no capabilities → strip it (a plain copy drops xattrs).
RUN cp /usr/bin/caddy /usr/bin/caddy.plain && mv -f /usr/bin/caddy.plain /usr/bin/caddy \
 && caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
# Version label last: an ARG invalidates the cache of every RUN after it, and the caddy copy
# above (~70 MB) would otherwise become a new layer for each release.
ARG PF_VERSION=dev
LABEL org.opencontainers.image.title="pocket-web" \
      org.opencontainers.image.source="https://github.com/d3grando/pocket-finance" \
      org.opencontainers.image.version="${PF_VERSION}"
# Unprivileged: :8080/:8081 need no capabilities, Caddy state lives in /tmp (admin API is off).
USER 10001:10001
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

#!/bin/sh
# renders the edge Caddyfile (optional ACME e-mail) and starts Caddy.
set -eu
: "${PF_DOMAIN:?PF_DOMAIN is required for the standalone profile (e.g. pocket.example.com)}"
mkdir -p /config
if [ -n "${ACME_EMAIL:-}" ]; then line="email ${ACME_EMAIL}"; else line=""; fi
sed "s|__EMAIL_LINE__|${line}|" /etc/caddy/edge.Caddyfile.tmpl > /config/Caddyfile
caddy validate --config /config/Caddyfile --adapter caddyfile >/dev/null
exec caddy run --config /config/Caddyfile --adapter caddyfile

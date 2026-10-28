#!/bin/sh
# Pocket Finance backend entrypoint.
#   api          wait for DB, `alembic upgrade head` (MIGRATE_ON_START=true), exec uvicorn
#   worker       wait for DB, exec python -m app.worker
#   migrate      wait for DB, alembic upgrade head, exit
#   healthcheck  exit 0 only when GET /api/v1/health answers 200 with "db": true
#   cli ...      python -m app.cli ...   (e.g. `cli create-owner`)
#   anything else is exec'ed as is.
# Logs are JSON lines on stdout (ARCH §6): uvicorn via /usr/local/lib/pf/uvicorn-log.json,
# migrations via pf_migrate (prints every applied revision).
set -eu

log() { printf '{"level":"%s","logger":"pf.entrypoint","msg":"%s"}\n' "${2:-INFO}" "$1"; }

# Container healthcheck (compose): cheap, no env normalization. "degraded" (DB down) = unhealthy.
if [ "${1:-}" = "healthcheck" ]; then
  exec python - "${API_PORT:-8000}" <<'PY'
import json, sys, urllib.request as u
try:
    r = u.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/v1/health", timeout=4)
    body = json.load(r)
except Exception as e:  # noqa: BLE001
    print(f"unhealthy: {type(e).__name__}: {e}")
    sys.exit(1)
ok = r.status == 200 and body.get("db") is True
print(("ok: " if ok else "unhealthy: ") + json.dumps(body))
sys.exit(0 if ok else 1)
PY
fi

wait_db() {
  timeout="${DB_WAIT_SECONDS:-90}"
  python - "$timeout" <<'PY'
import asyncio, os, sys, time
import asyncpg

url = os.environ.get("DATABASE_URL", "")
dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1).replace(
    "postgresql+psycopg://", "postgresql://", 1)
deadline = time.monotonic() + float(sys.argv[1])
last = None
while time.monotonic() < deadline:
    try:
        async def ping():
            conn = await asyncpg.connect(dsn, timeout=5)
            try:
                await conn.execute("SELECT 1")
            finally:
                await conn.close()
        asyncio.run(ping())
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        last = e
        time.sleep(2)
print(f"database is not reachable: {type(last).__name__}: {last}", file=sys.stderr)
sys.exit(1)
PY
}

# alembic upgrade head with JSON logs; exit 3 + rollback hint when the database is newer than
# this code (docs/UPDATE.md «Откат»).
migrate() {
  python -m pf_migrate
}

# CSV list settings (WEB_ORIGINS, ...) → JSON when app.config cannot parse them as given.
eval "$(python /usr/local/lib/pf/pf-env-normalize.py app.config:Settings)"

cmd="${1:-api}"
case "$cmd" in
  api)
    wait_db
    if [ "${MIGRATE_ON_START:-true}" = "true" ]; then migrate; fi
    # X-Forwarded-For is honoured only from TRUSTED_PROXIES (default "auto" = loopback + the
    # compose networks, i.e. the web container). uvicorn gets exactly the same set (ARCH §11);
    # never "*": uvicorn would then take the left-most, client-chosen address.
    if ! tp="$(python /usr/local/lib/pf/pf_proxies.py "${TRUSTED_PROXIES:-auto}")"; then
      log "invalid TRUSTED_PROXIES='${TRUSTED_PROXIES:-}' (see docs/INSTALL.md)" ERROR
      exit 2
    fi
    export TRUSTED_PROXIES="$tp"
    unset FORWARDED_ALLOW_IPS || true
    log "trusted proxies: $TRUSTED_PROXIES"
    log "starting uvicorn on :${API_PORT:-8000}"
    exec uvicorn app.main:app \
      --host 0.0.0.0 --port "${API_PORT:-8000}" --workers 1 \
      --proxy-headers --forwarded-allow-ips "$TRUSTED_PROXIES" \
      --log-config /usr/local/lib/pf/uvicorn-log.json \
      --no-server-header --timeout-graceful-shutdown 15 \
      --log-level "$(echo "${LOG_LEVEL:-INFO}" | tr '[:upper:]' '[:lower:]')"
    ;;
  worker)
    wait_db
    log "starting worker"
    exec python -m app.worker
    ;;
  migrate)
    wait_db
    migrate
    ;;
  cli)
    shift
    exec python -m app.cli "$@"
    ;;
  *)
    exec "$@"
    ;;
esac

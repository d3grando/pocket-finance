#!/usr/bin/env bash
# end-to-end test of the compose stack (run on a dev/CI host with Docker):
#   scripts/test-stack.sh            build + up (project pf-infra, web on 127.0.0.1:8180), checks,
#                                    backup → restore round trip, remote rsync over SSH,
#                                    standalone edge Caddy with HTTPS, then tear everything down
#   KEEP=1 scripts/test-stack.sh     leave the stack running afterwards
# Env: PF_TEST_PROJECT (pf-infra), PF_TEST_PORT (8180), PF_TEST_TLS_PORT (18443),
#      PF_TEST_DIR (/tmp/pf-infra-test), SKIP_EDGE=1, SKIP_REMOTE=1, SKIP_BUILD=1.
# Never uses real secrets: TELEGRAM_BOT_TOKEN / AI_API_KEY stay empty.
source "$(dirname "$0")/lib.sh"
need_docker
set +o pipefail  # checks below are explicit; avoid SIGPIPE false negatives with grep -q

P="${PF_TEST_PROJECT:-pf-infra}"
PORT="${PF_TEST_PORT:-8180}"
TLS_PORT="${PF_TEST_TLS_PORT:-18443}"
HTTP_EDGE_PORT="${PF_TEST_EDGE_HTTP_PORT:-18181}"
T="${PF_TEST_DIR:-/tmp/pf-infra-test}"
BASE="http://127.0.0.1:${PORT}"
SSHD="${P}-sshd"
PASS=0
FAILS=()

ok() { PASS=$((PASS + 1)); printf '\033[32mok\033[0m   %s\n' "$*"; }
bad() { FAILS+=("$*"); printf '\033[31mFAIL\033[0m %s\n' "$*"; }
check() {  # check "<description>" <command...>
  local d="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi
}

cleanup() {
  if [ "${KEEP:-0}" = "1" ]; then
    warn "KEEP=1: стек оставлен (docker compose $PF_COMPOSE_OPTS ...), каталог $T"
    return
  fi
  docker rm -f "$SSHD" >/dev/null 2>&1 || true
  dc --profile standalone down -v --remove-orphans >/dev/null 2>&1 || true
  docker network rm "${P}-probe" >/dev/null 2>&1 || true
  docker run --rm -v "$T:/t" alpine:3 sh -c 'rm -rf /t/* /t/.[!.]*' >/dev/null 2>&1 || rm -rf "$T"
  rmdir "$T" 2>/dev/null || true
}

# ---------- environment ----------
mkdir -p "$T/backups" "$T/ssh"
ENVF="$T/test.env"
cat >"$ENVF" <<EOF
COMPOSE_PROJECT_NAME=$P
PF_VERSION=test
APP_ENV=prod
PUBLIC_URL=https://pocket.test
WEB_ORIGINS=
WEBAUTHN_RP_ID=
WEBAUTHN_ORIGINS=
AI_ENABLED=false
AI_BASE_URL=
AI_MODEL=
TELEGRAM_ALLOWED_IDS=
TELEGRAM_BOT_USERNAME=
TRUSTED_PROXIES=
PF_HTTP_BIND=127.0.0.1
PF_HTTP_PORT=$PORT
POSTGRES_DB=pocket
POSTGRES_USER=pocket
POSTGRES_PASSWORD=Test$(head -c 64 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | head -c 24)
SECRET_KEY=test-$(head -c 256 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | head -c 48)
BOT_SERVICE_TOKEN=test-$(head -c 256 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | head -c 32)
AI_API_KEY=
TELEGRAM_BOT_TOKEN=
BACKUP_DIR=$T/backups
BACKUP_SSH_DIR=$T/ssh
BACKUP_CRON="15 4 * * *"
PF_DOMAIN=localhost
EDGE_BIND=127.0.0.1
EDGE_HTTP_PORT=$HTTP_EDGE_PORT
EDGE_HTTPS_PORT=$TLS_PORT
EOF
export PF_COMPOSE_OPTS="-p $P --env-file $ENVF"
trap cleanup EXIT

say "config"
check "docker compose config is valid" dc config -q
check "standalone profile config is valid" dc --profile standalone config -q
# Fail-fast: no PUBLIC_URL → compose refuses with a clear message (no author/localhost default).
grep -v '^PUBLIC_URL=' "$ENVF" >"$T/nopub.env"
out="$(docker compose -p "$P" --env-file "$T/nopub.env" config -q 2>&1)" \
  && bad "compose config without PUBLIC_URL must fail" \
  || { echo "$out" | grep -q 'set PUBLIC_URL' && ok "compose without PUBLIC_URL fails: ${out:0:80}" || bad "no PUBLIC_URL: $out"; }
cfg="$(dc --profile standalone config --format json 2>/dev/null)"
for s in backup caddy; do
  echo "$cfg" | jq -e --arg s "$s" '.services[$s] | (.cap_drop | index("ALL")) and
    (.security_opt | index("no-new-privileges:true")) and (.mem_limit | tonumber > 0) and (.pids_limit > 0)' \
    >/dev/null && ok "$s: cap_drop ALL, no-new-privileges, mem/pids limits" || bad "$s hardening: $(echo "$cfg" | jq -c --arg s "$s" '.services[$s] | {cap_drop, cap_add, security_opt, mem_limit, pids_limit}')"
done

# ---------- build + up ----------
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  say "build (backend, bot, web, backup)"
  dc build api bot web backup
  # worker shares the api image; make sure the tag exists without a second build
fi
say "up db api worker web backup"
dc up -d db api web backup
wait_healthy db 120
wait_healthy api 240
wait_healthy web 60
dc up -d worker bot >/dev/null 2>&1 || true

# ---------- HTTP checks ----------
say "HTTP checks on $BASE"
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
hdr() { curl -s -D - -o /dev/null "$1" | tr -d '\r' | grep -i "^$2:" | head -n1 | cut -d' ' -f2-; }

health="$(curl -s "$BASE/api/v1/health")"
[ "$(code "$BASE/api/v1/health")" = "200" ] && ok "GET /api/v1/health → 200 via web proxy" || bad "health 200 (got $(code "$BASE/api/v1/health"))"
echo "$health" | grep -q '"status":"ok"' && echo "$health" | grep -q '"db":true' \
  && ok "health body: status ok, db true ($health)" || bad "health body: $health"
echo "$health" | grep -q '"version":"test"' && ok "health version = PF_VERSION" || bad "health version: $health"
[ "$(code "$BASE/")" = "200" ] && ok "GET / → 200" || bad "GET /"
curl -s "$BASE/" | grep -q '<div id="root">' && ok "/ serves the SPA index.html" || bad "SPA index.html"
[ "$(code "$BASE/operations?tx=1")" = "200" ] && curl -s "$BASE/settings/devices" | grep -q 'id="root"' \
  && ok "SPA fallback for client routes" || bad "SPA fallback"
[ "$(code "$BASE/assets/does-not-exist.js")" = "404" ] && ok "missing hashed asset → 404 (no HTML fallback)" || bad "missing asset 404"
[ "$(hdr "$BASE/" content-security-policy | cut -c1-18)" = "default-src 'self'" ] && ok "CSP on static" || bad "CSP header"
[ "$(hdr "$BASE/" x-content-type-options)" = "nosniff" ] && ok "nosniff on static" || bad "nosniff"
[ "$(hdr "$BASE/" cache-control)" = "no-cache" ] && ok "index.html Cache-Control: no-cache" || bad "index no-cache"
asset="$(curl -s "$BASE/" | grep -oE '/assets/[^"]+\.js' | head -n1 || true)"
if [ -n "$asset" ]; then
  [ "$(hdr "$BASE$asset" cache-control)" = "public, max-age=31536000, immutable" ] \
    && ok "hashed asset immutable cache ($asset)" || bad "asset cache header"
  [ "$(curl -s -H 'Accept-Encoding: gzip' -D - -o /dev/null "$BASE$asset" | tr -d '\r' | grep -i '^content-encoding:' | cut -d' ' -f2)" = "gzip" ] \
    && ok "static compression (gzip)" || bad "gzip on assets"
else
  bad "no /assets/*.js referenced from index.html"
fi
[ -z "$(hdr "$BASE/" server)" ] && ok "no Server header" || bad "Server header present"
[ -z "$(hdr "$BASE/assets/does-not-exist.js" server)" ] && ok "no Server header on error responses" \
  || bad "Server header on a 404 error response"
nf="$(curl -s "$BASE/api/v1/definitely-missing")"
echo "$nf" | grep -q '"error"' && ok "unknown /api path → JSON error from the API (proxied)" || bad "api 404 json: $nf"

# ---------- client IP: X-Forwarded-For trust chain (front → web Caddy → api) ----------
say "client IP / X-Forwarded-For"
OWNER_PW="Pw-$(head -c 256 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | head -c 24)"
dc exec -T api pf-entrypoint cli create-owner --password "$OWNER_PW" >"$T/owner.log" 2>&1 \
  && ok "README quick start: cli create-owner on a fresh database" || { bad "cli create-owner"; cat "$T/owner.log"; }
login() {  # login <X-Forwarded-For value | -> → HTTP code; headers in $T/login.h
  local h=()
  [ "$1" = "-" ] || h=(-H "X-Forwarded-For: $1")
  curl -s -D "$T/login.h" -o /dev/null -w '%{http_code}' "${h[@]}" -H 'Content-Type: application/json' \
    --data "{\"username\":\"owner\",\"password\":\"$OWNER_PW\"}" "$BASE/api/v1/auth/login"
}
session_ip() {  # IP stored for the session from the last login()
  local c
  c="$(tr -d '\r' <"$T/login.h" | sed -n 's/^[Ss]et-[Cc]ookie: pf_session=\([^;]*\).*/\1/p' | head -n1)"
  curl -s -H "Cookie: pf_session=$c" "$BASE/api/v1/auth/sessions" | jq -r '.[] | select(.is_current) | .ip'
}
api_cmd="$(dc exec -T api sh -c 'tr "\0" " " </proc/1/cmdline' | tr -d '\r')"
api_tp="$(dc exec -T api sh -c 'tr "\0" "\n" </proc/1/environ | sed -n "s/^TRUSTED_PROXIES=//p"' | tr -d '\r')"
case "$api_cmd" in
  *"--forwarded-allow-ips $api_tp "*) ok "uvicorn --forwarded-allow-ips = api TRUSTED_PROXIES ($api_tp)" ;;
  *) bad "uvicorn proxy set differs from TRUSTED_PROXIES: '$api_cmd' vs '$api_tp'" ;;
esac
echo "$api_tp" | grep -qE '(^|,)(\*|0\.0\.0\.0/0|::/0)(,|$)' && bad "api trusts everybody: $api_tp" \
  || { echo "$api_tp" | grep -q '^127.0.0.1/32,::1/128,[0-9]' && ok "api trusts only loopback + compose networks" || bad "api TRUSTED_PROXIES: $api_tp"; }
# Default TRUSTED_PROXIES (127.0.0.1/32): nobody in front of the published port is trusted, so
# X-Forwarded-For sent through it (here via the docker gateway) is ignored.
c="$(login '203.0.113.88')"
ip="$(session_ip)"
[ "$c" = "200" ] && [ -n "$ip" ] && [ "$ip" != "203.0.113.88" ] \
  && ok "default: XFF on the published port ignored (session IP $ip)" || bad "default trust: HTTP $c, session IP '$ip'"
web_tp="$(dc exec -T web sh -c 'echo "$TRUSTED_PROXIES"' | tr -d '\r')"
[ "$web_tp" = "127.0.0.1/32" ] && ok "web TRUSTED_PROXIES defaults to 127.0.0.1/32" || bad "web TRUSTED_PROXIES default: '$web_tp'"
# A configured proxy (here: the docker gateway, inside private_ranges) appends the real client to
# whatever the client sent: the right-most untrusted hop wins, never the left-most.
TRUSTED_PROXIES=private_ranges dc up -d web >/dev/null 2>&1
wait_healthy web 60
c="$(login '198.51.100.7, 203.0.113.77')"
ip="$(session_ip)"
[ "$c" = "200" ] && [ "$ip" = "203.0.113.77" ] \
  && ok "XFF 'spoofed, real' from a trusted proxy → session IP is the real one ($ip)" \
  || bad "XFF through a trusted proxy: HTTP $c, session IP '$ip' (want 203.0.113.77)"
# Untrusted peer: its X-Forwarded-For is ignored, rotating it does not bypass 5 logins/min/IP.
dc up -d web >/dev/null 2>&1
wait_healthy web 60
codes=""
for i in 1 2 3 4 5 6; do codes="$codes $(login "198.51.100.$i")"; done
case "$codes" in
  *429*) ok "rotating X-Forwarded-For does not bypass the login rate limit:$codes" ;;
  *) bad "login rate limit bypassed by rotating XFF:$codes" ;;
esac

# ---------- logs: JSON lines only, no healthcheck noise, migrations visible ----------
say "logs"
sleep 12  # at least one container healthcheck after start
nonjson="$(dc logs --no-log-prefix api 2>/dev/null | grep -v '^{' | grep -c . || true)"
[ "$nonjson" = "0" ] && ok "api logs are JSON lines only" || { bad "api logs: $nonjson non-JSON lines"; dc logs --no-log-prefix --tail 5 api | grep -v '^{' | head -n 5; }
dc logs --no-log-prefix api 2>/dev/null | grep -q '"path": *"/api/v1/health".*"client": *"127.0.0.1' \
  && bad "healthcheck requests are in the access log" || ok "container healthcheck not in the access log"
dc logs --no-log-prefix api 2>/dev/null | grep -q '"logger": *"uvicorn.access"' \
  && ok "access log present (JSON)" || bad "no access log lines"
dc logs --no-log-prefix api 2>/dev/null | grep -q '"msg": *"Running upgrade' \
  && ok "applied migrations are logged (Running upgrade …)" || bad "migrations not visible in logs"

# ---------- isolation / hardening ----------
say "isolation"
for s in api db worker backup; do
  [ -z "$(docker port "$(dc ps -q "$s")" 2>/dev/null)" ] && ok "$s has no published ports" || bad "$s ports: $(docker port "$(dc ps -q "$s")")"
done
[ "$(docker port "$(dc ps -q web)" | sort -u)" = "8080/tcp -> 127.0.0.1:$PORT" ] && ok "web publishes only 127.0.0.1:$PORT" || bad "web ports: $(docker port "$(dc ps -q web)")"
[ "$(dc exec -T api id -u | tr -d '\r')" = "10001" ] && ok "api runs as uid 10001" || bad "api uid"
[ "$(dc exec -T web id -u | tr -d '\r')" = "10001" ] && ok "web runs as uid 10001" || bad "web uid"
dc exec -T api sh -c 'touch /app/x' >/dev/null 2>&1 && bad "api rootfs is writable" || ok "api rootfs read-only"
out="$(dc exec -T web wget -q -T 3 -O /dev/null http://db:5432 2>&1 || true)"
echo "$out" | grep -q 'bad address' && ok "web cannot resolve/reach db (db only on the internal network)" || bad "web reaches db: $out"
enc="$(dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtX -c "show server_encoding"' | tr -d '\r')"
[ "$enc" = "UTF8" ] && ok "db encoding UTF8" || bad "db encoding: $enc"
dc logs api 2>/dev/null | grep -q 'alembic upgrade head' && ok "api ran alembic upgrade head on start" || bad "alembic on start"
dc exec -T api pf-entrypoint alembic current >/dev/null 2>&1 && ok "alembic current works in the api image (pf-entrypoint)" || bad "alembic current"
dc run --rm --no-deps -T worker python -c 'import app.worker, app.main' >/dev/null 2>&1 \
  && ok "worker image imports app.worker" || bad "worker import"
dc run --rm --no-deps -T bot python -c 'import pocket_bot.config, pocket_bot.handlers' >/dev/null 2>&1 \
  && ok "bot image imports pocket_bot" || bad "bot import"
[ "$(dc run --rm --no-deps -T bot id -u 2>/dev/null | tr -d '\r')" = "10001" ] \
  && ok "bot runs as uid 10001" || bad "bot uid"
# no token: one log line, container idles (no restart loop, no Telegram calls)
bot_cid="$(dc ps -q bot)"
if [ -n "$bot_cid" ] && [ "$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$bot_cid")" = "true 0" ] \
  && dc logs --no-log-prefix bot 2>/dev/null | grep -q 'TELEGRAM_BOT_TOKEN не задан'; then
  ok "bot without token idles (running, 0 restarts, one clear log line)"
else
  bad "bot without token: $(docker inspect -f '{{.State.Status}} restarts={{.RestartCount}}' "$bot_cid" 2>&1)"
fi
docker rm -f "${P}-badtoken" >/dev/null 2>&1 || true
dc run -d --no-deps --name "${P}-badtoken" -e TELEGRAM_BOT_TOKEN=not-a-token bot >/dev/null 2>&1
sleep 3
if [ "$(docker inspect -f '{{.State.Running}}' "${P}-badtoken" 2>/dev/null)" = "true" ] \
  && docker logs "${P}-badtoken" 2>&1 | grep -q 'неверный формат' \
  && ! docker logs "${P}-badtoken" 2>&1 | grep -q Traceback; then
  ok "bot with a malformed token: one clear error line, no traceback, no restart loop"
else
  bad "bot malformed token: $(docker logs "${P}-badtoken" 2>&1 | tail -n 3)"
fi
docker rm -f "${P}-badtoken" >/dev/null 2>&1 || true

# ---------- API down: web answers /api/* with JSON 503 ----------
say "API down"
dc stop api >/dev/null 2>&1
down_h="$(curl -s -D - -o "$T/down.json" "$BASE/api/v1/health" | tr -d '\r')"
echo "$down_h" | head -n1 | grep -q ' 503' && grep -q '"code":"service_unavailable"' "$T/down.json" \
  && echo "$down_h" | grep -qi '^retry-after: 5' \
  && ok "API down → 503 JSON service_unavailable with Retry-After" || bad "API down: $(echo "$down_h" | head -n1) $(cat "$T/down.json")"
echo "$down_h" | grep -qi '^server:' && bad "Server header on the API-down error page" \
  || ok "no Server header on the API-down error page"
dc start api >/dev/null 2>&1
wait_healthy api 240

# ---------- backup container ----------
say "backup container"
# crond must still run jobs with cap_drop ALL (+ SETUID/SETGID): one-off container, checked at the end.
dc run --rm --no-deps -T --entrypoint sh backup -c \
  'echo "* * * * * touch /tmp/cron-ok" >/etc/crontabs/root; crond -c /etc/crontabs; sleep 65; test -f /tmp/cron-ok' \
  >"$T/cronprobe.log" 2>&1 &
cron_probe=$!
dc exec -T backup cat /etc/crontabs/root | grep -q '^15 4 \* \* \* ' && ok "crontab uses BACKUP_CRON" || bad "crontab"
dc exec -T backup pgrep crond >/dev/null && ok "crond is running" || bad "crond"
dc exec -T backup sh -c '[ "$TZ" = Asia/Ho_Chi_Minh ] && [ "$(date +%Z)" = "+07" ]' && ok "backup TZ Asia/Ho_Chi_Minh" || bad "backup TZ"
if dc run --rm --no-deps -T --entrypoint sh backup /usr/local/lib/pf-backup/test_rotation.sh >"$T/rotation.log" 2>&1; then
  ok "rotation unit tests ($(grep -c '^ok - ' "$T/rotation.log") checks)"
else
  bad "rotation unit tests"; tail -n 20 "$T/rotation.log"
fi

# ---------- backup → restore round trip ----------
say "backup → restore round trip"
psql_db() { dc exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -v ON_ERROR_STOP=1 -AtX -c \"$1\"" | tr -d '\r'; }
psql_db "CREATE EXTENSION IF NOT EXISTS citext; CREATE EXTENSION IF NOT EXISTS pg_trgm;
  CREATE TABLE infra_roundtrip (id int PRIMARY KEY, label citext NOT NULL, amount numeric(28,8) NOT NULL);
  INSERT INTO infra_roundtrip VALUES (1, 'Кофе ₫ Привет', 150000.12345678), (2, 'vast.ai', 21.3);" >/dev/null
before="$(psql_db "SELECT string_agg(id||'|'||label||'|'||amount, ';' ORDER BY id) FROM infra_roundtrip")"
scripts/backup.sh run >"$T/backup.log" 2>&1 && ok "scripts/backup.sh run" || { bad "backup run"; cat "$T/backup.log"; }
n_daily="$(ls "$T/backups/daily" | grep -c '\.dump$' || true)"
[ "$n_daily" -ge 1 ] && [ -n "$(ls "$T/backups/weekly")" ] && [ -n "$(ls "$T/backups/monthly")" ] \
  && ok "dump in daily/ + promoted to weekly/ and monthly/" || bad "backup files: $(ls -R "$T/backups")"
[ "$(stat -c %a "$T/backups/daily/$(ls "$T/backups/daily" | grep '\.dump$' | tail -n1)")" = "600" ] \
  && ok "dump file mode 600" || bad "dump file mode"
st_out="$(scripts/backup.sh status 2>&1 || true)"
echo "$st_out" | grep -q '^last ok: *20' && ok "scripts/backup.sh status" || bad "backup status: $st_out"
dc exec -T backup pf-backup health >/dev/null && ok "backup health ok" || bad "backup health"

psql_db "DELETE FROM infra_roundtrip WHERE id = 1; UPDATE infra_roundtrip SET amount = 0.5; INSERT INTO infra_roundtrip VALUES (3, 'junk', 1);" >/dev/null
[ "$(psql_db "SELECT string_agg(id||'|'||label||'|'||amount, ';' ORDER BY id) FROM infra_roundtrip")" != "$before" ] \
  && ok "data changed after backup" || bad "data change"

scripts/restore.sh --check latest >"$T/check.log" 2>&1 && ok "restore --check (separate DB, live untouched)" || { bad "restore --check"; cat "$T/check.log"; }
[ "$(psql_db "SELECT count(*) FROM infra_roundtrip")" = "2" ] && ok "live DB untouched by --check" || bad "--check touched live DB"

scripts/restore.sh --yes latest >"$T/restore.log" 2>&1 && ok "scripts/restore.sh --yes latest" || { bad "restore"; cat "$T/restore.log"; }
after="$(psql_db "SELECT string_agg(id||'|'||label||'|'||amount, ';' ORDER BY id) FROM infra_roundtrip")"
[ "$after" = "$before" ] && ok "restored data identical ($after)" || bad "restored data: '$after' != '$before'"
[ -n "$(ls "$T/backups/pre-restore" 2>/dev/null)" ] && ok "safety dump in pre-restore/" || bad "pre-restore dump"
[ "$(code "$BASE/api/v1/health")" = "200" ] && ok "API healthy after restore" || bad "health after restore"

# restore an arbitrary host file (import/)
cp "$T/backups/daily/$(ls "$T/backups/daily" | grep '\.dump$' | tail -n1)" "$T/manual.dump"
scripts/restore.sh --check "$T/manual.dump" >/dev/null 2>&1 && ok "restore --check of a host file (import/)" || bad "restore host file"
(cd "$T" && "$PF_ROOT/scripts/restore.sh" --check manual.dump) >"$T/relcheck.log" 2>&1 \
  && ok "restore --check with a path relative to the current directory" || { bad "relative restore path"; tail -n 5 "$T/relcheck.log"; }

# corrupted archive must be rejected before touching the live DB
head -c 2000 "$T/manual.dump" >"$T/broken.dump"
if scripts/restore.sh --yes "$T/broken.dump" >"$T/broken.log" 2>&1; then bad "broken dump accepted"
else
  [ "$(psql_db "SELECT count(*) FROM infra_roundtrip")" = "2" ] && ok "truncated dump rejected, live DB intact" || bad "broken dump damaged DB"
fi
dc up -d api worker bot >/dev/null 2>&1 || true
wait_healthy api 240

# ---------- api healthcheck: database down = unhealthy ----------
say "api healthcheck semantics"
dc exec -T api pf-entrypoint healthcheck >/dev/null 2>&1 && ok "healthcheck ok with the database up" || bad "healthcheck with db up"
dc stop db >/dev/null 2>&1
hc="$(dc exec -T api pf-entrypoint healthcheck 2>&1 | tr -d '\r')"
case "$hc" in
  unhealthy*'"db": false'*) ok "database down → healthcheck fails (${hc:0:60}…)" ;;
  *) bad "database down → healthcheck: '$hc'" ;;
esac
dc start db >/dev/null 2>&1
wait_healthy db 120
for _ in $(seq 1 30); do dc exec -T api pf-entrypoint healthcheck >/dev/null 2>&1 && break; sleep 2; done
dc exec -T api pf-entrypoint healthcheck >/dev/null 2>&1 && ok "healthcheck recovers when the database is back" || bad "healthcheck after db restart"

# ---------- rollback: database newer than the code → clear hint, exit 3 ----------
say "migrations: unknown revision"
rev="$(psql_db "SELECT version_num FROM alembic_version")"
psql_db "UPDATE alembic_version SET version_num = '9999_from_newer_version'" >/dev/null
rc=0; dc run --rm --no-deps -T api migrate >"$T/unknown_rev.log" 2>&1 || rc=$?
psql_db "UPDATE alembic_version SET version_num = '$rev'" >/dev/null
[ "$rc" = "3" ] && grep -q 'docs/UPDATE.md' "$T/unknown_rev.log" && grep -q -- '--restore' "$T/unknown_rev.log" \
  && ok "unknown DB revision → exit 3 with the rollback hint" || { bad "unknown revision: rc=$rc"; tail -n 5 "$T/unknown_rev.log"; }
dc run --rm --no-deps -T api migrate >"$T/migrate_again.log" 2>&1 && grep -q 'no new migrations' "$T/migrate_again.log" \
  && ok "migrate is idempotent and says so" || { bad "migrate again"; tail -n 5 "$T/migrate_again.log"; }

# ---------- remote copy over SSH ----------
if [ "${SKIP_REMOTE:-0}" != "1" ]; then
  say "remote rsync/scp over SSH (sidecar sshd)"
  docker run --rm -v "$T/ssh:/k" --entrypoint sh "$(dc images -q backup | head -n1)" -c \
    'rm -f /k/id_ed25519 /k/id_ed25519.pub && ssh-keygen -q -t ed25519 -N "" -f /k/id_ed25519' >/dev/null
  docker rm -f "$SSHD" >/dev/null 2>&1 || true
  docker run -d --name "$SSHD" --network "${P}_default" --network-alias sshd \
    -e PUBKEY="$(cat "$T/ssh/id_ed25519.pub")" alpine:3 sh -c '
      apk add --no-cache openssh rsync >/dev/null && ssh-keygen -A >/dev/null &&
      adduser -D bk && echo "bk:*" | chpasswd -e >/dev/null &&
      mkdir -p /home/bk/.ssh /home/bk/pocket && echo "$PUBKEY" >/home/bk/.ssh/authorized_keys &&
      chown -R bk:bk /home/bk && chmod 700 /home/bk/.ssh && chmod 600 /home/bk/.ssh/authorized_keys &&
      exec /usr/sbin/sshd -D -e' >/dev/null
  for _ in $(seq 1 60); do docker exec "$SSHD" sh -c 'nc -z 127.0.0.1 22' >/dev/null 2>&1 && break; sleep 1; done
  if dc exec -T -e BACKUP_REMOTE=bk@sshd:/home/bk/pocket backup pf-backup run >"$T/remote.log" 2>&1; then
    ok "pf-backup run with BACKUP_REMOTE (rsync over ssh, accept-new host key)"
  else bad "remote rsync"; cat "$T/remote.log"; fi
  r_daily="$(docker exec "$SSHD" sh -c 'ls /home/bk/pocket/daily | grep -c dump' || true)"
  l_daily="$(ls "$T/backups/daily" | grep -c '\.dump$')"
  [ "$r_daily" = "$l_daily" ] && ok "remote mirror has $r_daily daily dumps (= local)" || bad "remote mirror $r_daily != $l_daily"
  docker exec "$SSHD" sh -c 'ls -a /home/bk/pocket' | grep -qE '^(\.state|pre-restore)$' \
    && bad "state/pre-restore leaked to remote" || ok "state and pre-restore not mirrored"
  docker exec "$SSHD" sh -c 'mkdir -p /home/bk/scp && chown bk /home/bk/scp'
  dc exec -T -e BACKUP_REMOTE=bk@sshd:/home/bk/scp -e BACKUP_REMOTE_METHOD=scp backup pf-backup remote >/dev/null 2>&1 \
    && [ "$(docker exec "$SSHD" sh -c 'ls /home/bk/scp | grep -c dump')" = "1" ] \
    && ok "scp method copies the newest dump" || bad "scp method"
  docker rm -f "$SSHD" >/dev/null 2>&1 || true
fi

# ---------- standalone profile: edge Caddy with HTTPS ----------
if [ "${SKIP_EDGE:-0}" != "1" ]; then
  say "standalone profile (edge Caddy, PF_DOMAIN=localhost → internal CA)"
  dc --profile standalone up -d caddy >/dev/null
  wait_healthy caddy 90
  st=""
  for _ in $(seq 1 30); do
    st="$(curl -sk -o /dev/null -w '%{http_code}' --resolve "localhost:${TLS_PORT}:127.0.0.1" "https://localhost:${TLS_PORT}/api/v1/health" || true)"
    [ "$st" = "200" ] && break; sleep 2
  done
  [ "$st" = "200" ] && ok "HTTPS https://localhost:${TLS_PORT}/api/v1/health → 200 through edge Caddy" || bad "edge https ($st)"
  curl -sk --resolve "localhost:${TLS_PORT}:127.0.0.1" "https://localhost:${TLS_PORT}/" | grep -q 'id="root"' \
    && ok "edge serves the SPA over HTTPS" || bad "edge SPA"
  loc="$(curl -s -o /dev/null -w '%{redirect_url}' "http://127.0.0.1:${HTTP_EDGE_PORT}/" -H 'Host: localhost')"
  case "$loc" in https://localhost*) ok "edge redirects HTTP → HTTPS ($loc)" ;; *) bad "edge http redirect ($loc)" ;; esac
  curl -sk -D - -o /dev/null --resolve "localhost:${TLS_PORT}:127.0.0.1" "https://localhost:${TLS_PORT}/" \
    | tr -d '\r' | grep -qi '^server:' && bad "edge sends a Server header" || ok "edge: no Server header"
  # Client IP through the edge: web trusts the edge on its internal listener :8081, so the API
  # sees the edge's client (not the edge container) although TRUSTED_PROXIES is the default.
  # The probe client gets a public-looking address (a network in TEST-NET-2 joined by the edge)
  # and sends a forged X-Forwarded-For, which the edge must discard.
  probe="/api/v1/edge-probe-$RANDOM$RANDOM"
  pnet="${P}-probe"
  docker network rm "$pnet" >/dev/null 2>&1 || true
  docker network create --subnet 198.51.100.0/24 "$pnet" >/dev/null
  edge_cid="$(dc ps -q caddy)"
  docker network connect "$pnet" "$edge_cid"
  edge_pip="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$pnet\").IPAddress}}" "$edge_cid")"
  edge_ip="$(docker inspect -f "{{(index .NetworkSettings.Networks \"${P}_default\").IPAddress}}" "$edge_cid")"
  docker run --rm --network "$pnet" --ip 198.51.100.50 --entrypoint python \
    "$(dc images -q api | head -n1)" -c '
import socket, ssl, sys
ctx = ssl._create_unverified_context()
s = ctx.wrap_socket(socket.create_connection((sys.argv[1], 443), timeout=10), server_hostname="localhost")
s.sendall(("GET %s HTTP/1.1\r\nHost: localhost\r\nX-Forwarded-For: 203.0.113.99\r\n"
           "Connection: close\r\n\r\n" % sys.argv[2]).encode())
print(s.recv(64).split(b"\r\n")[0].decode())' "$edge_pip" "$probe" >"$T/edgeprobe.log" 2>&1 || true
  docker network disconnect "$pnet" "$edge_cid" >/dev/null 2>&1 || true
  docker network rm "$pnet" >/dev/null 2>&1 || true
  sleep 1
  seen="$(dc logs --no-log-prefix api 2>/dev/null | grep -F "\"$probe\"" | tail -n1 | jq -r '.client' 2>/dev/null | sed 's/:[0-9]*$//')"
  [ "$seen" = "198.51.100.50" ] \
    && ok "edge → web:8081 keeps the client IP, forged XFF dropped (API sees $seen, edge is $edge_ip)" \
    || bad "client IP through the edge: API sees '$seen' (want 198.51.100.50; edge $edge_ip; $(cat "$T/edgeprobe.log"))"
fi

# ---------- crond under the backup hardening (started above) ----------
if wait "$cron_probe"; then ok "backup: crond runs jobs with cap_drop ALL + SETUID/SETGID"
else bad "backup: crond job did not run under the hardening"; tail -n 5 "$T/cronprobe.log"; fi

# ---------- summary ----------
echo
if [ "${#FAILS[@]}" -eq 0 ]; then
  printf '\033[32mALL %d CHECKS PASSED\033[0m\n' "$PASS"
else
  printf '\033[31m%d passed, %d FAILED:\033[0m\n' "$PASS" "${#FAILS[@]}"
  printf '  - %s\n' "${FAILS[@]}"
  exit 1
fi

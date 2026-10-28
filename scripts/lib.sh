# shellcheck shell=bash
# common helpers for scripts/*.sh (bash). Not executable on its own.
# Compose options can be injected for tests/other stacks:
#   PF_COMPOSE_OPTS="-p pf-infra --env-file /tmp/pf.env" scripts/backup.sh run
set -euo pipefail

# Directory the user ran the script from (relative file arguments are resolved against it).
PF_ORIG_PWD="$PWD"
PF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PF_ROOT"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# shellcheck disable=SC2086
dc() { docker compose ${PF_COMPOSE_OPTS:-} "$@"; }

need_docker() {
  command -v docker >/dev/null || die "docker не найден (см. docs/INSTALL.md)"
  docker compose version >/dev/null 2>&1 || die "нужен плагин docker compose v2+"
  # healthcheck.start_interval in docker-compose.yml needs Docker Engine 25+
  local v
  v="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
  case "$v" in
    ''|*[!0-9.]*) ;;  # unknown / no access to the daemon: compose reports it itself
    *) [ "${v%%.*}" -ge 25 ] || die "нужен Docker Engine 25+ (сейчас $v, см. docs/INSTALL.md)" ;;
  esac
}

is_running() { dc ps --status running --services 2>/dev/null | grep -qx "$1"; }

# Run a command in the backup container (exec if it runs, otherwise a one-off container).
in_backup() {
  if is_running backup; then
    dc exec -T backup "$@"
  else
    dc run --rm -T backup "$@"
  fi
}

# Absolute path of a file argument given relative to the caller's directory (as typed otherwise).
user_path() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *) printf '%s/%s' "$PF_ORIG_PWD" "$1" ;;
  esac
}

wait_healthy() {  # wait_healthy <service> [timeout_s]
  local svc="$1" t="${2:-180}" i=0 cid st
  cid="$(dc ps -q "$svc")"
  [ -n "$cid" ] || die "сервис $svc не запущен"
  while :; do
    st="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")"
    [ "$st" = "healthy" ] && return 0
    if [ "$(docker inspect -f '{{.RestartCount}}' "$cid")" -ge 3 ]; then
      dc logs --tail 50 "$svc" >&2 || true
      die "$svc перезапускается в цикле (см. логи выше)"
    fi
    i=$((i + 2)); [ "$i" -ge "$t" ] && { dc logs --tail 50 "$svc" >&2 || true; die "$svc не стал healthy за ${t}s (статус: $st)"; }
    sleep 2
  done
}

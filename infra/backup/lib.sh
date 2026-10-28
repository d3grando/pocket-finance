# shellcheck shell=sh
# shared helpers for pf-backup / pf-restore (busybox sh, postgres:16-alpine).

# Connection: libpq PG* variables, falling back to the postgres image's POSTGRES_* names.
export PGHOST="${PGHOST:-db}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-${POSTGRES_USER:-postgres}}"
export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-}}"
export PGDATABASE="${PGDATABASE:-${POSTGRES_DB:-postgres}}"
export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-10}"

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_PREFIX="${BACKUP_PREFIX:-pocket}"
STATE_DIR="$BACKUP_DIR/.state"

umask 077

log() { printf '%s pf-backup: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"; }
err() { printf '%s pf-backup: ERROR: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >&2; }
die() { err "$*"; exit 1; }

is_true() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1 | true | yes | on) return 0 ;;
    *) return 1 ;;
  esac
}

ensure_dirs() {
  mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly" "$BACKUP_DIR/monthly" \
    "$BACKUP_DIR/pre-restore" "$STATE_DIR"
}

# One backup/restore at a time (fd 9 stays open until the process exits).
take_lock() {
  ensure_dirs
  exec 9>"$STATE_DIR/lock"
  flock -n 9 || die "another backup/restore is running (lock $STATE_DIR/lock)"
}

wait_db() {
  i=0
  until pg_isready -q -d postgres; do
    i=$((i + 1))
    [ "$i" -ge "${DB_WAIT_SECONDS:-60}" ] && die "database $PGHOST:$PGPORT is not ready"
    sleep 1
  done
}

# Full read of a custom-format archive: TOC + every data block (catches truncation/corruption).
verify_archive() {
  pg_restore --list "$1" >/dev/null || return 1
  pg_restore --file=/dev/null "$1" || return 1
}

human_size() {
  s=$(wc -c <"$1" | tr -d ' ')
  if [ "$s" -ge 1048576 ]; then echo "$((s / 1048576)) MiB"
  elif [ "$s" -ge 1024 ]; then echo "$((s / 1024)) KiB"
  else echo "$s B"; fi
}

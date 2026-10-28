#!/bin/sh
# restore a Pocket Finance backup (runs inside the `backup` container).
#
#   pf-restore [--yes] [--target-db NAME] [--no-safety-backup] <latest|FILE>
#
#   FILE: absolute path, or relative to BACKUP_DIR (e.g. daily/pocket_2026-10-27_033000.dump,
#         weekly/pocket_2026-W44.dump), or a bare file name searched in daily/weekly/monthly.
#   latest: newest file in daily/.
#
# Steps: verify archive → (target = live DB) safety dump into pre-restore/ → DROP DATABASE
# (WITH FORCE: terminates connections) → CREATE DATABASE (UTF8, template0) → pg_restore
# --single-transaction --exit-on-error --no-owner --no-acl. Stop api/worker/bot before
# restoring the live DB (scripts/restore.sh does it) — the API re-runs migrations on start.
set -eu
. /usr/local/lib/pf-backup/lib.sh

usage() { sed -n '2,14p' "$0"; exit 2; }

yes=0
safety=1
target="$PGDATABASE"
src=""
while [ $# -gt 0 ]; do
  case "$1" in
    -y | --yes) yes=1 ;;
    --no-safety-backup) safety=0 ;;
    --target-db) shift; target="${1:?--target-db needs a name}" ;;
    --target-db=*) target="${1#*=}" ;;
    -h | --help) usage ;;
    -*) die "unknown option: $1" ;;
    *) [ -z "$src" ] || die "only one backup file may be given"; src="$1" ;;
  esac
  shift
done
[ -n "$src" ] || usage
printf '%s' "$target" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]{0,62}$' || die "bad database name: $target"

resolve() {
  case "$1" in
    latest)
      f=$(ls -1 "$BACKUP_DIR/daily" 2>/dev/null | grep -E '\.dump$' | sort | tail -n 1)
      [ -n "$f" ] || die "no backups in $BACKUP_DIR/daily"
      echo "$BACKUP_DIR/daily/$f" ;;
    /*) echo "$1" ;;
    */*) echo "$BACKUP_DIR/$1" ;;
    *)
      for d in daily weekly monthly pre-restore; do
        if [ -f "$BACKUP_DIR/$d/$1" ]; then echo "$BACKUP_DIR/$d/$1"; return 0; fi
      done
      echo "$BACKUP_DIR/$1" ;;
  esac
}

file=$(resolve "$src")
[ -f "$file" ] || die "backup not found: $file"

live=0
[ "$target" = "$PGDATABASE" ] && live=1
if [ "$live" -eq 1 ] && [ "$yes" -ne 1 ] && ! is_true "${PF_RESTORE_YES:-}"; then
  die "restoring into the LIVE database '$target' replaces all data: re-run with --yes"
fi

take_lock
wait_db
log "verify $file"
verify_archive "$file" || die "archive is damaged: $file"

exists=$(psql -d postgres -AtX -v ON_ERROR_STOP=1 \
  -c "SELECT 1 FROM pg_database WHERE datname = '$target'")
if [ "$exists" = "1" ] && [ "$safety" -eq 1 ]; then
  ts=$(date +%Y-%m-%d_%H%M%S)
  pre="$BACKUP_DIR/pre-restore/${BACKUP_PREFIX}_pre-restore_${target}_${ts}.dump"
  log "safety dump of current '$target' → pre-restore/$(basename "$pre")"
  pg_dump --format=custom --compress=6 --file="$pre.partial" "$target" || die "safety dump failed"
  mv -f "$pre.partial" "$pre"
fi

log "recreate database '$target'"
psql -d postgres -AtX -v ON_ERROR_STOP=1 -q \
  -c "DROP DATABASE IF EXISTS \"$target\" WITH (FORCE)" \
  -c "CREATE DATABASE \"$target\" OWNER \"$PGUSER\" ENCODING 'UTF8' TEMPLATE template0"

log "pg_restore $(basename "$file") → '$target'"
pg_restore --dbname="$target" --no-owner --no-acl --exit-on-error --single-transaction "$file" ||
  die "pg_restore failed — '$target' is empty now; restore again or use pre-restore/ dump"

tables=$(psql -d "$target" -AtX -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
rev=$(psql -d "$target" -AtX -c "SELECT version_num FROM alembic_version" 2>/dev/null || true)
log "restored '$target' from $(basename "$file"): $tables tables, alembic ${rev:-none}"

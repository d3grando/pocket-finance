#!/bin/sh
# Pocket Finance database backup (runs inside the `backup` container).
#
#   pf-backup [run]   pg_dump -Fc → verify → daily/ (+ first of ISO week → weekly/, first of month
#                     → monthly/, hard links) → rotate (7 days / 4 weeks / 6 months) → remote copy
#   pf-backup rotate  rotation only
#   pf-backup remote  push to the remote host only (BACKUP_REMOTE)
#   pf-backup list    list backups
#   pf-backup status  last success / error, counts
#   pf-backup health  exit 0 if the last backup is fresh (container HEALTHCHECK)
#
# Env (defaults): BACKUP_DIR=/backups BACKUP_PREFIX=pocket BACKUP_KEEP_DAILY=7
#   BACKUP_KEEP_WEEKLY=4 BACKUP_KEEP_MONTHLY=6 BACKUP_VERIFY=true BACKUP_MAX_AGE_HOURS=30
#   BACKUP_REMOTE=user@host:/path (empty = off) BACKUP_REMOTE_METHOD=rsync|scp
#   BACKUP_REMOTE_PORT=22 BACKUP_SSH_KEY=/ssh/id_ed25519 BACKUP_SSH_KNOWN_HOSTS=/ssh/known_hosts
#   BACKUP_REMOTE_DELETE=true (rsync: mirror local rotation remotely)
#   BACKUP_NOW=<unix ts> (tests: pretend "now"), PG*/POSTGRES_* for the connection.
set -eu
. /usr/local/lib/pf-backup/lib.sh

KEEP_DAILY="${BACKUP_KEEP_DAILY:-7}"
KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-4}"
KEEP_MONTHLY="${BACKUP_KEEP_MONTHLY:-6}"
KEEP_PRE_RESTORE="${BACKUP_KEEP_PRE_RESTORE:-5}"

now_ts() { echo "${BACKUP_NOW:-$(date +%s)}"; }
fmt_ts() { date -d "@$1" "+$2"; }

# Keep files of the N most recent distinct days (daily/: <prefix>_YYYY-mm-dd_HHMMSS.dump).
prune_daily() {
  dir="$BACKUP_DIR/daily"
  keep_days=$(ls -1 "$dir" 2>/dev/null | sed -n "s/^${BACKUP_PREFIX}_\([0-9-]\{10\}\)_[0-9]\{6\}\.dump\$/\1/p" \
    | sort -ru | head -n "$KEEP_DAILY")
  for f in $(ls -1 "$dir" 2>/dev/null | grep -E "^${BACKUP_PREFIX}_[0-9-]{10}_[0-9]{6}\.dump\$" || true); do
    d=$(printf '%s' "$f" | cut -c $((${#BACKUP_PREFIX} + 2))-$((${#BACKUP_PREFIX} + 11)))
    if ! printf '%s\n' "$keep_days" | grep -qx "$d"; then
      rm -f "$dir/$f" && log "rotate: removed daily/$f"
    fi
  done
}

# Keep the newest N files (names sort chronologically: YYYY-Www, YYYY-mm, timestamps).
prune_newest() {
  dir="$1"; keep="$2"; pattern="$3"
  ls -1 "$dir" 2>/dev/null | grep -E "$pattern" | sort -r | tail -n +"$((keep + 1))" |
    while read -r f; do rm -f "$dir/$f" && log "rotate: removed $(basename "$dir")/$f"; done
}

cmd_rotate() {
  ensure_dirs
  prune_daily
  prune_newest "$BACKUP_DIR/weekly" "$KEEP_WEEKLY" "^${BACKUP_PREFIX}_[0-9]{4}-W[0-9]{2}\.dump\$"
  prune_newest "$BACKUP_DIR/monthly" "$KEEP_MONTHLY" "^${BACKUP_PREFIX}_[0-9]{4}-[0-9]{2}\.dump\$"
  prune_newest "$BACKUP_DIR/pre-restore" "$KEEP_PRE_RESTORE" "^${BACKUP_PREFIX}_pre-restore_.*\.dump\$"
  find "$BACKUP_DIR" -name '*.partial' -mmin +360 -exec rm -f {} + 2>/dev/null || true
}

# Hard link (same filesystem, no extra space) or copy.
link_or_copy() { ln -f "$1" "$2" 2>/dev/null || cp -p "$1" "$2"; }

promote() {
  src="$1"; ts="$2"
  week="$BACKUP_DIR/weekly/${BACKUP_PREFIX}_$(fmt_ts "$ts" %G-W%V).dump"
  month="$BACKUP_DIR/monthly/${BACKUP_PREFIX}_$(fmt_ts "$ts" %Y-%m).dump"
  if [ ! -e "$week" ]; then link_or_copy "$src" "$week" && log "weekly: $(basename "$week")"; fi
  if [ ! -e "$month" ]; then link_or_copy "$src" "$month" && log "monthly: $(basename "$month")"; fi
}

ssh_setup() {
  key_src="${BACKUP_SSH_KEY:-/ssh/id_ed25519}"
  if [ ! -r "$key_src" ]; then
    err "remote: SSH key $key_src is not readable (mount it via BACKUP_SSH_DIR)"; return 1
  fi
  # ssh refuses group/world-readable keys: use a private copy.
  SSH_KEY_COPY=$(mktemp /tmp/pf-ssh-key.XXXXXX)
  cat "$key_src" >"$SSH_KEY_COPY"
  chmod 600 "$SSH_KEY_COPY"
  kh="${BACKUP_SSH_KNOWN_HOSTS:-/ssh/known_hosts}"
  if [ -s "$kh" ]; then strict=yes; else kh="$STATE_DIR/known_hosts"; strict=accept-new; fi
  SSH_OPTS="-i $SSH_KEY_COPY -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=20 \
-o ServerAliveInterval=15 -o StrictHostKeyChecking=$strict -o UserKnownHostsFile=$kh"
}

cmd_remote() {
  target="${BACKUP_REMOTE:-}"
  [ -n "$target" ] || { log "remote: BACKUP_REMOTE is empty — skipped"; return 0; }
  ensure_dirs
  ssh_setup || return 1
  port="${BACKUP_REMOTE_PORT:-22}"
  method="${BACKUP_REMOTE_METHOD:-rsync}"
  rc=0
  case "$method" in
    rsync)
      del=""
      if is_true "${BACKUP_REMOTE_DELETE:-true}"; then del="--delete-after"; fi
      # shellcheck disable=SC2086
      rsync -aH $del --exclude='.*' --exclude='*.partial' --exclude='pre-restore/' \
        -e "ssh -p $port $SSH_OPTS" "$BACKUP_DIR/" "${target%/}/" || rc=$?
      ;;
    scp)
      latest=$(ls -1 "$BACKUP_DIR/daily" | grep -E '\.dump$' | sort | tail -n 1)
      [ -n "$latest" ] || { err "remote: nothing to copy"; rm -f "$SSH_KEY_COPY"; return 1; }
      # shellcheck disable=SC2086
      scp -P "$port" $SSH_OPTS "$BACKUP_DIR/daily/$latest" "${target%/}/" || rc=$?
      ;;
    *) rm -f "$SSH_KEY_COPY"; err "remote: unknown BACKUP_REMOTE_METHOD=$method (rsync|scp)"; return 1 ;;
  esac
  rm -f "$SSH_KEY_COPY"
  [ "$rc" -eq 0 ] || { err "remote: $method to $target failed (exit $rc)"; return "$rc"; }
  log "remote: $method → $target ok"
}

RUN_ERROR_RECORDED=0
record_error() {
  RUN_ERROR_RECORDED=1
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  date +%s >"$STATE_DIR/last_error_ts" 2>/dev/null || true
  printf '%s\n' "$*" >"$STATE_DIR/last_error" 2>/dev/null || true
}

# Any failed run marks the container unhealthy, including failures before pg_dump (database
# unreachable, another backup holding the lock): docs/BACKUP.md promises "unhealthy when the
# last attempt failed".
on_run_exit() {
  rc=$?
  if [ "$rc" -ne 0 ] && [ "$RUN_ERROR_RECORDED" -eq 0 ]; then
    record_error "backup failed before the dump (exit $rc): database unreachable or another backup/restore running — see logs"
  fi
  return "$rc"
}

cmd_run() {
  trap on_run_exit EXIT
  take_lock
  ts=$(now_ts)
  name="${BACKUP_PREFIX}_$(fmt_ts "$ts" %Y-%m-%d_%H%M%S).dump"
  out="$BACKUP_DIR/daily/$name"
  tmp="$BACKUP_DIR/daily/.$name.partial"
  wait_db
  log "dump $PGDATABASE@$PGHOST → daily/$name"
  if ! pg_dump --format=custom --compress=6 --file="$tmp" "$PGDATABASE"; then
    rm -f "$tmp"; record_error "pg_dump failed"; die "pg_dump failed"
  fi
  if is_true "${BACKUP_VERIFY:-true}"; then
    if ! verify_archive "$tmp"; then
      rm -f "$tmp"; record_error "archive verification failed"; die "archive verification failed"
    fi
  fi
  mv -f "$tmp" "$out"
  log "ok: daily/$name ($(human_size "$out"))"
  promote "$out" "$ts"
  cmd_rotate
  date +%s >"$STATE_DIR/last_ok_ts"
  printf '%s\n' "daily/$name" >"$STATE_DIR/last_ok"
  if [ -n "${BACKUP_REMOTE:-}" ]; then
    if ! cmd_remote; then record_error "remote copy failed"; exit 3; fi
  fi
  rm -f "$STATE_DIR/last_error" "$STATE_DIR/last_error_ts"
}

cmd_list() {
  ensure_dirs
  for d in daily weekly monthly pre-restore; do
    ls -1 "$BACKUP_DIR/$d" 2>/dev/null | grep -E '\.dump$' | sort | while read -r f; do
      printf '%-12s %-48s %s\n' "$d" "$f" "$(human_size "$BACKUP_DIR/$d/$f")"
    done
  done
}

cmd_status() {
  ensure_dirs
  if [ -f "$STATE_DIR/last_ok_ts" ]; then
    ok=$(cat "$STATE_DIR/last_ok_ts")
    echo "last ok:    $(date -d "@$ok" '+%Y-%m-%d %H:%M:%S %Z') ($(cat "$STATE_DIR/last_ok"))"
  else
    echo "last ok:    never"
  fi
  if [ -f "$STATE_DIR/last_error" ]; then
    echo "last error: $(date -d "@$(cat "$STATE_DIR/last_error_ts")" '+%Y-%m-%d %H:%M:%S %Z') — $(cat "$STATE_DIR/last_error")"
  fi
  for d in daily weekly monthly pre-restore; do
    n=$(ls -1 "$BACKUP_DIR/$d" 2>/dev/null | grep -cE '\.dump$' || true)
    echo "$d: $n"
  done
  echo "schedule:   ${BACKUP_CRON:-?} (${TZ:-UTC}); remote: ${BACKUP_REMOTE:-off}"
}

cmd_health() {
  max=$(( ${BACKUP_MAX_AGE_HOURS:-30} * 3600 ))
  now=$(date +%s)
  if [ -f "$STATE_DIR/last_error_ts" ]; then
    e=$(cat "$STATE_DIR/last_error_ts"); o=$(cat "$STATE_DIR/last_ok_ts" 2>/dev/null || echo 0)
    if [ "$e" -ge "$o" ]; then echo "unhealthy: $(cat "$STATE_DIR/last_error")"; exit 1; fi
  fi
  if [ -f "$STATE_DIR/last_ok_ts" ]; then
    age=$((now - $(cat "$STATE_DIR/last_ok_ts")))
    [ "$age" -le "$max" ] || { echo "unhealthy: last backup ${age}s ago"; exit 1; }
    echo "ok: last backup ${age}s ago"; exit 0
  fi
  started=$(cat /tmp/pf-backup-started 2>/dev/null || echo "$now")
  [ $((now - started)) -le "$max" ] || { echo "unhealthy: no backups yet"; exit 1; }
  echo "ok: no backups yet (scheduled)"
}

case "${1:-run}" in
  run) cmd_run ;;
  rotate) take_lock; cmd_rotate ;;
  remote) take_lock; cmd_remote || exit 3 ;;
  list) cmd_list ;;
  status) cmd_status ;;
  health) cmd_health ;;
  -h | --help | help) sed -n '2,20p' "$0" ;;
  *) die "unknown command: $1 (run|rotate|remote|list|status|health)" ;;
esac

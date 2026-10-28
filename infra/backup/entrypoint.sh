#!/bin/sh
# `backup` container entrypoint.
#   cron (default)  schedule `pf-backup run` with busybox crond (BACKUP_CRON in BACKUP_TZ/TZ);
#                   BACKUP_ON_START=true also runs one backup right away
#   anything else   exec'ed (pf-backup ..., pf-restore ..., sh)
set -eu
. /usr/local/lib/pf-backup/lib.sh

export TZ="${TZ:-${BACKUP_TZ:-UTC}}"

if [ "${1:-cron}" != "cron" ]; then
  exec "$@"
fi

ensure_dirs
date +%s >/tmp/pf-backup-started
# crond starts jobs with an empty environment: hand over ours through a private file.
export -p | grep -E "^export (PG[A-Z]*|POSTGRES_[A-Z]*|BACKUP_[A-Z_]*|TZ)=" >/tmp/pf-backup.env
chmod 600 /tmp/pf-backup.env
mkdir -p /etc/crontabs
schedule="${BACKUP_CRON:-30 3 * * *}"
printf '%s . /tmp/pf-backup.env; /usr/local/bin/pf-backup run >/proc/1/fd/1 2>/proc/1/fd/2\n' \
  "$schedule" >/etc/crontabs/root
log "scheduled: '$schedule' ($TZ), dir $BACKUP_DIR, remote ${BACKUP_REMOTE:-off}"

if is_true "${BACKUP_ON_START:-false}"; then
  pf-backup run || err "initial backup failed"
fi

exec crond -f -d "${BACKUP_CRON_LOGLEVEL:-8}" -c /etc/crontabs

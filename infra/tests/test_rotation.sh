#!/bin/sh
# offline tests for pf-backup rotation/promotion/remote mirroring.
# Runs inside the backup image without a database (pg_* are stubbed):
#   docker run --rm --entrypoint sh pocket-backup /usr/local/lib/pf-backup/test_rotation.sh
set -eu
fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok - $*"; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"
cat >"$work/bin/pg_dump" <<'SH'
#!/bin/sh
# stub: writes a fake archive to --file=
for a in "$@"; do case "$a" in --file=*) f="${a#--file=}" ;; esac; done
echo "fake dump $BACKUP_NOW" >"$f"
SH
printf '#!/bin/sh\nexit 0\n' >"$work/bin/pg_isready"
printf '#!/bin/sh\nexit 0\n' >"$work/bin/pg_restore"
chmod +x "$work/bin/"*
export PATH="$work/bin:$PATH"
export BACKUP_DIR="$work/backups" BACKUP_PREFIX=pocket TZ=Asia/Ho_Chi_Minh
unset BACKUP_REMOTE || true

count() { ls -1 "$BACKUP_DIR/$1" 2>/dev/null | grep -c '\.dump$' || true; }

# 1) 400 days of nightly backups at 03:30 local time, starting 2025-01-01.
start=$(date -d '2025-01-01 03:30:00' +%s)
day=0
while [ "$day" -lt 400 ]; do
  BACKUP_NOW=$((start + day * 86400)) pf-backup run >/dev/null
  [ "$(count daily)" -le 7 ] || fail "daily > 7 on day $day"
  [ "$(count weekly)" -le 4 ] || fail "weekly > 4 on day $day"
  [ "$(count monthly)" -le 6 ] || fail "monthly > 6 on day $day"
  day=$((day + 1))
done
last=$((start + 399 * 86400))
[ "$(count daily)" -eq 7 ] || fail "daily != 7: $(count daily)"
[ "$(count weekly)" -eq 4 ] || fail "weekly != 4: $(count weekly)"
[ "$(count monthly)" -eq 6 ] || fail "monthly != 6: $(count monthly)"
pass "counts 7/4/6 after 400 days"

newest="pocket_$(date -d "@$last" +%Y-%m-%d)_033000.dump"
[ -f "$BACKUP_DIR/daily/$newest" ] || fail "newest daily missing: $newest"
oldest_kept="pocket_$(date -d "@$((last - 6 * 86400))" +%Y-%m-%d)_033000.dump"
[ -f "$BACKUP_DIR/daily/$oldest_kept" ] || fail "7th daily missing: $oldest_kept"
pass "daily keeps the last 7 days"

wk="pocket_$(date -d "@$last" +%G-W%V).dump"
[ -f "$BACKUP_DIR/weekly/$wk" ] || fail "current week missing: $wk"
mo="pocket_$(date -d "@$last" +%Y-%m).dump"
[ -f "$BACKUP_DIR/monthly/$mo" ] || fail "current month missing: $mo"
# monthly = first backup of the month (hard link of that day's daily dump content)
grep -q "$(date -d "$(date -d "@$last" +%Y-%m)-01 03:30:00" +%s)" "$BACKUP_DIR/monthly/$mo" ||
  fail "monthly is not the first backup of the month"
expected_months=$(i=0; while [ $i -lt 6 ]; do date -d "@$((last - i * 28 * 86400))" +%Y-%m; i=$((i + 1)); done | sort -u | wc -l)
[ "$expected_months" -ge 5 ] || fail "test self-check"
pass "weekly/monthly hold the current period (first backup of the period)"

# hard links: the current month file shares an inode with a daily or weekly file when still kept
ino=$(stat -c %i "$BACKUP_DIR/weekly/$wk")
found=0
for f in "$BACKUP_DIR"/daily/*.dump; do [ "$(stat -c %i "$f")" = "$ino" ] && found=1; done
[ "$found" -eq 1 ] || fail "weekly is not a hard link of a daily dump"
pass "promotion uses hard links"

# 2) two backups on the same day are both kept; still 7 distinct days.
BACKUP_NOW=$((last + 3600 * 5)) pf-backup run >/dev/null
[ "$(count daily)" -eq 8 ] || fail "same-day manual backup should be kept (got $(count daily))"
days=$(ls -1 "$BACKUP_DIR/daily" | cut -c8-17 | sort -u | wc -l)
[ "$days" -eq 7 ] || fail "distinct days != 7: $days"
pass "extra same-day backup kept, 7 distinct days"

# 3) no partial files left, state recorded, health ok.
[ -z "$(find "$BACKUP_DIR" -name '*.partial')" ] || fail "partial files left"
[ -f "$BACKUP_DIR/.state/last_ok" ] || fail "last_ok missing"
pf-backup health >/dev/null || fail "health should be ok"
pass "state + health"

# 4) remote mirror (rsync to a local dir; ssh is not used for local targets).
mkdir -p "$work/ssh" "$work/remote"
echo "dummy" >"$work/ssh/id_ed25519"
echo "stale" >"$work/remote/old.dump"
BACKUP_REMOTE="$work/remote" BACKUP_SSH_KEY="$work/ssh/id_ed25519" pf-backup remote >/dev/null
[ "$(ls -1 "$work/remote/daily" | grep -c '\.dump$')" -eq 8 ] || fail "remote daily mirror"
[ "$(ls -1 "$work/remote/monthly" | grep -c '\.dump$')" -eq 6 ] || fail "remote monthly mirror"
[ ! -e "$work/remote/old.dump" ] || fail "rsync --delete-after did not mirror deletions"
[ ! -e "$work/remote/.state" ] || fail "state dir must not be mirrored"
pass "remote rsync mirror with deletions"

# 5) remote failure is reported (exit 3) and makes health fail.
set +e
BACKUP_NOW=$((last + 3600 * 6)) BACKUP_REMOTE="nobody@127.0.0.1:/nowhere" BACKUP_REMOTE_PORT=1 \
  BACKUP_SSH_KEY="$work/ssh/id_ed25519" pf-backup run >/dev/null 2>&1
rc=$?
set -e
[ "$rc" -eq 3 ] || fail "remote failure exit code should be 3, got $rc"
if pf-backup health >/dev/null; then fail "health should fail after remote error"; fi
BACKUP_NOW=$((last + 3600 * 7)) pf-backup run >/dev/null
pf-backup health >/dev/null || fail "health should recover after a good run"
pass "remote failure → exit 3 + unhealthy, recovers on next success"

# 6) failed dump leaves no file and is recorded.
printf '#!/bin/sh\nexit 1\n' >"$work/bin/pg_dump"
before=$(count daily)
if BACKUP_NOW=$((last + 86400)) pf-backup run >/dev/null 2>&1; then fail "run must fail"; fi
[ "$(count daily)" -eq "$before" ] || fail "failed dump created a file"
[ -z "$(find "$BACKUP_DIR" -name '*.partial')" ] || fail "partial left after failure"
if pf-backup health >/dev/null; then fail "health should fail after dump error"; fi
pass "failed dump: no file, unhealthy"

# 7) failure before pg_dump (database unreachable) is recorded too → unhealthy, then recovers.
cat >"$work/bin/pg_dump" <<'SH'
#!/bin/sh
for a in "$@"; do case "$a" in --file=*) f="${a#--file=}" ;; esac; done
echo "fake dump $BACKUP_NOW" >"$f"
SH
chmod +x "$work/bin/pg_dump"
BACKUP_NOW=$((last + 86400 + 60)) pf-backup run >/dev/null
pf-backup health >/dev/null || fail "health should be ok after a good run"
printf '#!/bin/sh\nexit 2\n' >"$work/bin/pg_isready"
before=$(count daily)
if BACKUP_NOW=$((last + 86400 + 120)) DB_WAIT_SECONDS=1 pf-backup run >/dev/null 2>&1; then
  fail "run must fail when the database is unreachable"
fi
[ "$(count daily)" -eq "$before" ] || fail "unreachable DB created a file"
if pf-backup health >/dev/null; then fail "health should fail when the DB was unreachable"; fi
grep -q 'before the dump' "$BACKUP_DIR/.state/last_error" || fail "last_error not recorded"
printf '#!/bin/sh\nexit 0\n' >"$work/bin/pg_isready"
BACKUP_NOW=$((last + 86400 + 180)) pf-backup run >/dev/null
pf-backup health >/dev/null || fail "health should recover after a good run"
pass "unreachable database: recorded, unhealthy, recovers"

# 8) lock held by another backup/restore → the attempt fails and is recorded.
(
  exec 8>"$BACKUP_DIR/.state/lock"
  flock 8
  if BACKUP_NOW=$((last + 86400 + 240)) pf-backup run >/dev/null 2>&1; then exit 10; fi
  exit 0
) || fail "run must fail while the lock is held"
if pf-backup health >/dev/null; then fail "health should fail after a lock failure"; fi
BACKUP_NOW=$((last + 86400 + 300)) pf-backup run >/dev/null
pf-backup health >/dev/null || fail "health should recover after a good run"
pass "busy lock: recorded, unhealthy, recovers"

echo "ALL ROTATION TESTS PASSED"

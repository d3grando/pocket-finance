#!/usr/bin/env bash
# offline regression tests for scripts/update.sh and scripts/restore.sh.
# No Docker needed: a fake `docker` CLI records every call; a throw-away git origin provides
# commits. Run: bash infra/tests/test_scripts.sh   (CI: scripts/ci-backup-tests.sh)
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok - $*"; }

# ---------- fake docker ----------
mkdir -p "$work/bin"
cat >"$work/bin/docker" <<'SH'
#!/usr/bin/env bash
# Fake docker CLI: appends each call to $FAKE_LOG; behaviour via FAKE_* variables.
echo "$*" >>"$FAKE_LOG"
if [ "$1" = compose ]; then
  shift
  while [ $# -gt 0 ]; do
    case "$1" in -p | --env-file | --profile | -f) shift 2 ;; *) break ;; esac
  done
  sub="$1"; shift
  case "$sub" in
    version) echo "Docker Compose version v5.0.0" ;;
    ps)
      if [ "${1:-}" = "--status" ]; then for s in ${FAKE_RUNNING:-}; do echo "$s"; done; fi
      if [ "${1:-}" = "-q" ]; then echo "cid-$2"; fi ;;
    exec | run)
      case "$*" in
        *"pf-backup run"*) exit "${FAKE_BACKUP_RC:-0}" ;;
        *last_ok*) echo "daily/pocket_2026-10-27_120000.dump" ;;
        *pf-restore*) exit "${FAKE_RESTORE_RC:-0}" ;;
        *healthcheck*) echo 'ok: {"status":"ok","db":true}' ;;
      esac ;;
    build) exit "${FAKE_BUILD_RC:-0}" ;;
    config) if [ "${1:-}" = "--images" ]; then
              printf '%s\n' "pocket-backend:$PF_VERSION" "pocket-backend:$PF_VERSION" "pocket-web:$PF_VERSION"
            fi ;;
  esac
  exit 0
fi
case "$1" in
  version) echo "${FAKE_ENGINE:-29.0.0}" ;;
  inspect) case "$*" in *RestartCount*) echo 0 ;; *) echo healthy ;; esac ;;
  image) if [ "$2" = ls ]; then repo="${*: -1}"; for t in ${FAKE_TAGS:-}; do echo "$repo:$t"; done; fi ;;
esac
exit 0
SH
chmod +x "$work/bin/docker"
export PATH="$work/bin:$PATH" FAKE_LOG="$work/docker.log"
unset PF_COMPOSE_OPTS PF_VERSION || true
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com

# ---------- git origin + deployed clone ----------
git init -q --bare -b main "$work/origin.git"
git clone -q "$work/origin.git" "$work/seed" 2>/dev/null
git -C "$work/seed" symbolic-ref HEAD refs/heads/main
cp -r "$root/scripts" "$work/seed/scripts"
commit() { echo "$1" >"$work/seed/v.txt"; git -C "$work/seed" add -A; git -C "$work/seed" commit -qm "$1"; git -C "$work/seed" push -q origin main 2>/dev/null; git -C "$work/seed" rev-parse --short HEAD; }
commit c1 >/dev/null; c2=$(commit c2)
git clone -q "$work/origin.git" "$work/app"
app="$work/app"
printf 'POSTGRES_PASSWORD=x\nPF_VERSION=%s\n' "$c2" >"$app/.env"
env_version() { sed -n 's/^PF_VERSION=//p' "$app/.env"; }
head_of() { git -C "$app" rev-parse --short HEAD; }
on_branch() { git -C "$app" symbolic-ref -q --short HEAD || echo DETACHED; }
upd() { : >"$FAKE_LOG"; bash "$app/scripts/update.sh" "$@" >"$work/out.log" 2>&1; }

# 1) plain update pulls main.
c3=$(commit c3)
upd --no-backup || { cat "$work/out.log"; fail "update failed"; }
[ "$(head_of)" = "$c3" ] && [ "$(on_branch)" = main ] && [ "$(env_version)" = "$c3" ] ||
  fail "plain update: head $(head_of) branch $(on_branch) env $(env_version), want $c3/main"
pass "plain update: git pull, PF_VERSION written"

# 2) rollback with --ref, then a plain update must return to the newest main.
upd --ref HEAD~1 --no-backup || { cat "$work/out.log"; fail "--ref failed"; }
[ "$(head_of)" = "$c2" ] && [ "$(env_version)" = "$c2" ] || fail "--ref HEAD~1: head $(head_of)"
c4=$(commit c4)
upd --no-backup || { cat "$work/out.log"; fail "update after --ref failed"; }
[ "$(head_of)" = "$c4" ] && [ "$(on_branch)" = main ] && [ "$(env_version)" = "$c4" ] ||
  fail "update after --ref stayed on $(head_of) ($(on_branch)), want $c4 on main"
pass "after --ref, plain update switches back to main and pulls"

# 3) failed pre-update backup: nothing changed (code, PF_VERSION), nothing built.
c5=$(commit c5)
if FAKE_RUNNING="db backup" FAKE_BACKUP_RC=1 upd; then fail "update must stop when the backup fails"; fi
[ "$(head_of)" = "$c4" ] && [ "$(env_version)" = "$c4" ] ||
  fail "backup failure changed code/version: head $(head_of) env $(env_version)"
grep -q 'compose build' "$FAKE_LOG" && fail "images were built after a failed backup"
grep -q 'compose up' "$FAKE_LOG" && fail "containers started after a failed backup"
pass "backup failure: code and PF_VERSION untouched, no build/up"

# 4) failed build: code and PF_VERSION are reverted, containers untouched.
if FAKE_RUNNING="db backup" FAKE_BUILD_RC=1 upd; then fail "update must fail when the build fails"; fi
[ "$(head_of)" = "$c4" ] && [ "$(on_branch)" = main ] && [ "$(env_version)" = "$c4" ] ||
  fail "build failure not reverted: head $(head_of) ($(on_branch)) env $(env_version)"
grep -q 'compose up' "$FAKE_LOG" && fail "containers started after a failed build"
grep -q 'pf-backup run' "$FAKE_LOG" || fail "backup did not run before the build"
pass "build failure: code and PF_VERSION reverted, no up"

# 5) success: backup first, then images older than the previous version are removed.
FAKE_RUNNING="db backup" FAKE_TAGS="0ldf00d $c4 $c5 test" upd || { cat "$work/out.log"; fail "update failed"; }
[ "$(head_of)" = "$c5" ] && [ "$(env_version)" = "$c5" ] || fail "update to $c5"
b=$(grep -n 'pf-backup run' "$FAKE_LOG" | head -n1 | cut -d: -f1)
g=$(grep -n 'compose .*build' "$FAKE_LOG" | head -n1 | cut -d: -f1)
[ -n "$b" ] && [ -n "$g" ] && [ "$b" -lt "$g" ] || fail "backup must run before the build"
for img in pocket-backend:0ldf00d pocket-web:0ldf00d pocket-backend:test; do
  grep -qx "rmi $img" "$FAKE_LOG" || fail "old image $img not removed"
done
grep -qE "^rmi .*:($c4|$c5)\$" "$FAKE_LOG" && fail "current/previous image removed"
grep -q 'бэкап перед обновлением: daily/' "$work/out.log" || fail "pre-update dump name not printed"
pass "backup before git; old images removed, current + previous kept"

# 6) --restore: build, stop api/worker/bot, pf-restore the dump, then up.
upd --ref "$c4" --restore daily/pocket_2026-10-27_120000.dump || { cat "$work/out.log"; fail "--restore failed"; }
s=$(grep -n 'compose .*stop api worker bot' "$FAKE_LOG" | cut -d: -f1)
r=$(grep -n 'pf-restore --yes daily/pocket_2026-10-27_120000.dump' "$FAKE_LOG" | cut -d: -f1)
u=$(grep -n 'compose .*up -d' "$FAKE_LOG" | cut -d: -f1)
[ -n "$s" ] && [ -n "$r" ] && [ -n "$u" ] && [ "$s" -lt "$r" ] && [ "$r" -lt "$u" ] ||
  fail "--restore order: stop=$s restore=$r up=$u"
grep -q 'pf-backup run' "$FAKE_LOG" && fail "--restore must not take a new backup (pf-restore keeps pre-restore/)"
[ "$(head_of)" = "$c4" ] || fail "--restore --ref did not switch code"
pass "--ref + --restore: stop → restore dump → up"

# 7) restore.sh: a relative file name is resolved against the caller's directory.
mkdir -p "$work/dumps"
echo dump >"$work/dumps/manual.dump"
: >"$FAKE_LOG"
(cd "$work/dumps" && FAKE_RUNNING="db backup" bash "$app/scripts/restore.sh" --check manual.dump) \
  >"$work/out.log" 2>&1 || { cat "$work/out.log"; fail "restore.sh --check manual.dump"; }
grep -q "compose cp $work/dumps/manual.dump backup:/backups/import/manual.dump" "$FAKE_LOG" ||
  fail "relative dump path not resolved from the caller's cwd: $(grep cp "$FAKE_LOG" || true)"
: >"$FAKE_LOG"
(cd "$work/dumps" && FAKE_RUNNING="db backup" bash "$app/scripts/restore.sh" --check daily/x.dump) >/dev/null 2>&1
grep -q 'compose cp' "$FAKE_LOG" && fail "BACKUP_DIR path must not be copied from the host"
grep -q 'pf-restore --yes --no-safety-backup --target-db _verify daily/x.dump' "$FAKE_LOG" ||
  fail "BACKUP_DIR-relative path not passed through"
pass "restore.sh: host file relative to cwd, BACKUP_DIR paths untouched"

# 8) gen-secrets.sh: a relative env path is resolved against the caller's directory; without an
#    argument it is the project's .env (the one compose reads), wherever the script is started from.
cp "$root/.env.example" "$app/.env.example"
mkdir -p "$work/elsewhere"
(cd "$work/elsewhere" && bash "$app/scripts/gen-secrets.sh" my.env) >"$work/out.log" 2>&1 ||
  { cat "$work/out.log"; fail "gen-secrets.sh my.env"; }
[ -f "$work/elsewhere/my.env" ] && [ ! -e "$app/my.env" ] ||
  fail "relative env path not resolved from the caller's cwd (got: $(ls "$app" | tr '\n' ' '))"
[ "$(stat -c %a "$work/elsewhere/my.env")" = 600 ] || fail "env file must be mode 600"
key=$(sed -n 's/^SECRET_KEY=//p' "$work/elsewhere/my.env")
[ "${#key}" -eq 64 ] || fail "SECRET_KEY not generated"
grep -qE '^BOT_SERVICE_TOKEN=[A-Za-z0-9]{48}$' "$work/elsewhere/my.env" || fail "BOT_SERVICE_TOKEN not generated"
grep -qE '^POSTGRES_PASSWORD=[A-Za-z0-9]{32}$' "$work/elsewhere/my.env" || fail "POSTGRES_PASSWORD not generated"
(cd "$work/elsewhere" && bash "$app/scripts/gen-secrets.sh" "$work/elsewhere/my.env") >/dev/null 2>&1 ||
  fail "gen-secrets.sh with an absolute path"
[ "$(sed -n 's/^SECRET_KEY=//p' "$work/elsewhere/my.env")" = "$key" ] || fail "existing secret overwritten"
(cd "$app/scripts" && bash ./gen-secrets.sh) >/dev/null 2>&1 || fail "gen-secrets.sh without arguments"
[ ! -e "$app/scripts/.env" ] && grep -qE '^SECRET_KEY=[A-Za-z0-9]{64}$' "$app/.env" ||
  fail "default env file is not <project>/.env"
grep -qx 'POSTGRES_PASSWORD=x' "$app/.env" || fail "existing POSTGRES_PASSWORD changed"
pass "gen-secrets.sh: path relative to cwd, default <project>/.env, existing values kept"

# 9) Docker Engine < 25 (healthcheck.start_interval) is refused before anything is touched;
#    an unknown version (no access to the daemon, distro suffixes) is left to compose.
: >"$FAKE_LOG"
FAKE_ENGINE=24.0.9 bash "$app/scripts/restore.sh" --check daily/x.dump >"$work/out.log" 2>&1 &&
  fail "Docker Engine 24 must be refused"
grep -q 'Docker Engine 25+' "$work/out.log" || fail "no Engine 25+ hint: $(cat "$work/out.log")"
grep -q 'pf-restore' "$FAKE_LOG" && fail "restore ran on Docker Engine 24"
(cd "$work/dumps" && FAKE_ENGINE=26.1.5+dfsg1 FAKE_RUNNING="db backup" \
  bash "$app/scripts/restore.sh" --check daily/x.dump) >/dev/null 2>&1 ||
  fail "unknown engine version format must not be refused"
pass "need_docker: Engine 25+ required, unknown version formats pass"

echo "ALL SCRIPT TESTS PASSED"

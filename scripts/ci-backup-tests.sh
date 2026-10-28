#!/bin/sh
# offline infra tests without Docker (CI / any Linux with GNU or busybox
# coreutils, flock, rsync, ssh, git, python3): backup rotation (scripts installed into a
# temporary prefix), update.sh/restore.sh/gen-secrets.sh logic, backend-image Python helpers,
# release config checks.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/lib"
# pf-backup sources /usr/local/lib/pf-backup/lib.sh — point it at the checkout instead.
for s in pf-backup pf-restore; do
  sed "s|/usr/local/lib/pf-backup/lib.sh|$root/infra/backup/lib.sh|" \
    "$root/infra/backup/$s.sh" >"$tmp/bin/$s"
  chmod +x "$tmp/bin/$s"
done
PATH="$tmp/bin:$PATH" TMPDIR="$tmp" sh "$root/infra/tests/test_rotation.sh"
# update.sh / restore.sh with a fake docker CLI, and the backend-image Python helpers.
TMPDIR="$tmp" bash "$root/infra/tests/test_scripts.sh"
python3 "$root/infra/tests/test_pf_helpers.py"
# Release config: neutral defaults, pinned images/actions, hardening (compose part needs Docker).
python3 "$root/infra/tests/test_release_config.py"

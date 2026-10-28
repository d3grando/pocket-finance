#!/bin/bash
# Windows build under wine (Linux host with Docker + Xvfb): does Pocket.exe still start with the hardening fuses, and is the
# embedded asar integrity really enforced? Needs release/win-unpacked from `pnpm dist:win` / `pnpm dist:win:docker`.
#   cd desktop && bash scripts/wine-smoke.sh          (several minutes: wine prefix init)
# Linux only: runs electronuserland/builder:wine against an Xvfb display of the host (the image has no Xvfb).
# The prefix is initialised once, shut down cleanly and copied for every check (each check gets its
# own wineserver; killing wine between runs breaks a shared prefix).
# Checks:
#   start     Pocket.exe starts and keeps running (asar integrity of the real build is valid);
#   runasnode ELECTRON_RUN_AS_NODE=1 Pocket.exe script.js starts the app, the script does not run;
#   tamper    a copy with one hash in the app.asar header changed dies at startup (integrity enforced).
set -euo pipefail

if [ "${1:-}" != "--inside" ]; then
  cd "$(dirname "$0")/.."
  [ -f release/win-unpacked/Pocket.exe ] || { echo "no release/win-unpacked/Pocket.exe — run pnpm dist:win (or dist:win:docker) first" >&2; exit 2; }
  # --hostname: the Xauthority cookie of xvfb-run is bound to this host name
  exec xvfb-run -a -s "-screen 0 1920x1080x24" bash -c '
    docker run --rm --hostname "$(hostname)" -e DISPLAY -e XAUTHORITY=/xauth -v "$XAUTHORITY":/xauth:ro \
      -v /tmp/.X11-unix:/tmp/.X11-unix -v "$PWD/release/win-unpacked":/rel:ro \
      -v "$PWD/scripts/wine-smoke.sh":/wine-smoke.sh:ro electronuserland/builder:wine bash /wine-smoke.sh --inside'
fi

# ---- inside the container ----
export WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml=" # no Mono/Gecko installers: fast init
WINEPREFIX=/tmp/wp-template wineboot -i >/dev/null 2>&1 || true
WINEPREFIX=/tmp/wp-template wineboot -s >/dev/null 2>&1 || true
cd /tmp
fail=0
# run <check> <exe> [args…] → exit code in $code (124 = still running when the timeout hit)
run() {
  local check="$1" exe="$2"; shift 2
  cp -a /tmp/wp-template "/tmp/wp-$check"
  code=0
  WINEPREFIX="/tmp/wp-$check" timeout 40 wine "$exe" "$@" >"/tmp/$check.log" 2>&1 || code=$?
}

run start /rel/Pocket.exe --no-sandbox --disable-gpu
if [ "$code" = 124 ]; then echo "ok   Pocket.exe starts and keeps running (asar integrity valid)"
else echo "FAIL Pocket.exe exited early: $code"; grep -v fixme /tmp/start.log | tail -5; fail=1; fi

echo "require('fs').writeFileSync('Z:\\\\tmp\\\\ran.txt', 'x')" > /tmp/v.js
ELECTRON_RUN_AS_NODE=1 run runasnode /rel/Pocket.exe 'Z:\tmp\v.js'
if [ ! -f /tmp/ran.txt ] && [ "$code" = 124 ]; then echo "ok   ELECTRON_RUN_AS_NODE=1 starts the app, the script does not run"
else echo "FAIL ELECTRON_RUN_AS_NODE=1: exit $code, script ran: $([ -f /tmp/ran.txt ] && echo yes || echo no)"; fail=1; fi

cp -r /rel /tmp/tamper
node -e '
  const fs = require("fs"); const f = "/tmp/tamper/resources/app.asar"
  const b = fs.readFileSync(f); const len = b.readUInt32LE(12); const start = 16
  const header = b.subarray(start, start + len).toString("utf8")
  const i = header.indexOf("\"hash\":\"") + 8
  if (i < 8) { console.error("no hash in the asar header"); process.exit(1) }
  const pos = start + Buffer.byteLength(header.slice(0, i))
  b[pos] = b[pos] === 0x30 ? 0x31 : 0x30; fs.writeFileSync(f, b)'
run tamper /tmp/tamper/Pocket.exe --no-sandbox --disable-gpu
reason=$(grep -i -m1 -E 'integrity' /tmp/tamper.log || true)
if [ "$code" != 124 ] && [ -n "$reason" ]; then echo "ok   tampered app.asar refused at startup (exit $code): ${reason:0:160}"
else echo "FAIL tampered app.asar: exit $code, integrity error logged: ${reason:-no}"; grep -v fixme /tmp/tamper.log | tail -5; fail=1; fi

[ "$fail" = 0 ] && echo WINE_SMOKE_OK
exit "$fail"

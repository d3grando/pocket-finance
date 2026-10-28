// Packaged-app checks (Linux dir build; same asar + fuse config as the Windows .exe):
//  1. fuses of the real binary: RunAsNode / NODE_OPTIONS / --inspect off, app only from app.asar
//     with integrity validation on, no extra file:// privileges (electron-builder.yml → electronFuses);
//  2. hardening probes on the real binary: ELECTRON_RUN_AS_NODE=1 starts the app instead of running
//     a script, --inspect / --remote-debugging-port open no port (NODE_OPTIONS has no probe: packaged
//     apps reject --require and friends even with the fuse on — the fuse readout covers it);
//  3. functional smoke (asar layout, app:// from inside app.asar, extraResources, packaged defaults:
//     server from PF_DEFAULT_SERVER_URL or none — then the login screen asks for it —, no test hooks,
//     no DevTools) through Playwright on a hard-linked copy of the build
//     whose only difference is the inspect fuse flipped back on — Playwright drives Electron through
//     --inspect + --remote-debugging-port, which the shipped binary refuses.
// Usage: pnpm exec electron-builder --linux dir --publish never && xvfb-run -a -s "-screen 0 1920x1080x24" node tests/e2e/packaged-smoke.mjs
import { execFileSync, spawn } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { _electron } from '@playwright/test'
import fuses from '@electron/fuses' // CommonJS: no named exports in ESM
import { readFuses } from '../../scripts/check-fuses.mjs'

const { flipFuses, FuseV1Options, FuseVersion } = fuses

const exe = path.resolve('release/linux-unpacked/pocket-desktop')
const failures = []
const check = (name, ok, detail) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail === undefined ? '' : ` — ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`}`)
  if (!ok) failures.push(name)
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ---------- 1. fuses ----------
for (const f of await readFuses(exe)) check(`fuse ${f.name} = ${f.want ? 'on' : 'off'}`, f.ok, f.state)

// ---------- 2. hardening probes on the shipped binary ----------
/** Starts the binary with its own profile (no single-instance clash, no prod API calls). */
async function probe({ args, env = {}, ms = 7000, poll }) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'pocket-probe-'))
  const cfg = path.join(home, '.config')
  fs.mkdirSync(path.join(cfg, 'Pocket'), { recursive: true })
  fs.writeFileSync(path.join(cfg, 'Pocket', 'config.json'), JSON.stringify({ serverUrl: 'http://127.0.0.1:9', hotkey: 'Ctrl+Alt+Space', autostart: false, autostartInitialized: true }))
  const child = spawn(exe, args, {
    env: { ...process.env, HOME: home, XDG_CONFIG_HOME: cfg, ELECTRON_DISABLE_SANDBOX: '1', ...env },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let out = ''
  child.stdout.on('data', (d) => (out += d))
  child.stderr.on('data', (d) => (out += d))
  let exited = null
  child.on('exit', (code, sig) => (exited = { code, sig }))
  const hits = []
  const t0 = Date.now()
  while (Date.now() - t0 < ms && !exited) {
    if (poll) {
      const h = await poll()
      if (h) hits.push(h)
    }
    await sleep(250)
  }
  const alive = !exited
  if (alive) {
    child.kill('SIGTERM')
    await Promise.race([new Promise((r) => child.once('exit', r)), sleep(3000)])
    if (!exited) child.kill('SIGKILL')
  }
  fs.rmSync(home, { recursive: true, force: true })
  return { out, alive, exited, hits }
}

const portOpen = (port) =>
  new Promise((resolve) => {
    const s = net.connect({ host: '127.0.0.1', port })
    s.once('connect', () => {
      s.destroy()
      resolve(true)
    })
    s.once('error', () => resolve(false))
  })

{
  // RunAsNode off: the binary starts the app (stays alive) instead of executing the script
  const r = await probe({ args: ['-e', 'console.log("PF_RUN_AS_NODE", process.versions.node)'], env: { ELECTRON_RUN_AS_NODE: '1' } })
  check('ELECTRON_RUN_AS_NODE=1 does not run a script', !r.out.includes('PF_RUN_AS_NODE') && r.alive, { alive: r.alive, exited: r.exited })
}
{
  // --inspect (fuse) and --remote-debugging-port (stripped by main) open no port
  const inspectPort = 39229
  const cdpPort = 39222
  const r = await probe({
    args: ['--no-sandbox', '--password-store=basic', `--inspect=127.0.0.1:${inspectPort}`, `--remote-debugging-port=${cdpPort}`],
    poll: async () => ((await portOpen(inspectPort)) ? 'inspect' : (await portOpen(cdpPort)) ? 'cdp' : null),
  })
  const banner = /Debugger listening|DevTools listening/.test(r.out)
  check('--inspect / --remote-debugging-port open no port', r.hits.length === 0 && !banner && r.alive, { hits: [...new Set(r.hits)], banner, alive: r.alive })
}

// ---------- 3. functional smoke on a copy with the inspect fuse back on ----------
const smokeDir = path.resolve('release/linux-smoke')
fs.rmSync(smokeDir, { recursive: true, force: true })
execFileSync('cp', ['-al', path.dirname(exe), smokeDir]) // hard links: no 300 MB copy
const smokeExe = path.join(smokeDir, 'pocket-desktop')
fs.rmSync(smokeExe)
fs.copyFileSync(exe, smokeExe) // own inode: flipping fuses must not touch the real binary
fs.chmodSync(smokeExe, 0o755)
await flipFuses(smokeExe, { version: FuseVersion.V1, [FuseV1Options.EnableNodeCliInspectArguments]: true })

const home = fs.mkdtempSync(path.join(os.tmpdir(), 'pocket-pkg-'))
const app = await _electron.launch({
  executablePath: smokeExe,
  args: ['--no-sandbox', '--password-store=basic'],
  env: { ...process.env, HOME: home, XDG_CONFIG_HOME: path.join(home, '.config'), ELECTRON_DISABLE_SANDBOX: '1' },
})
try {
  const page = await app.firstWindow()
  await page.waitForURL(/app:\/\/pocket\/__pocket\/login\.html/)
  await page.locator('h1').waitFor()
  const info = await app.evaluate(({ app, BrowserWindow }) => ({
    packaged: app.isPackaged,
    hooks: typeof globalThis.__pocketTest,
    windows: BrowserWindow.getAllWindows().map((w) => ({ url: w.webContents.getURL(), size: w.getSize(), devtools: w.webContents.isDevToolsOpened() })),
  }))
  // the build-time default (PF_DEFAULT_SERVER_URL) or none: then the first screen asks for the server
  const builtIn = (process.env.PF_DEFAULT_SERVER_URL ?? '').trim()
  const wantHost = builtIn ? new URL(/^[a-z]+:\/\//i.test(builtIn) ? builtIn : `https://${builtIn}`).host : ''
  await page.locator(wantHost ? '#form' : '#server-form').waitFor()
  const text = {
    h1: await page.locator('h1').textContent(),
    host: await page.locator('#server-host').textContent(),
    serverStep: await page.locator('#server-form').isVisible(),
    css: await page.evaluate(() => getComputedStyle(document.querySelector('#submit')).backgroundColor),
  }
  console.log(JSON.stringify({ info, text }))
  check('packaged app boots from app.asar: login screen, built-in server or the server step, no test hooks',
    info.packaged && info.hooks === 'undefined' && text.h1 === 'Вход в Pocket' && text.host === wantHost && text.serverStep === !wantHost, { wantHost, ...text })
  check('popup 420×450', info.windows[0].size[0] === 420 && info.windows[0].size[1] === 450, info.windows[0].size)
} finally {
  await app.close()
  fs.rmSync(home, { recursive: true, force: true })
  fs.rmSync(smokeDir, { recursive: true, force: true })
}

if (failures.length) {
  console.error(`packaged smoke failed: ${failures.join('; ')}`)
  process.exit(1)
}
console.log('PACKAGED_SMOKE_OK')

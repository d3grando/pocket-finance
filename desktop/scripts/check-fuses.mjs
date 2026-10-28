// Electron fuses the shipped binaries must have (mirror of electron-builder.yml → electronFuses).
// CLI: node scripts/check-fuses.mjs <path to Pocket.exe | pocket-desktop>  → exit 1 on mismatch.
// Used by tests/e2e/packaged-smoke.mjs (Linux build) and `pnpm fuses:win` (after pnpm dist:win).
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import fuses from '@electron/fuses' // CommonJS: no named exports in ESM

const { FuseV1Options, getCurrentFuseWire } = fuses
// @electron/fuses does not export FuseState at runtime: ASCII '1' / '0' in the fuse wire
const FuseState = { ENABLE: 49, DISABLE: 48 }

export const EXPECTED_FUSES = {
  RunAsNode: false,
  EnableNodeOptionsEnvironmentVariable: false,
  EnableNodeCliInspectArguments: false,
  EnableEmbeddedAsarIntegrityValidation: true,
  OnlyLoadAppFromAsar: true,
  GrantFileProtocolExtraPrivileges: false,
}

/** [{ name, want, state, ok }] for every expected fuse of the binary at `exe`. */
export async function readFuses(exe) {
  const wire = await getCurrentFuseWire(exe)
  return Object.entries(EXPECTED_FUSES).map(([name, want]) => {
    const state = wire[FuseV1Options[name]]
    const ok = state === (want ? FuseState.ENABLE : FuseState.DISABLE)
    return { name, want, state: state === FuseState.ENABLE ? 'on' : state === FuseState.DISABLE ? 'off' : String(state), ok }
  })
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const exe = process.argv[2]
  if (!exe) {
    console.error('usage: node scripts/check-fuses.mjs <electron binary>')
    process.exit(2)
  }
  const rows = await readFuses(path.resolve(exe))
  for (const r of rows) console.log(`${r.ok ? 'ok  ' : 'FAIL'} ${r.name}: ${r.state} (want ${r.want ? 'on' : 'off'})`)
  if (rows.some((r) => !r.ok)) process.exit(1)
  console.log('FUSES_OK')
}

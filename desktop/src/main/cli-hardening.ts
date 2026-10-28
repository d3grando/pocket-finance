// Command-line hardening of the packaged app (defence in depth against code running as the same
// Windows user). ELECTRON_RUN_AS_NODE, NODE_OPTIONS and --inspect* are switched off by Electron
// fuses (electron-builder.yml → electronFuses). Chromium's remote debugging is not a fuse: started
// with --remote-debugging-port/pipe, the DevTools protocol would let anyone drive our pages and
// call window.pocket.auth.getToken(). The switches are removed before `ready`, when Chromium
// starts its DevTools server.
//
// One exception: a packaged build whose Node inspector is already active. With the
// EnableNodeCliInspectArguments fuse off (every shipped build) that never happens; the packaged
// smoke test flips that fuse back on a copy of the Linux build to drive it with Playwright, which
// needs both --inspect and --remote-debugging-port.

export const REMOTE_DEBUGGING_SWITCHES = [
  'remote-debugging-port',
  'remote-debugging-pipe',
  'remote-debugging-address',
  'remote-allow-origins',
] as const

export interface CommandLinePort {
  hasSwitch(name: string): boolean
  removeSwitch(name: string): void
}

/** Removes remote-debugging switches from a packaged app; returns the removed ones. */
export function stripRemoteDebugging(cl: CommandLinePort, opts: { packaged: boolean; inspectorActive: boolean }): string[] {
  if (!opts.packaged || opts.inspectorActive) return []
  const removed = REMOTE_DEBUGGING_SWITCHES.filter((s) => cl.hasSwitch(s))
  for (const s of removed) cl.removeSwitch(s)
  return removed
}

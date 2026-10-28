// Autostart with Windows (on by default). Electron's setLoginItemSettings writes
// HKCU\Software\Microsoft\Windows\CurrentVersion\Run → "<Pocket.exe>" --hidden (start in the tray).
// Only for the installed (packaged) app: in dev it would register electron.exe.

export const HIDDEN_ARG = '--hidden'

export interface LoginItemPort {
  supported: boolean
  set(enabled: boolean): void
  get(): boolean
}

export interface AutostartConfig {
  autostart: boolean
  autostartInitialized: boolean
}

/**
 * First run: enable autostart and remember that the default was applied (so turning it off
 * in settings sticks). Later runs: re-apply the saved choice (the exe path changes on update).
 * Returns the config patch to persist.
 */
export function initAutostart(cfg: AutostartConfig, port: LoginItemPort): AutostartConfig {
  if (!port.supported) return cfg
  if (!cfg.autostartInitialized) {
    port.set(true)
    return { autostart: true, autostartInitialized: true }
  }
  port.set(cfg.autostart)
  return cfg
}

export function setAutostart(enabled: boolean, port: LoginItemPort): boolean {
  if (!port.supported) return enabled
  port.set(enabled)
  return port.get()
}

export function electronLoginItemPort(app: Electron.App, platform: string): LoginItemPort {
  const opts = { path: process.execPath, args: [HIDDEN_ARG] }
  return {
    supported: app.isPackaged && (platform === 'win32' || platform === 'darwin'),
    set: (enabled) => app.setLoginItemSettings({ openAtLogin: enabled, ...opts }),
    get: () => app.getLoginItemSettings(opts).openAtLogin,
  }
}

export function launchedHidden(argv: string[]): boolean {
  return argv.includes(HIDDEN_ARG)
}

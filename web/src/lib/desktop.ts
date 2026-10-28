// Electron preload bridge (window.pocket). Every member is optional:
// the web build runs without it, and an older/newer desktop shell must not crash the renderer.
// The source of truth for the shape is desktop/src/shared/ipc.ts (PocketBridge); this file is the
// only place the web touches it. Notable differences from what the web needs, adapted here:
//   * «popup shown» is `window.onShown(cb)` with `{ edge }` (legacy top-level `onShow` still accepted);
//   * `platform` is `process.platform` ('win32' | 'darwin' | 'linux') → mapped to the API values
//     'windows' | 'macos' | 'linux' (POST /auth/device-login rejects anything else with 422);
//   * the device name comes from `config.get().deviceName` (host name), not from the bridge root.
import { configureApi } from './api/client'

export type TaskbarEdge = 'top' | 'bottom' | 'left' | 'right'
export interface ShownEvent { edge?: TaskbarEdge }
export type DevicePlatform = 'windows' | 'macos' | 'linux'

export interface DesktopConfig {
  apiBaseUrl: string
  hotkey: string
  autostart: boolean
  /** process.platform of the desktop shell */
  platform?: string
  /** host name, used as the device name on POST /auth/device-login */
  deviceName?: string
}

type Unsubscribe = (() => void) | void

export interface PocketBridge {
  auth?: {
    getToken?: () => Promise<string | null> | string | null
    setToken?: (token: string) => Promise<unknown> | void
    clearToken?: () => Promise<void> | void
  }
  config?: { get?: () => Promise<DesktopConfig> | DesktopConfig }
  window?: {
    hide?: () => void
    openFull?: (route?: string) => void
    resize?: (height: number) => void
    setPinned?: (pinned: boolean) => void
    /** fires every time the Quick Capture popup is shown (focus the input, animate, refetch) */
    onShown?: (cb: (e?: ShownEvent) => void) => Unsubscribe
  }
  /** @deprecated older name of window.onShown; kept so an older shell still works */
  onShow?: (cb: (e?: ShownEvent) => void) => Unsubscribe
  /** process.platform ('win32' | 'darwin' | 'linux'); the normalized values are accepted too */
  platform?: string
  deviceName?: string
}

declare global {
  interface Window { pocket?: PocketBridge }
}

export const bridge = (): PocketBridge | null => (typeof window !== 'undefined' && window.pocket ? window.pocket : null)
export const isDesktopBuild = import.meta.env.MODE === 'desktop'
export const hasDesktopAuth = () => typeof bridge()?.auth?.getToken === 'function'

/** process.platform (or an already normalized value) → the API `platform` enum; null = unknown. */
export function normalizePlatform(p: unknown): DevicePlatform | null {
  if (typeof p !== 'string') return null
  const v = p.toLowerCase()
  if (v === 'win32' || v === 'windows') return 'windows'
  if (v === 'darwin' || v === 'macos') return 'macos'
  if (v === 'linux') return 'linux'
  return null
}

let shellConfig: DesktopConfig | null = null

/** Wire API auth to the device token when running inside Electron (call before first request). */
export async function initDesktopBridge(): Promise<void> {
  const b = bridge()
  if (!b) return
  try {
    const cfg = await b.config?.get?.()
    if (cfg) shellConfig = cfg
    if (cfg?.apiBaseUrl) configureApi({ baseUrl: cfg.apiBaseUrl })
  } catch { /* keep VITE_API_BASE_URL */ }
  if (b.auth?.getToken) configureApi({ getToken: () => b.auth!.getToken!() })
}

export const desktop = {
  hide: () => bridge()?.window?.hide?.(),
  openFull: (route = '/') => {
    const b = bridge()
    if (b?.window?.openFull) b.window.openFull(route)
    else window.open(route, '_blank', 'noopener')
  },
  resize: (h: number) => bridge()?.window?.resize?.(Math.min(640, Math.max(300, Math.round(h)))),
  setPinned: (p: boolean) => bridge()?.window?.setPinned?.(p),
  setToken: async (t: string) => { await bridge()?.auth?.setToken?.(t) },
  clearToken: async () => { await bridge()?.auth?.clearToken?.() },
  /** Subscribe to «popup shown». Returns an unsubscribe function (no-op outside Electron). */
  onShow: (cb: (e: ShownEvent) => void): (() => void) => {
    const b = bridge()
    const sub = b?.window?.onShown ?? b?.onShow
    if (typeof sub !== 'function') return () => {}
    const off = sub((e) => cb(e ?? {}))
    return typeof off === 'function' ? off : () => {}
  },
  platform: (): DevicePlatform => {
    const b = bridge()
    const p = normalizePlatform(b?.platform) ?? normalizePlatform(shellConfig?.platform)
    if (p) return p
    const ua = typeof navigator !== 'undefined' ? navigator.userAgent : ''
    return /Mac/i.test(ua) ? 'macos' : /Linux/i.test(ua) ? 'linux' : 'windows'
  },
  deviceName: (): string => {
    const n = (bridge()?.deviceName || shellConfig?.deviceName || '').trim()
    return (n || 'Pocket Desktop').slice(0, 100)
  },
}

/** test hook: forget the cached shell config */
export function __resetDesktopForTests(): void { shellConfig = null }

// Which window/page may call which IPC channel (pure, unit-tested). ipc-handlers.ts first checks
// that the call comes from the main frame of one of our own windows showing an app://pocket page,
// then asks this table. Channels that change the app itself (server address, hotkey, autostart,
// sign-out) are accepted only from the desktop-native settings page (and the server address also
// from the login page, for the first run) — never from the web renderer, so script injected into
// the web app cannot point the app at another server or bind Ctrl+V.
import { IPC, UI_PREFIX, type WindowKind } from '../shared/ipc'
import { isInternalUrl } from './urls'

/** login / settings / capture = desktop-native pages under app://pocket/__pocket/; web = the packaged web app */
export type PageKind = 'login' | 'settings' | 'capture' | 'web'

export function pageKind(url: string): PageKind | null {
  if (!isInternalUrl(url)) return null
  let path: string
  try {
    path = new URL(url).pathname
  } catch {
    return null
  }
  if (!path.startsWith(UI_PREFIX)) return 'web'
  const page = path.slice(UI_PREFIX.length)
  if (page === 'login.html') return 'login'
  if (page === 'settings.html') return 'settings'
  if (page === 'capture.html') return 'capture'
  return null
}

type Rule = { windows: readonly WindowKind[]; pages: readonly PageKind[] }

const ANY_WINDOW: readonly WindowKind[] = ['popup', 'full', 'settings']
const ANY_PAGE: readonly PageKind[] = ['login', 'settings', 'capture', 'web']
const SETTINGS_ONLY: Rule = { windows: ['settings'], pages: ['settings'] }
/** pages that talk to the API with the device token: the web app (popup /quick, full panel) and the lite capture */
const API_PAGES: Rule = { windows: ['popup', 'full'], pages: ['web', 'capture'] }

export const IPC_RULES: Record<string, Rule> = {
  [IPC.getToken]: API_PAGES,
  [IPC.setToken]: { windows: ['popup', 'full'], pages: ['web'] }, // web in-window login (/quick)
  [IPC.clearToken]: API_PAGES,
  [IPC.login]: { windows: ['popup'], pages: ['login'] },
  [IPC.logout]: SETTINGS_ONLY,
  [IPC.getConfig]: { windows: ANY_WINDOW, pages: ANY_PAGE },
  [IPC.setServerUrl]: { windows: ['settings', 'popup'], pages: ['settings', 'login'] },
  [IPC.setHotkey]: SETTINGS_ONLY,
  [IPC.suggestHotkeys]: SETTINGS_ONLY,
  [IPC.setHotkeyRecording]: SETTINGS_ONLY,
  [IPC.setAutostart]: SETTINGS_ONLY,
  [IPC.hide]: { windows: ANY_WINDOW, pages: ANY_PAGE },
  [IPC.openFull]: { windows: ANY_WINDOW, pages: ANY_PAGE },
  [IPC.openSettings]: { windows: ANY_WINDOW, pages: ANY_PAGE },
  [IPC.resize]: { windows: ['popup'], pages: ANY_PAGE },
  [IPC.setPinned]: { windows: ['popup'], pages: ANY_PAGE },
  [IPC.escapeResult]: { windows: ['popup'], pages: ANY_PAGE },
  [IPC.openExternal]: { windows: ANY_WINDOW, pages: ANY_PAGE },
  [IPC.getConnection]: { windows: ANY_WINDOW, pages: ANY_PAGE },
}

/** true = `channel` may be called by a `window` window showing `page`. Unknown channels: never. */
export function ipcAllowed(channel: string, window: WindowKind | null, page: PageKind | null): boolean {
  const rule = Object.prototype.hasOwnProperty.call(IPC_RULES, channel) ? IPC_RULES[channel] : undefined
  if (!rule || !window || !page) return false
  return rule.windows.includes(window) && rule.pages.includes(page)
}

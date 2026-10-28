// Regression tests (desktop): dangerous IPC (server address, hotkey,
// autostart, sign-out) only from the desktop settings page; the server address of an existing
// install changes only after a native confirmation; the token is handed only to API pages.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { IPC, type WindowKind } from '../../src/shared/ipc'

const electron = vi.hoisted(() => ({ handlers: new Map<string, (e: unknown, ...a: unknown[]) => unknown>() }))
vi.mock('electron', () => ({
  BrowserWindow: { fromWebContents: (wc: { win: unknown }) => wc.win ?? null },
  ipcMain: { handle: (ch: string, fn: (e: unknown, ...a: unknown[]) => unknown) => electron.handlers.set(ch, fn) },
}))

const { ipcAllowed, pageKind, IPC_RULES } = await import('../../src/main/ipc-policy')
const { registerIpc } = await import('../../src/main/ipc-handlers')
const { changeServerUrl } = await import('../../src/main/server-change')

const PAGES = {
  login: 'app://pocket/__pocket/login.html',
  settings: 'app://pocket/__pocket/settings.html',
  capture: 'app://pocket/__pocket/capture.html',
  web: 'app://pocket/quick',
}

describe('pageKind', () => {
  it('classifies our pages; anything else is not ours', () => {
    expect(pageKind(PAGES.login)).toBe('login')
    expect(pageKind(PAGES.settings)).toBe('settings')
    expect(pageKind('app://pocket/__pocket/capture.html?x=1')).toBe('capture')
    expect(pageKind('app://pocket/operations?status=needs_account')).toBe('web')
    expect(pageKind('app://pocket/__pocket/other.html')).toBeNull()
    expect(pageKind('https://pocket.example.com/__pocket/settings.html')).toBeNull()
    expect(pageKind('app://evil/__pocket/settings.html')).toBeNull()
  })
})

describe('ipcAllowed', () => {
  it('server address, hotkey, autostart, logout: only the settings page (+ server on the login page)', () => {
    for (const ch of [IPC.setHotkey, IPC.suggestHotkeys, IPC.setHotkeyRecording, IPC.setAutostart, IPC.logout]) {
      expect(ipcAllowed(ch, 'settings', 'settings')).toBe(true)
      expect(ipcAllowed(ch, 'popup', 'web')).toBe(false)
      expect(ipcAllowed(ch, 'popup', 'capture')).toBe(false)
      expect(ipcAllowed(ch, 'full', 'web')).toBe(false)
      expect(ipcAllowed(ch, 'popup', 'login')).toBe(false)
    }
    expect(ipcAllowed(IPC.setServerUrl, 'settings', 'settings')).toBe(true)
    expect(ipcAllowed(IPC.setServerUrl, 'popup', 'login')).toBe(true) // first run: the server step
    expect(ipcAllowed(IPC.setServerUrl, 'popup', 'web')).toBe(false) // a web page inside the popup must not change the server
    expect(ipcAllowed(IPC.setServerUrl, 'popup', 'capture')).toBe(false)
    expect(ipcAllowed(IPC.setServerUrl, 'full', 'web')).toBe(false)
    expect(ipcAllowed(IPC.setServerUrl, 'settings', 'web')).toBe(false) // settings window navigated elsewhere
  })

  it('the device token goes only to the pages that call the API', () => {
    expect(ipcAllowed(IPC.getToken, 'popup', 'web')).toBe(true)
    expect(ipcAllowed(IPC.getToken, 'full', 'web')).toBe(true)
    expect(ipcAllowed(IPC.getToken, 'popup', 'capture')).toBe(true)
    expect(ipcAllowed(IPC.getToken, 'settings', 'settings')).toBe(false)
    expect(ipcAllowed(IPC.getToken, 'popup', 'login')).toBe(false)
    expect(ipcAllowed(IPC.setToken, 'popup', 'capture')).toBe(false)
    expect(ipcAllowed(IPC.login, 'popup', 'login')).toBe(true)
    expect(ipcAllowed(IPC.login, 'popup', 'web')).toBe(false)
  })

  it('every channel has a rule; unknown channels and senders are refused', () => {
    for (const ch of Object.values(IPC)) expect(IPC_RULES[ch], ch).toBeDefined()
    expect(ipcAllowed('pocket:nope', 'settings', 'settings')).toBe(false)
    expect(ipcAllowed('constructor', 'settings', 'settings')).toBe(false)
    expect(ipcAllowed(IPC.getConfig, null, 'web')).toBe(false)
    expect(ipcAllowed(IPC.getConfig, 'popup', null)).toBe(false)
  })
})

// ------------------------------------------------------------------ registerIpc with fake windows
type FakeWin = { id: number; kind: WindowKind; webContents: { id: number; win?: FakeWin; mainFrame: unknown }; isDestroyed(): boolean; close(): void }

function fakeWin(id: number, kind: WindowKind): FakeWin {
  const w: FakeWin = { id, kind, webContents: { id: id * 10, mainFrame: null }, isDestroyed: () => false, close: vi.fn() }
  w.webContents.win = w
  return w
}

function event(win: FakeWin, url: string, opts: { subframe?: boolean } = {}) {
  const frame = { url }
  win.webContents.mainFrame = opts.subframe ? { url: 'app://pocket/quick' } : frame
  return { sender: win.webContents, senderFrame: frame }
}

describe('registerIpc', () => {
  const popup = fakeWin(1, 'popup')
  const full = fakeWin(2, 'full')
  const settings = fakeWin(3, 'settings')
  const ctx = {
    ownWindows: () => [popup, full, settings],
    popupWindow: () => popup,
    windowKind: (w: FakeWin) => w.kind,
    getToken: vi.fn(() => 'pfd_token'),
    setToken: vi.fn(() => ({ ok: true })),
    clearToken: vi.fn(),
    login: vi.fn(async () => ({ ok: true as const })),
    logout: vi.fn(async () => {}),
    desktopConfig: vi.fn(() => ({ serverUrl: 'https://pocket.example.com' })),
    setServerUrl: vi.fn(async () => ({ ok: true })),
    setHotkey: vi.fn(() => ({ ok: true, accelerator: 'Ctrl+V' })),
    suggestHotkeys: vi.fn(() => []),
    setHotkeyRecording: vi.fn(),
    setAutostart: vi.fn(() => true),
    hidePopup: vi.fn(),
    openFull: vi.fn(),
    openSettings: vi.fn(),
    resizePopup: vi.fn(),
    setPinned: vi.fn(),
    popupEscape: vi.fn(),
    openExternal: vi.fn(() => true),
    connection: vi.fn(() => 'online'),
    log: vi.fn(),
  }
  beforeEach(() => {
    electron.handlers.clear()
    for (const f of Object.values(ctx)) if (vi.isMockFunction(f)) f.mockClear()
    registerIpc(ctx as never)
  })
  const call = (ch: string, ev: unknown, ...args: unknown[]) => electron.handlers.get(ch)!(ev, ...args)

  it('setServerUrl from the popup (web app or lite capture) is refused — it used to switch the server', async () => {
    await expect(call(IPC.setServerUrl, event(popup, PAGES.web), 'https://evil.example.net')).rejects.toThrow('forbidden')
    await expect(call(IPC.setServerUrl, event(popup, PAGES.capture), 'https://evil.example.net')).rejects.toThrow('forbidden')
    await expect(call(IPC.setServerUrl, event(full, 'app://pocket/settings'), 'https://evil.example.net')).rejects.toThrow('forbidden')
    expect(ctx.setServerUrl).not.toHaveBeenCalled()
    await call(IPC.setServerUrl, event(settings, PAGES.settings), 'https://new.example.com')
    expect(ctx.setServerUrl).toHaveBeenCalledWith('https://new.example.com', settings) // parent of the native dialog
  })

  it('hotkey, autostart and logout only from the settings window', async () => {
    await expect(call(IPC.setHotkey, event(popup, PAGES.web), 'Ctrl+Alt+P')).rejects.toThrow('forbidden')
    await expect(call(IPC.setAutostart, event(full, 'app://pocket/'), false)).rejects.toThrow('forbidden')
    await expect(call(IPC.logout, event(popup, PAGES.web))).rejects.toThrow('forbidden')
    expect(ctx.setHotkey).not.toHaveBeenCalled()
    expect(ctx.setAutostart).not.toHaveBeenCalled()
    expect(ctx.logout).not.toHaveBeenCalled()
    await call(IPC.setHotkey, event(settings, PAGES.settings), 'Ctrl+Alt+P')
    await call(IPC.setAutostart, event(settings, PAGES.settings), true)
    expect(ctx.setHotkey).toHaveBeenCalledWith('Ctrl+Alt+P')
    expect(ctx.setAutostart).toHaveBeenCalledWith(true)
  })

  it('subframes, foreign windows and non-app pages are refused for every channel', async () => {
    await expect(call(IPC.getConfig, event(settings, PAGES.settings, { subframe: true }))).rejects.toThrow('forbidden')
    const stranger = fakeWin(9, 'settings')
    await expect(call(IPC.getConfig, event(stranger, PAGES.settings))).rejects.toThrow('forbidden')
    await expect(call(IPC.getToken, event(popup, 'https://evil.example.net/'))).rejects.toThrow('forbidden')
    await expect(call(IPC.getToken, event(settings, PAGES.settings))).rejects.toThrow('forbidden')
    expect(ctx.getToken).not.toHaveBeenCalled()
    expect(await call(IPC.getToken, event(popup, PAGES.web))).toBe('pfd_token')
    expect(ctx.log).toHaveBeenCalled()
  })
})

describe('changeServerUrl', () => {
  const deps = (current: string, answer: boolean) => ({ current: () => current, confirm: vi.fn(async () => answer), apply: vi.fn() })

  it('replacing an existing server needs the native confirmation', async () => {
    const yes = deps('https://pocket.example.com', true)
    expect(await changeServerUrl('new.example.com', yes)).toEqual({ ok: true, changed: true })
    expect(yes.confirm).toHaveBeenCalledWith('pocket.example.com', 'new.example.com')
    expect(yes.apply).toHaveBeenCalledWith('https://new.example.com')

    const no = deps('https://pocket.example.com', false)
    expect(await changeServerUrl('https://evil.example.net', no)).toEqual({ ok: false, message: 'Сервер не изменён' })
    expect(no.apply).not.toHaveBeenCalled()
  })

  it('first run (no server yet): no dialog; same address: nothing to do; bad address: message', async () => {
    const first = deps('', false)
    expect(await changeServerUrl('https://pocket.example.com/', first)).toEqual({ ok: true, changed: true })
    expect(first.confirm).not.toHaveBeenCalled()
    expect(first.apply).toHaveBeenCalledWith('https://pocket.example.com')

    const same = deps('https://pocket.example.com', false)
    expect(await changeServerUrl('https://pocket.example.com/api/v1', same)).toEqual({ ok: true, changed: false })
    expect(same.confirm).not.toHaveBeenCalled()
    expect(same.apply).not.toHaveBeenCalled()

    const bad = deps('https://pocket.example.com', true)
    expect(await changeServerUrl('http://evil.example.net', bad)).toEqual({ ok: false, message: 'Нужен адрес https://' })
    expect(bad.confirm).not.toHaveBeenCalled()
  })
})

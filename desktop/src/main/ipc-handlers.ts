// IPC whitelist: exactly the channels of src/shared/ipc.ts. Every call must come from the main frame
// of one of our own windows showing an app://pocket page; then ipc-policy.ts decides by window
// (the sender's webContents is matched against the popup / full / settings window) and by page
// (login / settings / capture / web app) — the server address, hotkey, autostart and sign-out
// only from the desktop settings page. Arguments are validated.
import { BrowserWindow, ipcMain, type IpcMainInvokeEvent } from 'electron'
import { IPC, type ConnectionState, type DesktopConfig, type HotkeyResult, type LoginResult, type SetServerResult, type WindowKind } from '../shared/ipc'
import { ipcAllowed, pageKind, type PageKind } from './ipc-policy'
import { sanitizeRoute } from './urls'

export interface IpcContext {
  ownWindows(): BrowserWindow[]
  popupWindow(): BrowserWindow
  /** popup / full panel / settings — by window identity (null = not ours) */
  windowKind(win: BrowserWindow): WindowKind | null
  getToken(): string | null
  setToken(token: string): { ok: boolean }
  clearToken(): void
  login(password: string): Promise<LoginResult>
  logout(): Promise<void>
  desktopConfig(): DesktopConfig
  /** `from` = the window that asked (parent of the native confirmation dialog) */
  setServerUrl(url: string, from: BrowserWindow): SetServerResult | Promise<SetServerResult>
  setHotkey(acc: string): HotkeyResult
  suggestHotkeys(): string[]
  setHotkeyRecording(on: boolean): void
  setAutostart(enabled: boolean): boolean
  hidePopup(): void
  openFull(route: string): void
  openSettings(): void
  resizePopup(height: number): void
  setPinned(pinned: boolean): void
  popupEscape(handled: boolean): void
  openExternal(url: string): boolean
  connection(): ConnectionState
  log?(msg: string): void
}

interface Sender {
  win: BrowserWindow
  kind: WindowKind
  page: PageKind
}

function sender(ctx: IpcContext, e: IpcMainInvokeEvent): Sender | null {
  const frame = e.senderFrame
  if (!frame || frame !== e.sender.mainFrame) return null
  const page = pageKind(frame.url)
  if (!page) return null
  const win = BrowserWindow.fromWebContents(e.sender)
  if (!win || win.isDestroyed() || !ctx.ownWindows().includes(win) || win.webContents.id !== e.sender.id) return null
  const kind = ctx.windowKind(win)
  if (!kind) return null
  return { win, kind, page }
}

export function registerIpc(ctx: IpcContext): void {
  const handle = <A extends unknown[], R>(channel: string, fn: (win: BrowserWindow, ...args: A) => R | Promise<R>) => {
    ipcMain.handle(channel, async (e, ...args) => {
      const s = sender(ctx, e)
      if (!s || !ipcAllowed(channel, s.kind, s.page)) {
        ctx.log?.(`ipc ${channel} refused (${s ? `${s.kind}/${s.page}` : 'unknown sender'})`)
        throw new Error('forbidden')
      }
      return fn(s.win, ...(args as A))
    })
  }
  const str = (v: unknown, max = 2048): string => (typeof v === 'string' && v.length <= max ? v : '')

  handle(IPC.getToken, () => ctx.getToken())
  handle(IPC.setToken, (_w, token: unknown) => ctx.setToken(str(token, 300)))
  handle(IPC.clearToken, () => ctx.clearToken())
  handle(IPC.login, (_w, password: unknown) => ctx.login(str(password, 1024)))
  handle(IPC.logout, () => ctx.logout())

  handle(IPC.getConfig, () => ctx.desktopConfig())
  handle(IPC.setServerUrl, (win, url: unknown) => ctx.setServerUrl(str(url, 300), win))

  handle(IPC.setHotkey, (_w, acc: unknown) => ctx.setHotkey(str(acc, 64)))
  handle(IPC.suggestHotkeys, () => ctx.suggestHotkeys())
  handle(IPC.setHotkeyRecording, (_w, on: unknown) => ctx.setHotkeyRecording(on === true))
  handle(IPC.setAutostart, (_w, enabled: unknown) => ctx.setAutostart(enabled === true))

  handle(IPC.hide, (win) => {
    if (win === ctx.popupWindow()) ctx.hidePopup()
    else win.close()
  })
  handle(IPC.openFull, (_w, route: unknown) => {
    const r = sanitizeRoute(route)
    if (r) ctx.openFull(r)
  })
  handle(IPC.openSettings, () => ctx.openSettings())
  handle(IPC.resize, (win, height: unknown) => {
    if (win === ctx.popupWindow() && typeof height === 'number' && Number.isFinite(height)) ctx.resizePopup(height)
  })
  handle(IPC.setPinned, (win, pinned: unknown) => {
    if (win === ctx.popupWindow()) ctx.setPinned(pinned === true)
  })
  handle(IPC.escapeResult, (win, handled: unknown) => {
    if (win === ctx.popupWindow()) ctx.popupEscape(handled === true)
  })
  handle(IPC.openExternal, (_w, url: unknown) => ctx.openExternal(str(url)))
  handle(IPC.getConnection, () => ctx.connection())
}

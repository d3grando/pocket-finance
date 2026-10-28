// Quick Capture popup: one frameless 420×450 window, created once at startup and only
// shown/hidden afterwards (never recreated). Placement — position.ts, show/hide rules (incl. the
// Esc hand-off to the page) — visibility.ts.
import { BrowserWindow, nativeTheme, screen } from 'electron'
import {
  IPC_EVENTS,
  POPUP_HEIGHT,
  POPUP_MAX_HEIGHT,
  POPUP_MIN_HEIGHT,
  POPUP_WIDTH,
  type ShownEvent,
  type TaskbarEdge,
} from '../shared/ipc'
import { center, computePopupBounds, computeResize, isUsableRect, type Rect } from './position'
import { secureWebPreferences } from './security'
import { VisibilityController, type WindowPort } from './visibility'

export type ShowSource = 'tray' | 'hotkey' | 'other'

export interface PopupOptions {
  preload: string
  devTools: boolean
  /** Bounds of the tray icon (last click bounds on Windows, tray.getBounds() otherwise). */
  trayBounds: () => Rect | null
  isQuitting: () => boolean
  onShow?: () => void
  onOpenFull: () => void
}

export function surfaceColor(): string {
  // DESIGN.md --card: light #ffffff, dark #15171b (opaque: no white flash, no transparency tricks)
  return nativeTheme.shouldUseDarkColors ? '#15171b' : '#ffffff'
}

export class Popup implements WindowPort {
  readonly win: BrowserWindow
  readonly visibility: VisibilityController
  private edge: TaskbarEdge = 'bottom'
  private height = POPUP_HEIGHT
  private source: ShowSource = 'other'
  private displayId: number | null = null

  constructor(private readonly opts: PopupOptions) {
    this.win = new BrowserWindow({
      width: POPUP_WIDTH,
      height: POPUP_HEIGHT,
      show: false,
      frame: false,
      resizable: false,
      movable: true,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      roundedCorners: true,
      hasShadow: true,
      title: 'Pocket',
      backgroundColor: surfaceColor(),
      webPreferences: secureWebPreferences(opts.preload, ['--pocket-window=popup'], opts.devTools),
    })
    this.win.setMenu(null)
    this.visibility = new VisibilityController(this)

    this.win.on('blur', () => {
      if (this.win.webContents.isDevToolsFocused()) return
      this.visibility.onBlur()
    })
    this.win.on('close', (e) => {
      if (this.opts.isQuitting()) return
      e.preventDefault()
      this.visibility.hide()
    })
    this.win.on('hide', () => this.send(IPC_EVENTS.hidden, undefined))
    this.win.webContents.on('devtools-opened', () => this.visibility.setSystemPinned(true))
    this.win.webContents.on('devtools-closed', () => this.visibility.setSystemPinned(this.loginShown))
    this.win.webContents.on('before-input-event', (event, input) => {
      if (input.type !== 'keyDown') return
      const plain = !input.control && !input.alt && !input.shift && !input.meta
      if (input.key === 'Escape' && plain) {
        // Not swallowed: the page closes its menu / dialog / preview or clears the field first;
        // the preload reports whether it did (pageEscape), otherwise the popup hides.
        this.visibility.onEscapeKey()
        return
      }
      if (input.control && input.shift && !input.alt && input.code === 'KeyF') {
        event.preventDefault()
        this.opts.onOpenFull()
      }
    })
    nativeTheme.on('updated', () => this.win.setBackgroundColor(surfaceColor()))
  }

  private loginShown = false

  /** Login screen is shown → never auto-hide (the login dialog must stay open). */
  setLoginShown(v: boolean): void {
    this.loginShown = v
    this.visibility.setSystemPinned(v || this.win.webContents.isDevToolsOpened())
  }

  load(url: string): Promise<void> {
    if (this.win.webContents.getURL() === url) return Promise.resolve()
    return this.win.loadURL(url).catch(() => undefined)
  }

  get taskbarEdge(): TaskbarEdge {
    return this.edge
  }

  // ---- WindowPort (used by VisibilityController) ----
  isVisible(): boolean {
    return this.win.isVisible()
  }

  isFocused(): boolean {
    return this.win.isFocused()
  }

  show(): void {
    this.place()
    this.win.show()
    this.focus()
    const ev: ShownEvent = { edge: this.edge }
    this.send(IPC_EVENTS.shown, ev)
    this.opts.onShow?.()
  }

  hide(): void {
    this.win.hide()
  }

  focus(): void {
    if (this.win.isMinimized()) this.win.restore()
    this.win.focus()
    this.win.webContents.focus()
  }

  // ---- triggers ----
  showFrom(source: ShowSource): void {
    this.source = source
    this.visibility.show()
  }

  toggleFromTray(): void {
    this.source = 'tray'
    this.visibility.onTrayClick()
  }

  toggleFromHotkey(): void {
    this.source = 'hotkey'
    this.visibility.onHotkey()
  }

  setPinned(pinned: boolean): void {
    this.visibility.setRendererPinned(pinned)
  }

  /** Preload: the page finished handling an Esc key; `handled` = it called preventDefault(). */
  pageEscape(handled: boolean): void {
    this.visibility.onPageEscape(handled)
  }

  /** Content asked for a different height (bulk preview). Grows away from the taskbar. */
  resize(height: number): void {
    if (!Number.isFinite(height)) return
    this.height = Math.round(Math.min(Math.max(height, POPUP_MIN_HEIGHT), POPUP_MAX_HEIGHT))
    if (!this.win.isVisible()) return
    const cur = this.win.getBounds()
    const display = screen.getDisplayMatching(cur)
    const next = computeResize(cur, this.height, this.edge, display.workArea, { min: POPUP_MIN_HEIGHT, max: POPUP_MAX_HEIGHT })
    this.setBoundsChecked(next)
  }

  private anchorDisplay(): { display: Electron.Display; tray: Rect | null } {
    const tray = this.opts.trayBounds()
    const trayOk = isUsableRect(tray) ? tray : null
    const cursor = screen.getCursorScreenPoint()
    if (this.source === 'tray' && trayOk) {
      return { display: screen.getDisplayNearestPoint(center(trayOk)), tray: trayOk }
    }
    // hotkey / second instance: the display the user works on (cursor); use the tray icon
    // only if it lives on that display, otherwise that display's tray corner.
    const display = screen.getDisplayNearestPoint(cursor)
    if (trayOk && screen.getDisplayNearestPoint(center(trayOk)).id === display.id) return { display, tray: trayOk }
    return { display, tray: null }
  }

  private place(): void {
    const { display, tray } = this.anchorDisplay()
    const p = computePopupBounds({
      trayBounds: tray,
      cursor: screen.getCursorScreenPoint(),
      display,
      size: { width: POPUP_WIDTH, height: this.height },
      platform: process.platform,
    })
    this.edge = p.edge
    this.displayId = display.id
    this.setBoundsChecked(p.bounds)
  }

  /**
   * Windows with mixed DPI: moving a window to a monitor with another scale factor can apply
   * the old factor to the size on the first setBounds — re-apply once if the result differs.
   */
  private setBoundsChecked(b: Rect): void {
    this.win.setBounds(b)
    const got = this.win.getBounds()
    if (got.width !== b.width || got.height !== b.height || got.x !== b.x || got.y !== b.y) this.win.setBounds(b)
  }

  get currentDisplayId(): number | null {
    return this.displayId
  }

  private send(channel: string, payload: unknown): void {
    if (!this.win.isDestroyed()) this.win.webContents.send(channel, payload)
  }
}

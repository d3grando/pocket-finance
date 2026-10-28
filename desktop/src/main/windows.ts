// «Полная панель» (big window with the same packaged web app — same device token, no second
// login) and the desktop settings window. Both are created on demand and reused while open.
import { BrowserWindow, screen } from 'electron'
import { UI_PREFIX } from '../shared/ipc'
import { surfaceColor } from './popup'
import { secureWebPreferences } from './security'
import { appUrl } from './urls'

export interface WindowDeps {
  preload: string
  devTools: boolean
}

function centeredOnCursorDisplay(width: number, height: number) {
  const wa = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea
  const w = Math.min(width, wa.width - 32)
  const h = Math.min(height, wa.height - 32)
  return { x: Math.round(wa.x + (wa.width - w) / 2), y: Math.round(wa.y + (wa.height - h) / 2), width: w, height: h }
}

function reveal(win: BrowserWindow): void {
  if (win.isMinimized()) win.restore()
  win.show()
  win.focus()
}

export class FullWindow {
  private win: BrowserWindow | null = null

  constructor(private readonly deps: WindowDeps) {}

  get window(): BrowserWindow | null {
    return this.win && !this.win.isDestroyed() ? this.win : null
  }

  open(route: string): void {
    const url = appUrl(route)
    const existing = this.window
    if (existing) {
      if (route !== '/' || !existing.webContents.getURL().startsWith(appUrl('/'))) void existing.loadURL(url).catch(() => undefined)
      reveal(existing)
      return
    }
    const win = new BrowserWindow({
      ...centeredOnCursorDisplay(1280, 860),
      minWidth: 900,
      minHeight: 600,
      show: false,
      title: 'Pocket',
      autoHideMenuBar: true,
      backgroundColor: surfaceColor(),
      webPreferences: secureWebPreferences(this.deps.preload, ['--pocket-window=full'], this.deps.devTools),
    })
    win.setMenu(null)
    this.win = win
    win.once('ready-to-show', () => reveal(win))
    win.on('closed', () => {
      if (this.win === win) this.win = null
    })
    void win.loadURL(url).catch(() => undefined)
  }

  close(): void {
    this.window?.close()
  }
}

export class SettingsWindow {
  private win: BrowserWindow | null = null

  constructor(private readonly deps: WindowDeps) {}

  get window(): BrowserWindow | null {
    return this.win && !this.win.isDestroyed() ? this.win : null
  }

  open(): void {
    const existing = this.window
    if (existing) {
      reveal(existing)
      return
    }
    const win = new BrowserWindow({
      ...centeredOnCursorDisplay(480, 640),
      resizable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      show: false,
      title: 'Pocket — настройки',
      autoHideMenuBar: true,
      backgroundColor: surfaceColor(),
      webPreferences: secureWebPreferences(this.deps.preload, ['--pocket-window=settings'], this.deps.devTools),
    })
    win.setMenu(null)
    this.win = win
    win.once('ready-to-show', () => reveal(win))
    win.on('closed', () => {
      if (this.win === win) this.win = null
    })
    void win.loadURL(appUrl(`${UI_PREFIX}settings.html`)).catch(() => undefined)
  }
}

// Tray icon «Pocket» with the context menu from the spec: Открыть / Полная панель / Настройки / Выход.
// Left click toggles the popup; the icon turns grey (offline coin) when the server is unreachable.
import path from 'node:path'
import { Menu, Tray, nativeImage, type NativeImage } from 'electron'
import type { ConnectionState } from '../shared/ipc'
import type { Rect } from './position'

export interface TrayActions {
  toggle(): void
  open(): void
  openFull(): void
  openSettings(): void
  quit(): void
}

function loadIcon(assetsDir: string, offline: boolean): NativeImage {
  const base = offline ? 'tray-offline' : 'tray'
  // macOS menu bar: a template image (black + alpha, @2x picked up automatically) that the system
  // tints for light/dark; offline keeps the grey coin so the state is visible.
  if (process.platform === 'darwin' && !offline) {
    const tpl = nativeImage.createFromPath(path.join(assetsDir, 'trayTemplate.png'))
    if (!tpl.isEmpty()) {
      tpl.setTemplateImage(true)
      return tpl
    }
  }
  // Windows: multi-size .ico (16/20/24/32/40/48 for 100–300% scaling); elsewhere png + @2x
  const file = process.platform === 'win32' ? `${base}.ico` : `${base}.png`
  const img = nativeImage.createFromPath(path.join(assetsDir, file))
  return img.isEmpty() ? nativeImage.createEmpty() : img
}

export class TrayController {
  readonly tray: Tray
  private lastClickBounds: Rect | null = null
  private state: ConnectionState = 'unknown'

  constructor(
    private readonly assetsDir: string,
    private readonly actions: TrayActions,
  ) {
    this.tray = new Tray(loadIcon(assetsDir, false))
    this.tray.setToolTip('Pocket')
    const menu = this.buildMenu()
    if (process.platform === 'darwin') {
      // On macOS a tray with a context menu opens the menu on a left click and never emits
      // «click»: keep the left click for the popup and show the menu on a right click.
      this.tray.on('right-click', () => this.tray.popUpContextMenu(menu))
    } else {
      this.tray.setContextMenu(menu)
    }
    this.tray.on('click', (_e, bounds) => {
      if (bounds && bounds.width > 0) this.lastClickBounds = bounds
      this.actions.toggle()
    })
  }

  private buildMenu(): Menu {
    return Menu.buildFromTemplate([
      { label: 'Открыть', click: () => this.actions.open() },
      { label: 'Полная панель', click: () => this.actions.openFull() },
      { label: 'Настройки', click: () => this.actions.openSettings() },
      { type: 'separator' },
      { label: 'Выход', click: () => this.actions.quit() },
    ])
  }

  /** Tray icon bounds for popup placement (click bounds are the most reliable on Windows). */
  bounds(): Rect | null {
    try {
      const b = this.tray.getBounds()
      if (b && b.width > 0 && b.height > 0) return b
    } catch {
      /* not supported on this platform */
    }
    return this.lastClickBounds
  }

  setConnection(state: ConnectionState): void {
    if (state === this.state) return
    const wasOffline = this.state === 'offline'
    this.state = state
    const offline = state === 'offline'
    if (offline !== wasOffline) this.tray.setImage(loadIcon(this.assetsDir, offline))
    this.tray.setToolTip(offline ? 'Pocket — нет связи с сервером' : 'Pocket')
  }

  destroy(): void {
    this.tray.destroy()
  }
}

// Electron main. Tray «Pocket», 420×450 frameless popup above the tray,
// global hotkey, single instance lock, autostart, app://pocket protocol serving the packaged
// frontend, contextIsolation + sandbox, no nodeIntegration, navigation/new windows blocked.
// The popup is created once and only shown/hidden. Esc goes to the page first (closes its top
// layer / clears the field), an Esc the page leaves alone hides; blur hides unless pinned.
import fs from 'node:fs'
import inspector from 'node:inspector'
import os from 'node:os'
import path from 'node:path'
import { app, BrowserWindow, dialog, globalShortcut, Menu, net, Notification, safeStorage } from 'electron'
import { formatAccelerator } from '../shared/accelerator'
import { IPC_EVENTS, QUICK_ROUTE, UI_PREFIX, type DesktopConfig, type WindowKind } from '../shared/ipc'
import { ApiClient, platformName } from './api-client'
import { electronLoginItemPort, initAutostart, launchedHidden, setAutostart } from './autostart'
import { stripRemoteDebugging } from './cli-hardening'
import { ConfigStore } from './config'
import { ConnectionMonitor } from './connection'
import { HotkeyManager } from './hotkey'
import { registerIpc } from './ipc-handlers'
import { Popup } from './popup'
import { handleAppProtocol, registerSchemePrivileges } from './protocol'
import { hardenApp, openExternalSafe } from './security'
import { changeServerUrl } from './server-change'
import { TokenStore } from './token-store'
import { TrayController } from './tray'
import { appUrl, normalizeServerUrl } from './urls'
import { FullWindow, SettingsWindow } from './windows'

const DEV = !app.isPackaged
const E2E = DEV && process.env.POCKET_E2E === '1'
const log = (msg: string) => {
  if (DEV) console.log(`[pocket] ${msg}`)
}

// ---- before ready: command line, paths, sandbox, scheme, single instance ----
// Packaged builds: no remote debugging (DevTools protocol → the device token); --inspect,
// NODE_OPTIONS and ELECTRON_RUN_AS_NODE are already off via fuses (electron-builder.yml).
stripRemoteDebugging(app.commandLine, { packaged: app.isPackaged, inspectorActive: inspector.url() !== undefined })
if (DEV && process.env.POCKET_USER_DATA) app.setPath('userData', path.resolve(process.env.POCKET_USER_DATA))
// OS-level sandbox for every renderer (webPreferences.sandbox is set per window as well).
// Skipped only when Chromium's sandbox is explicitly disabled (root on the Linux test stand),
// otherwise enableSandbox() forces it back on and every child process aborts.
if (!app.commandLine.hasSwitch('no-sandbox') && !process.env.ELECTRON_DISABLE_SANDBOX) app.enableSandbox()
registerSchemePrivileges()
// must match appId in electron-builder.yml (taskbar grouping, notifications)
if (process.platform === 'win32') app.setAppUserModelId('io.github.d3grando.pocket')

const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  void main()
}

async function main(): Promise<void> {
  let quitting = false
  let popup: Popup | null = null
  app.on('second-instance', () => popup?.showFrom('other'))
  app.on('before-quit', () => {
    quitting = true
  })
  // Stay alive in the tray when every window is closed.
  app.on('window-all-closed', () => {})

  await app.whenReady()
  if (process.platform === 'darwin') {
    // Menu bar app: no Dock icon (LSUIElement in the bundle; this covers dev runs). The menu is
    // invisible without a Dock icon, but its Edit roles are what make ⌘C/⌘V/⌘A/⌘Z work in inputs.
    app.dock?.hide()
    Menu.setApplicationMenu(Menu.buildFromTemplate([{ role: 'appMenu' }, { role: 'editMenu' }]))
  } else {
    Menu.setApplicationMenu(null)
  }

  const appRoot = app.getAppPath()
  const userData = app.getPath('userData')
  const uiRoot = path.join(appRoot, 'out', 'ui')
  const webRoot = DEV && process.env.POCKET_RENDERER_DIR ? path.resolve(process.env.POCKET_RENDERER_DIR) : path.join(appRoot, 'renderer')
  const preload = path.join(appRoot, 'out', 'preload', 'index.js')
  const assetsDir = app.isPackaged ? path.join(process.resourcesPath, 'assets') : path.join(appRoot, 'assets')
  const devTools = DEV
  const rendererKind: DesktopConfig['renderer'] = fs.existsSync(path.join(webRoot, 'index.html')) ? 'web' : 'lite'

  const config = new ConfigStore(path.join(userData, 'config.json'))
  if (DEV && process.env.POCKET_SERVER_URL) {
    const s = normalizeServerUrl(process.env.POCKET_SERVER_URL)
    if (s.ok) config.update({ serverUrl: s.serverUrl })
  }
  const server = () => normalizeServerUrl(config.get().serverUrl)
  const apiBaseUrl = () => {
    const s = server()
    return s.ok ? s.apiBaseUrl : ''
  }
  const apiOrigin = () => {
    try {
      return new URL(apiBaseUrl()).origin
    } catch {
      return ''
    }
  }
  // Linux dev stand without a keyring (Xvfb tests): Chromium's "basic" store needs an explicit
  // opt-in. Windows always uses DPAPI; packaged builds never take this branch.
  if (DEV && process.platform === 'linux' && safeStorage.getSelectedStorageBackend() === 'basic_text') {
    safeStorage.setUsePlainTextEncryption(true)
  }
  const tokens = new TokenStore(path.join(userData, 'device-token.bin'), safeStorage)
  const api = new ApiClient((url, init) => net.fetch(url, init), apiBaseUrl, app.getVersion())

  const serverUrl = () => config.get().serverUrl
  hardenApp({
    allowLoopbackApi: () => /^http:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|\/|$)/.test(apiBaseUrl()),
    apiOrigin,
    serverUrl,
    log,
  })
  handleAppProtocol(() => ({ webRoot, uiRoot }), apiOrigin)

  // ---- windows ----
  const signedIn = () => tokens.get() !== null
  const captureUrl = () => (rendererKind === 'web' ? appUrl(QUICK_ROUTE) : appUrl(`${UI_PREFIX}capture.html`))
  const loginUrl = () => appUrl(`${UI_PREFIX}login.html`)
  const full = new FullWindow({ preload, devTools })
  const settings = new SettingsWindow({ preload, devTools })
  let tray: TrayController | null = null

  const openFull = (route = '/') => {
    if (!signedIn()) {
      popup?.showFrom('other')
      return
    }
    popup?.hide()
    if (rendererKind === 'web') full.open(route)
    else openExternalSafe(`${serverUrl()}${route}`, serverUrl())
  }
  const openSettings = () => settings.open()

  popup = new Popup({
    preload,
    devTools,
    trayBounds: () => tray?.bounds() ?? null,
    isQuitting: () => quitting,
    onShow: () => {
      void connection.check()
      void verifyToken()
    },
    onOpenFull: () => openFull('/'),
  })
  const p = popup

  const showPage = () => {
    const login = !signedIn()
    p.setLoginShown(login)
    void p.load(login ? loginUrl() : captureUrl())
  }

  const allWindows = () => [p.win, full.window, settings.window].filter((w): w is BrowserWindow => !!w && !w.isDestroyed())
  const broadcast = (channel: string, payload: unknown) => {
    for (const w of allWindows()) w.webContents.send(channel, payload)
  }

  // ---- connection indicator ----
  const connection = new ConnectionMonitor(
    () => api.health(),
    (s) => {
      tray?.setConnection(s)
      broadcast(IPC_EVENTS.connection, s)
    },
  )

  // ---- auth ----
  let lastTokenCheck = 0
  const signedOut = () => {
    tokens.clear()
    full.close()
    showPage()
    broadcast(IPC_EVENTS.config, desktopConfig())
    p.showFrom('other')
  }
  const verifyToken = async () => {
    const token = tokens.get()
    if (!token || Date.now() - lastTokenCheck < 60_000) return
    lastTokenCheck = Date.now()
    if ((await api.checkToken(token)) === 'revoked' && tokens.get() === token) signedOut()
  }
  const signedInNow = () => {
    lastTokenCheck = Date.now()
    showPage()
    broadcast(IPC_EVENTS.config, desktopConfig())
  }

  // ---- hotkey ----
  const hotkey = new HotkeyManager(globalShortcut, () => p.toggleFromHotkey())
  const hk = hotkey.init(config.get().hotkey)
  if (!hk.ok) notifyHotkeyProblem(config.get().hotkey, openSettings)

  // ---- autostart ----
  const loginItems = electronLoginItemPort(app, process.platform)
  const auto = initAutostart(config.get(), loginItems)
  config.update(auto)

  const deviceName = (): string => os.hostname() || 'Pocket'
  const desktopConfig = (): DesktopConfig => {
    const c = config.get()
    return {
      serverUrl: c.serverUrl,
      apiBaseUrl: apiBaseUrl(),
      hotkey: c.hotkey,
      hotkeyStatus: hotkey.status,
      autostart: c.autostart,
      version: app.getVersion(),
      platform: process.platform,
      deviceName: deviceName(),
      renderer: rendererKind,
      signedIn: signedIn(),
    }
  }

  const windowKind = (w: BrowserWindow): WindowKind | null =>
    w === p.win ? 'popup' : w === full.window ? 'full' : w === settings.window ? 'settings' : null

  registerIpc({
    ownWindows: allWindows,
    popupWindow: () => p.win,
    windowKind,
    log,
    getToken: () => tokens.get(),
    setToken: (token) => {
      const r = tokens.set(token)
      if (r.ok) signedInNow()
      return { ok: r.ok }
    },
    clearToken: () => {
      if (tokens.get() !== null) signedOut()
    },
    login: async (password) => {
      const { result, token } = await api.deviceLogin(password, { name: deviceName(), platform: platformName(process.platform) })
      if (result.ok && token) {
        tokens.set(token)
        signedInNow()
        void connection.check()
      }
      return result
    },
    logout: async () => {
      const token = tokens.get()
      if (token) await api.logout(token)
      signedOut()
    },
    desktopConfig,
    setServerUrl: async (url, from) => {
      const r = await changeServerUrl(url, {
        current: serverUrl,
        // native dialog: a page cannot fake or auto-accept it
        confirm: async (fromHost, toHost) => {
          const { response } = await dialog.showMessageBox(from, {
            type: 'warning',
            buttons: ['Сменить сервер', 'Отмена'],
            defaultId: 1,
            cancelId: 1,
            noLink: true,
            title: 'Pocket',
            message: `Сменить сервер ${fromHost} на ${toHost}?`,
            detail: 'Вход на этом устройстве будет сброшен, пароль понадобится ввести для нового сервера. Если вы не меняли адрес сами — нажмите «Отмена».',
          })
          return response === 0
        },
        apply: (next) => {
          config.update({ serverUrl: next })
          // a device token belongs to one server: sign in again on the new one
          tokens.clear()
          full.close()
          showPage()
          connection.reset()
        },
      })
      if (!r.ok) return { ok: false, message: r.message }
      const cfg = desktopConfig()
      broadcast(IPC_EVENTS.config, cfg)
      return { ok: true, config: cfg }
    },
    setHotkey: (acc) => {
      const r = hotkey.apply(acc)
      if (r.ok) config.update({ hotkey: r.accelerator })
      broadcast(IPC_EVENTS.config, desktopConfig())
      return r
    },
    suggestHotkeys: () => hotkey.suggestions(),
    setHotkeyRecording: (on) => (on ? hotkey.suspend() : hotkey.resume()),
    setAutostart: (enabled) => {
      const actual = setAutostart(enabled, loginItems)
      config.update({ autostart: enabled, autostartInitialized: true })
      broadcast(IPC_EVENTS.config, desktopConfig())
      return actual
    },
    hidePopup: () => p.visibility.hide(),
    openFull: (route) => openFull(route),
    openSettings,
    resizePopup: (h) => p.resize(h),
    setPinned: (pinned) => p.setPinned(pinned),
    popupEscape: (handled) => p.pageEscape(handled),
    openExternal: (url) => openExternalSafe(url, serverUrl()),
    connection: () => connection.state,
  })

  tray = new TrayController(assetsDir, {
    toggle: () => p.toggleFromTray(),
    open: () => p.showFrom('tray'),
    openFull: () => openFull('/'),
    openSettings,
    quit: () => {
      quitting = true
      app.quit()
    },
  })

  app.on('will-quit', () => {
    hotkey.dispose()
    globalShortcut.unregisterAll()
    connection.stop()
  })

  showPage()
  connection.start()
  if (!launchedHidden(process.argv) || !signedIn()) {
    // first launch / manual start: show the popup; autostart (--hidden) stays in the tray
    p.win.webContents.once('did-finish-load', () => p.showFrom('other'))
  }

  if (E2E) {
    ;(globalThis as Record<string, unknown>).__pocketTest = {
      popupId: () => p.win.id,
      trayClick: () => p.toggleFromTray(),
      hotkey: () => p.toggleFromHotkey(),
      blur: () => p.visibility.onBlur(),
      openFull: (route?: string) => openFull(route ?? '/'),
      openSettings,
      hotkeyActive: () => hotkey.active,
      pinned: () => p.visibility.pinned,
      pendingEscapes: () => p.visibility.pendingEscapes,
      config: () => desktopConfig(),
      tokenFile: () => path.join(userData, 'device-token.bin'),
    }
  }
  log(`ready: renderer=${rendererKind} api=${apiBaseUrl() || '(not set)'} hotkey=${hotkey.active ?? '-'}`)
}

function notifyHotkeyProblem(acc: string, openSettings: () => void): void {
  if (!Notification.isSupported()) return
  const n = new Notification({
    title: 'Pocket',
    body: `Сочетание ${formatAccelerator(acc, process.platform)} занято другой программой. Назначьте другое в настройках.`,
  })
  n.on('click', openSettings)
  n.show()
}

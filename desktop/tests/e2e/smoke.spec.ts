// Electron smoke (real app under Xvfb, Playwright _electron) against tests/e2e/mock-api.ts.
// Tray/hotkey OS integration cannot be observed on Linux — those paths are driven through the
// same controller methods the tray/hotkey call (globalThis.__pocketTest, dev+POCKET_E2E only).
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import electronPath from 'electron'
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test'
import { MockApi, PASSWORD, TOKEN } from './mock-api'

const APP_DIR = path.resolve(__dirname, '../..')
const FIXTURE_WEB = path.join(__dirname, 'fixtures', 'web-renderer')

test.describe.configure({ mode: 'serial' })

const mock = new MockApi()
let tmp: string
let emptyRenderer: string
let app: ElectronApplication | null = null

function userDir(name: string): string {
  const d = path.join(tmp, name)
  fs.mkdirSync(d, { recursive: true })
  return d
}

async function launch(userData: string, renderer = emptyRenderer, server: string | null = mock.url): Promise<{ app: ElectronApplication; page: Page }> {
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    ELECTRON_DISABLE_SANDBOX: '1', // the test container runs as root; OS sandbox off, renderer isolation still on
    POCKET_E2E: '1',
    POCKET_USER_DATA: userData,
    POCKET_RENDERER_DIR: renderer,
  }
  if (server) env.POCKET_SERVER_URL = server
  else delete env.POCKET_SERVER_URL // fresh install: no server until the user enters it
  const a = await electron.launch({
    executablePath: electronPath as unknown as string,
    args: ['--no-sandbox', '--password-store=basic', APP_DIR],
    env,
    timeout: 30_000,
  })
  app = a
  const page = await a.firstWindow()
  return { app: a, page }
}

type PopupState = { id: number; visible: boolean; focused: boolean; bounds: Electron.Rectangle; pinned: boolean; windows: number; workArea: Electron.Rectangle }
async function popup(a: ElectronApplication): Promise<PopupState> {
  return a.evaluate(({ BrowserWindow, screen }) => {
    const t = (globalThis as any).__pocketTest
    const w = BrowserWindow.fromId(t.popupId())!
    return {
      id: w.id,
      visible: w.isVisible(),
      focused: w.isFocused(),
      bounds: w.getBounds(),
      pinned: t.pinned(),
      windows: BrowserWindow.getAllWindows().length,
      workArea: screen.getDisplayMatching(w.getBounds()).workArea,
    }
  })
}
const osKey = (a: ElectronApplication, keyCode: string) =>
  a.evaluate(({ BrowserWindow }, k) => {
    const w = BrowserWindow.fromId((globalThis as any).__pocketTest.popupId())!
    w.webContents.sendInputEvent({ type: 'keyDown', keyCode: k })
    w.webContents.sendInputEvent({ type: 'keyUp', keyCode: k })
  }, keyCode)
const hook = (a: ElectronApplication, name: string, arg?: string) =>
  a.evaluate((_e, [n, x]) => (globalThis as any).__pocketTest[n as string](x), [name, arg] as const)

async function login(page: Page) {
  await page.waitForURL(/__pocket\/login\.html/)
  await page.locator('#password').fill(PASSWORD)
  await page.keyboard.press('Enter')
}

test.beforeAll(async () => {
  await mock.start()
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pocket-e2e-'))
  emptyRenderer = userDir('no-web-build')
})

test.afterEach(async () => {
  if (app) await app.close().catch(() => undefined)
  app = null
})

test.afterAll(async () => {
  await mock.stop()
  fs.rmSync(tmp, { recursive: true, force: true })
})

test('first run: login screen in the 420×450 popup, password once, token encrypted', async () => {
  const ud = userDir('first-run')
  const { app: a, page } = await launch(ud)
  await page.waitForURL(/__pocket\/login\.html/)
  await expect(page.locator('h1')).toHaveText('Вход в Pocket')
  await expect(page.locator('#password')).toBeFocused()
  await expect.poll(async () => (await popup(a)).visible).toBe(true)
  const s = await popup(a)
  expect(s.bounds.width).toBe(420)
  expect(s.bounds.height).toBe(450)
  // inside the work area, pressed to the tray corner (bottom-right without a taskbar on Xvfb)
  expect(s.bounds.x + s.bounds.width).toBeLessThanOrEqual(s.workArea.x + s.workArea.width)
  expect(s.bounds.y + s.bounds.height).toBeLessThanOrEqual(s.workArea.y + s.workArea.height)
  expect(s.bounds.x).toBeGreaterThan(s.workArea.x + s.workArea.width / 2)
  expect(s.pinned).toBe(true) // login screen never auto-hides
  await hook(a, 'blur')
  expect((await popup(a)).visible).toBe(true)

  await page.locator('#password').fill('wrong')
  await page.locator('#submit').click()
  await expect(page.locator('#error')).toHaveText('Неверный пароль')

  await page.locator('#password').fill(PASSWORD)
  await page.keyboard.press('Enter')
  await page.waitForURL(/__pocket\/capture\.html/)
  const loginReq = mock.find('POST', '/auth/device-login').at(-1)!
  expect(loginReq.body).toMatchObject({ username: 'owner', password: PASSWORD, platform: 'linux' })

  const tokenFile = path.join(ud, 'device-token.bin')
  expect(fs.existsSync(tokenFile)).toBe(true)
  expect(fs.readFileSync(tokenFile).toString('latin1')).not.toContain('pfd_')
  expect((await popup(a)).id).toBe(s.id) // same window, only the page changed
  expect((await popup(a)).pinned).toBe(false)
})

test('record «еда 250к» → card with Отменить/Изменить → undo (Bearer + Idempotency-Key)', async () => {
  const { page } = await launch(userDir('first-run')) // token from the previous test
  await page.waitForURL(/__pocket\/capture\.html/)
  const input = page.locator('#input')
  await expect(input).toBeFocused()
  await expect(input).toHaveAttribute('placeholder', 'На что потратил деньги?')

  const before = mock.find('POST', '/assistant/messages').length
  await input.fill('еда 250к')
  await input.press('Enter')
  const card = page.locator('.card[data-kind="created"]')
  await expect(card.locator('.title')).toHaveText(/250\s000\s₫ · Еда/)
  await expect(card.locator('.btn[data-kind="undo"]')).toHaveText('Отменить')
  await expect(card.locator('.btn[data-kind="edit"]')).toHaveText('Изменить')
  await expect(input).toHaveValue('')
  await expect(input).toBeFocused()

  const reqs = mock.find('POST', '/assistant/messages')
  expect(reqs.length).toBe(before + 1)
  const req = reqs.at(-1)!
  expect(req.headers.authorization).toBe(`Bearer ${TOKEN}`)
  expect(req.headers['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/)
  expect(req.headers.origin).toBe('app://pocket')
  expect(req.body).toEqual({ text: 'еда 250к', context: { edit_transaction_id: null } })

  await expect(page.locator('#kpi-today')).toHaveText(/−250\s000\s₫/)
  await expect(page.locator('#recent li').first()).toContainText('Еда')

  // Ctrl+Z with an empty input = «Отменить»
  await input.press('Control+z')
  await expect(page.locator('.undone')).toContainText('Отменено')
  const undo = mock.find('POST', '/actions/').at(-1)!
  expect(undo.headers['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/)
  expect(undo.headers.authorization).toBe(`Bearer ${TOKEN}`)
  await expect(page.locator('.undone .btn')).toHaveText('Вернуть')

  // «Изменить»: next message carries context.edit_transaction_id
  await input.fill('кофе 45к')
  await input.press('Enter')
  await expect(page.locator('.card[data-kind="created"] .title')).toHaveText(/45\s000\s₫ · кофе/)
  await page.locator('.card .btn[data-kind="edit"]').click()
  await expect(page.locator('#edit-hint')).toBeVisible()
  const sent = mock.find('POST', '/assistant/messages').length
  await input.fill('50к')
  await input.press('Enter')
  // the request reaches the mock asynchronously after Enter — poll instead of reading it at once
  await expect.poll(() => mock.find('POST', '/assistant/messages').length).toBe(sent + 1)
  const last = mock.find('POST', '/assistant/messages').at(-1)!
  expect(last.body).toMatchObject({ text: '50к' })
  expect((last.body as { context: { edit_transaction_id: number } }).context.edit_transaction_id).toBeGreaterThan(0)
  await expect(page.locator('.card[data-kind="unclear"]')).toBeVisible() // mock answers «unclear» for «50к»
  await expect(input).toHaveValue('50к') // keep_input
})

test('window: Esc hides, hotkey/tray toggle, blur hides unless pinned, never recreated', async () => {
  const { app: a, page } = await launch(userDir('first-run'))
  await page.waitForURL(/__pocket\/capture\.html/)
  await expect.poll(async () => (await popup(a)).visible).toBe(true)
  const first = await popup(a)

  // A real key press (through Chromium's input pipeline → before-input-event in main);
  // Playwright's CDP key events bypass that hook.
  await osKey(a, 'Escape')
  await expect.poll(async () => (await popup(a)).visible).toBe(false)

  await hook(a, 'hotkey')
  await expect.poll(async () => (await popup(a)).visible).toBe(true)
  await expect(page.locator('#input')).toBeFocused()
  expect((await popup(a)).focused).toBe(true)
  await hook(a, 'hotkey') // second press hides
  expect((await popup(a)).visible).toBe(false)
  await hook(a, 'hotkey')
  await expect.poll(async () => (await popup(a)).visible).toBe(true)

  await hook(a, 'blur')
  expect((await popup(a)).visible).toBe(false)

  await page.waitForTimeout(400) // past the blur→tray-click grace period
  await hook(a, 'trayClick')
  expect((await popup(a)).visible).toBe(true)
  await page.evaluate(() => window.pocket.window.setPinned(true)) // e.g. needs-account choice open
  await hook(a, 'blur')
  expect((await popup(a)).visible).toBe(true)
  await osKey(a, 'Escape') // pinned: Esc goes to the page, window stays
  expect((await popup(a)).visible).toBe(true)
  await page.evaluate(() => window.pocket.window.setPinned(false))
  await hook(a, 'trayClick')
  expect((await popup(a)).visible).toBe(false)

  // grows up from the tray for a bulk preview, max 640
  await hook(a, 'hotkey')
  const b0 = (await popup(a)).bounds
  await page.evaluate(() => window.pocket.window.resize(900))
  const b1 = (await popup(a)).bounds
  expect(b1.height).toBe(640)
  expect(b1.y + b1.height).toBe(b0.y + b0.height)

  // Esc goes to the page first (DESIGN §9.1/§11): text in the field → cleared, popup stays;
  // the next Esc (nothing left to close) hides
  await page.locator('#input').fill('черновик 10к')
  await osKey(a, 'Escape')
  await expect(page.locator('#input')).toHaveValue('')
  await expect.poll(() => hook(a, 'pendingEscapes')).toBe(0)
  expect((await popup(a)).visible).toBe(true)
  await osKey(a, 'Escape')
  await expect.poll(async () => (await popup(a)).visible).toBe(false)

  // a hung page never answers: main still hides on Esc after a short timeout
  await hook(a, 'hotkey')
  await expect.poll(async () => (await popup(a)).visible).toBe(true)
  await page.locator('#input').fill('черновик')
  let busyDone = false
  const busy = page
    .evaluate(() => {
      const t = Date.now()
      while (Date.now() - t < 3000) {
        /* block the renderer */
      }
    })
    .then(() => (busyDone = true))
  await page.waitForTimeout(200)
  await osKey(a, 'Escape')
  await expect.poll(async () => (await popup(a)).visible, { timeout: 2000 }).toBe(false)
  expect(busyDone).toBe(false)
  await busy
  await expect.poll(() => hook(a, 'pendingEscapes')).toBe(0)
  await hook(a, 'hotkey') // the late verdict of that key does not hide the popup again
  await page.waitForTimeout(300)
  expect((await popup(a)).visible).toBe(true)

  const last = await popup(a)
  expect(last.id).toBe(first.id)
  expect(last.windows).toBe(first.windows)
  expect(await hook(a, 'hotkeyActive')).toBe('Ctrl+Alt+Space')
})

test('security: isolated renderer, no navigation/new windows, protocol confinement, CSP', async () => {
  const { app: a, page } = await launch(userDir('first-run'))
  await page.waitForURL(/__pocket\/capture\.html/)
  const env = await page.evaluate(() => ({
    require: typeof (window as any).require,
    process: typeof (window as any).process,
    module: typeof (window as any).module,
    ipc: typeof (window as any).ipcRenderer,
    keys: Object.keys(window.pocket).sort(),
  }))
  expect(env).toEqual({
    require: 'undefined',
    process: 'undefined',
    module: 'undefined',
    ipc: 'undefined',
    // onShow: legacy alias of window.onShown kept in the preload for older web builds
    keys: ['auth', 'autostart', 'config', 'connection', 'hotkey', 'isDesktop', 'onShow', 'openExternal', 'platform', 'window', 'windowKind'].sort(),
  })
  const windows = (await popup(a)).windows
  expect(await page.evaluate(() => window.open('https://example.com/') === null)).toBe(true)
  await page.evaluate(() => {
    location.href = 'https://example.com/'
  })
  await page.waitForTimeout(500)
  expect(page.url()).toMatch(/^app:\/\/pocket\/__pocket\/capture\.html/)
  expect((await popup(a)).windows).toBe(windows)

  const probe = await page.evaluate(async () => {
    const r1 = await fetch('app://pocket/%2e%2e/%2e%2e/etc/passwd')
    const r2 = await fetch('app://pocket/__pocket/capture.html')
    return { traversal: r1.status, csp: r2.headers.get('content-security-policy') ?? '' }
  })
  expect(probe.traversal).toBe(404)
  expect(probe.csp).toContain("script-src 'self'")
  expect(probe.csp).toContain(`connect-src 'self' ${mock.url}`)

  expect(await page.evaluate(() => window.pocket.openExternal('file:///etc/passwd'))).toBe(false)
  expect(await page.evaluate(() => window.pocket.openExternal('http://example.com/'))).toBe(false)
  // only the configured server and t.me/<bot> may open in the browser: no way to carry a token out
  expect(await page.evaluate(() => window.pocket.openExternal('https://evil.example.net/?t=pfd_x'))).toBe(false)
  await page.evaluate(() => window.pocket.window.openFull('//evil.example.com/x'))
  expect((await popup(a)).windows).toBe(windows)
  // the capture page may not set a token, change the server, the hotkey or autostart (settings window only)
  const refused = await page.evaluate(async () => {
    const r = async (f: () => Promise<unknown>) => f().then(() => 'allowed', (e: Error) => (String(e.message).includes('forbidden') ? 'forbidden' : String(e)))
    return {
      setToken: await r(() => window.pocket.auth.setToken('garbage')),
      server: await r(() => window.pocket.config.setServerUrl('https://evil.example.net')),
      hotkey: await r(() => window.pocket.hotkey.set('Ctrl+Alt+P')),
      autostart: await r(() => window.pocket.autostart.set(false)),
      logout: await r(() => window.pocket.auth.logout()),
    }
  })
  expect(refused).toEqual({ setToken: 'forbidden', server: 'forbidden', hotkey: 'forbidden', autostart: 'forbidden', logout: 'forbidden' })
  expect((await hook(a, 'config')).serverUrl).toBe(mock.url)
  expect(await hook(a, 'hotkeyActive')).toBe('Ctrl+Alt+Space')
})

test('fresh install without a built-in server: the login screen asks for the address first', async () => {
  const { app: a, page } = await launch(userDir('no-server'), emptyRenderer, null)
  await page.waitForURL(/__pocket\/login\.html/)
  await expect(page.locator('#server-form')).toBeVisible()
  await expect(page.locator('#form')).toBeHidden()
  await expect(page.locator('#server-url')).toBeFocused()
  expect((await hook(a, 'config')).serverUrl).toBe('')
  const logins = mock.find('POST', '/auth/device-login').length

  await page.locator('#server-url').fill('http://example.com')
  await page.keyboard.press('Enter')
  await expect(page.locator('#server-error')).toHaveText('Нужен адрес https://')

  await page.locator('#server-url').fill(mock.url)
  await page.keyboard.press('Enter') // the first address needs no confirmation
  await expect(page.locator('#form')).toBeVisible()
  await expect(page.locator('#server-form')).toBeHidden()
  await expect(page.locator('#server-host')).toHaveText(new URL(mock.url).host)
  expect((await hook(a, 'config')).serverUrl).toBe(mock.url)
  expect(mock.find('POST', '/auth/device-login').length).toBe(logins) // no password went anywhere yet
  await login(page)
  await page.waitForURL(/__pocket\/capture\.html/)
})

test('restart keeps the device token; a revoked token (401) brings back the login screen', async () => {
  const ud = userDir('first-run')
  const meBefore = mock.find('GET', '/auth/me').length
  const sseBefore = mock.find('GET', '/events/stream').length
  const { app: a, page } = await launch(ud)
  await page.waitForURL(/__pocket\/capture\.html/) // no password asked again
  // main checks the token when the popup is shown (GET /auth/me) and the page loads its data
  await expect.poll(() => mock.find('GET', '/auth/me').length).toBeGreaterThan(meBefore)
  await expect(page.locator('#kpi-today')).not.toHaveText('—')
  // the live-update stream is up — otherwise its first connect could already meet the revocation
  // (401 → login screen shown on its own) and race with the Esc below
  await expect.poll(() => mock.find('GET', '/events/stream').length).toBeGreaterThan(sseBefore)
  await osKey(a, 'Escape')
  await expect.poll(async () => (await popup(a)).visible).toBe(false)
  // the device is revoked in the web settings; next time the popup is opened the page gets 401
  mock.revoked = true
  await hook(a, 'hotkey')
  await page.waitForURL(/__pocket\/login\.html/)
  expect((await popup(a)).visible).toBe(true)
  expect(fs.existsSync(path.join(ud, 'device-token.bin'))).toBe(false)
  await login(page) // logging in again un-revokes in the mock
  await page.waitForURL(/__pocket\/capture\.html/)
})

test('web build: popup loads /quick with the bridge; «Полная панель» opens without a second login', async () => {
  const { app: a, page } = await launch(userDir('web'), FIXTURE_WEB)
  await login(page)
  await page.waitForURL('app://pocket/quick')
  await expect(page.locator('#path')).toHaveText('/quick')
  await expect(page.locator('#kind')).toHaveText('popup')
  await expect(page.locator('#api')).toHaveText(`${mock.url}/api/v1`)
  await expect(page.locator('#me')).toHaveText('owner')

  const fullPromise = a.waitForEvent('window')
  await page.locator('#open-full').click()
  const full = await fullPromise
  await full.waitForURL(/app:\/\/pocket\/operations\?status=needs_account/)
  await expect(full.locator('#kind')).toHaveText('full')
  await expect(full.locator('#me')).toHaveText('owner')
  await expect.poll(async () => (await popup(a)).visible).toBe(false)
  const size = await a.evaluate(({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().includes('/operations'))!
    return w.getBounds()
  })
  expect(size.width).toBeGreaterThanOrEqual(900)
})

test('web /quick: Esc closes the ⋯ menu / clears the field first, only then hides', async () => {
  const { app: a, page } = await launch(userDir('web'), FIXTURE_WEB)
  await page.waitForURL('app://pocket/quick') // signed in by the previous test
  await expect.poll(async () => (await popup(a)).visible).toBe(true)
  await expect(page.locator('#me')).toHaveText('owner')

  // ⋯ open → the first Esc closes the menu (the page consumes it), the popup stays
  await page.locator('#menu-btn').click()
  await expect(page.locator('#menu')).toBeVisible()
  await osKey(a, 'Escape')
  await expect(page.locator('#menu')).toBeHidden()
  await expect.poll(() => hook(a, 'pendingEscapes')).toBe(0)
  expect((await popup(a)).visible).toBe(true)
  // nothing left to close and the page leaves Esc alone → main hides
  await osKey(a, 'Escape')
  await expect.poll(async () => (await popup(a)).visible).toBe(false)
  // shown again: the menu is not still open
  await hook(a, 'hotkey')
  await expect.poll(async () => (await popup(a)).visible).toBe(true)
  await expect(page.locator('#menu')).toBeHidden()

  // text in the field → cleared first; Esc in the empty field hides (the page calls window.hide())
  await page.locator('#input').fill('черновик 10к')
  await osKey(a, 'Escape')
  await expect(page.locator('#input')).toHaveValue('')
  await expect.poll(() => hook(a, 'pendingEscapes')).toBe(0)
  expect((await popup(a)).visible).toBe(true)
  await osKey(a, 'Escape')
  await expect.poll(async () => (await popup(a)).visible).toBe(false)
})

test('settings: hotkey, autostart, server address validation, logout', async () => {
  const { app: a, page } = await launch(userDir('first-run'))
  await page.waitForURL(/__pocket\/capture\.html/)
  const winPromise = a.waitForEvent('window')
  await hook(a, 'openSettings')
  const s = await winPromise
  await s.waitForURL(/__pocket\/settings\.html/)
  await expect(s.locator('#hotkey')).toHaveText('Ctrl + Alt + Space')
  await expect(s.locator('#autostart')).toBeChecked()
  await expect(s.locator('#server')).toHaveValue(mock.url)

  await s.locator('#server').fill('http://example.com')
  await s.locator('#server-save').click()
  await expect(s.locator('#server-msg')).toHaveText('Нужен адрес https://')

  // system shortcuts are refused while recording (Ctrl+V would swallow «paste» everywhere)
  await s.locator('#hotkey-change').click()
  await s.locator('#hotkey').press('Control+KeyV')
  await expect(s.locator('#hotkey-msg')).toHaveText(/системное сочетание/)
  await s.locator('#hotkey').press('Escape')
  await expect.poll(() => hook(a, 'hotkeyActive')).toBe('Ctrl+Alt+Space')

  // record a new hotkey
  await s.locator('#hotkey-change').click()
  await expect(s.locator('#hotkey')).toHaveText('Нажмите сочетание…')
  await s.locator('#hotkey').press('Control+Alt+KeyK')
  await expect(s.locator('#hotkey-msg')).toHaveText('Сохранено: Ctrl + Alt + K')
  expect(await hook(a, 'hotkeyActive')).toBe('Ctrl+Alt+K')
  await s.locator('#hotkey-reset').click()
  await expect(s.locator('#hotkey-msg')).toHaveText('Сохранено: Ctrl + Alt + Space')
  expect(await hook(a, 'hotkeyActive')).toBe('Ctrl+Alt+Space')

  // logout: revokes the device on the server and shows the login screen
  s.once('dialog', (d) => void d.accept())
  await s.locator('#logout').click()
  await page.waitForURL(/__pocket\/login\.html/)
  expect(mock.find('POST', '/auth/logout').length).toBeGreaterThan(0)
  await login(page)
  await page.waitForURL(/__pocket\/capture\.html/)
})

test('single instance: a second launch exits and shows the running popup', async () => {
  const ud = userDir('first-run')
  const { app: a, page } = await launch(ud)
  await page.waitForURL(/__pocket\/capture\.html/)
  await osKey(a, 'Escape')
  await expect.poll(async () => (await popup(a)).visible).toBe(false)
  const code = await new Promise<number | null>((resolve, reject) => {
    const p = spawn(electronPath as unknown as string, ['--no-sandbox', '--password-store=basic', APP_DIR], {
      env: { ...process.env, ELECTRON_DISABLE_SANDBOX: '1', POCKET_E2E: '1', POCKET_USER_DATA: ud, POCKET_SERVER_URL: mock.url, POCKET_RENDERER_DIR: emptyRenderer },
      stdio: 'ignore',
    })
    const t = setTimeout(() => {
      p.kill('SIGKILL')
      reject(new Error('second instance did not exit'))
    }, 20_000)
    p.on('exit', (c) => {
      clearTimeout(t)
      resolve(c)
    })
  })
  expect(code).toBe(0)
  await expect.poll(async () => (await popup(a)).visible).toBe(true)
})

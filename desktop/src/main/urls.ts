// URL policy (pure, unit-tested): which server URLs are allowed, what counts as an internal
// page, which links may be handed to the system browser, which routes the full panel accepts.
import { APP_HOST, APP_ORIGIN, APP_SCHEME } from '../shared/ipc'

const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]'])

export type ServerUrlResult = { ok: true; serverUrl: string; apiBaseUrl: string } | { ok: false; message: string }

/**
 * Normalize the server address typed in settings. Accepts "pocket.example.com", "https://pocket.example.com/",
 * "https://pocket.example.com/api/v1". The API must be HTTPS; plain http is allowed only for loopback
 * (dev server / tests). Returns the origin (+ optional base path) without a trailing slash.
 */
export function normalizeServerUrl(input: string): ServerUrlResult {
  if (typeof input !== 'string') return { ok: false, message: 'Укажите адрес сервера' }
  let s = input.trim()
  if (!s) return { ok: false, message: 'Укажите адрес сервера' }
  if (s.length > 300) return { ok: false, message: 'Слишком длинный адрес' }
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = `https://${s}`
  let u: URL
  try {
    u = new URL(s)
  } catch {
    return { ok: false, message: 'Неверный адрес сервера' }
  }
  if (u.username || u.password) return { ok: false, message: 'Адрес не должен содержать логин и пароль' }
  if (u.search || u.hash) return { ok: false, message: 'Адрес не должен содержать параметры' }
  const loopback = LOOPBACK.has(u.hostname)
  if (u.protocol !== 'https:' && !(u.protocol === 'http:' && loopback)) {
    return { ok: false, message: 'Нужен адрес https://' }
  }
  if (!u.hostname) return { ok: false, message: 'Неверный адрес сервера' }
  let base = u.pathname.replace(/\/+$/, '')
  if (base.endsWith('/api/v1')) base = base.slice(0, -'/api/v1'.length)
  const serverUrl = `${u.protocol}//${u.host}${base}`
  return { ok: true, serverUrl, apiBaseUrl: `${serverUrl}/api/v1` }
}

export function hostLabel(serverUrl: string): string {
  try {
    return new URL(serverUrl).host
  } catch {
    return serverUrl
  }
}

/** Pages of this app: app://pocket/... only. */
export function isInternalUrl(url: string): boolean {
  try {
    const u = new URL(url)
    return u.protocol === `${APP_SCHEME}:` && u.host === APP_HOST
  } catch {
    return false
  }
}

/** The only non-server links the app shows: https://t.me/<bot username> (no tail, no query). */
const TELEGRAM_HOST = 't.me'
const TELEGRAM_PATH = /^\/[A-Za-z0-9_]{3,64}\/?$/

/**
 * Link → system browser: http(s) only, no embedded credentials, and only to the configured Pocket
 * server (http only when that server is on loopback) or a plain t.me/<bot> link. Anything else —
 * including a page that got the device token and tries to carry it out in a URL — is dropped.
 * Returns the parsed `href` (what shell.openExternal gets, never the raw string) or null.
 */
export function allowedExternalUrl(url: unknown, serverUrl: string): string | null {
  if (typeof url !== 'string' || url.length > 2048) return null
  let u: URL
  try {
    u = new URL(url)
  } catch {
    return null
  }
  if (u.username || u.password || !u.hostname) return null
  if (u.protocol === 'https:' && u.host === TELEGRAM_HOST) {
    return TELEGRAM_PATH.test(u.pathname) && !u.search && !u.hash ? u.href : null
  }
  let server: URL | null = null
  try {
    server = serverUrl ? new URL(serverUrl) : null
  } catch {
    server = null
  }
  if (!server || u.host !== server.host || u.protocol !== server.protocol) return null
  if (u.protocol === 'https:' || (u.protocol === 'http:' && LOOPBACK.has(u.hostname))) return u.href
  return null
}

/**
 * Route for the full panel window (from tray / renderer). Must be an absolute in-app path like
 * "/analytics?period=this_month"; anything that could escape the app origin is rejected.
 */
export function sanitizeRoute(route: unknown): string | null {
  if (route === undefined || route === null || route === '') return '/'
  if (typeof route !== 'string' || route.length > 512) return null
  if (!route.startsWith('/') || route.startsWith('//') || route.includes('\\')) return null
  try {
    const u = new URL(route, `${APP_ORIGIN}/`)
    if (!isInternalUrl(u.href)) return null
    return `${u.pathname}${u.search}${u.hash}`
  } catch {
    return null
  }
}

export function appUrl(route: string): string {
  return `${APP_ORIGIN}${route.startsWith('/') ? route : `/${route}`}`
}

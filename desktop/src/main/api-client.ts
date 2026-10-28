// Calls the main process makes itself (no CORS, password never reaches renderer storage):
// device login, health check for the connection indicator, token check, device logout.
// fetch is injected: Electron net.fetch in the app (system proxy / Windows cert store), a stub in tests.
import type { LoginResult } from '../shared/ipc'
import { isValidDeviceToken } from './token-store'

export type FetchFn = (url: string, init?: RequestInit) => Promise<Response>

export interface DeviceInfo {
  name: string
  platform: 'windows' | 'macos' | 'linux'
}

export function platformName(p: string): DeviceInfo['platform'] {
  if (p === 'win32') return 'windows'
  if (p === 'darwin') return 'macos'
  return 'linux'
}

const LOGIN_TIMEOUT_MS = 15_000
const HEALTH_TIMEOUT_MS = 5_000

async function readError(res: Response): Promise<{ code?: string; message?: string }> {
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string } }
    return body?.error ?? {}
  } catch {
    return {}
  }
}

export class ApiClient {
  constructor(
    private readonly fetchFn: FetchFn,
    private readonly apiBaseUrl: () => string,
    private readonly version: string,
  ) {}

  private url(p: string): string {
    return `${this.apiBaseUrl()}${p}`
  }

  async deviceLogin(password: string, device: DeviceInfo): Promise<{ result: LoginResult; token?: string }> {
    if (typeof password !== 'string' || password.length === 0 || password.length > 1024) {
      return { result: { ok: false, code: 'bad_request', message: 'Введите пароль' } }
    }
    // fresh install without a server address: never send the password anywhere
    if (!this.apiBaseUrl()) return { result: { ok: false, code: 'bad_request', message: 'Укажите адрес сервера' } }
    let res: Response
    try {
      res = await this.fetchFn(this.url('/auth/device-login'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'User-Agent': `PocketDesktop/${this.version}` },
        body: JSON.stringify({ username: 'owner', password, device_name: device.name.slice(0, 100), platform: device.platform }),
        signal: AbortSignal.timeout(LOGIN_TIMEOUT_MS),
      })
    } catch {
      return { result: { ok: false, code: 'network', message: 'Нет связи с сервером' } }
    }
    if (res.status === 201 || res.status === 200) {
      let body: { token?: unknown } = {}
      try {
        body = (await res.json()) as { token?: unknown }
      } catch {
        /* handled below */
      }
      if (isValidDeviceToken(body.token)) return { result: { ok: true }, token: body.token }
      return { result: { ok: false, code: 'server', message: 'Сервер вернул неожиданный ответ' } }
    }
    const err = await readError(res)
    if (res.status === 401) return { result: { ok: false, code: 'wrong_password', message: err.message || 'Неверный пароль' } }
    if (res.status === 429) {
      const ra = Number(res.headers.get('Retry-After'))
      const wait = Number.isFinite(ra) && ra > 0 ? ` Подождите ${Math.ceil(ra)} с.` : ''
      return { result: { ok: false, code: 'rate_limited', message: `Слишком много попыток.${wait}` } }
    }
    if (res.status === 422 || res.status === 400) {
      return { result: { ok: false, code: 'bad_request', message: err.message || 'Проверьте пароль' } }
    }
    return { result: { ok: false, code: 'server', message: err.message || `Ошибка сервера (${res.status})` } }
  }

  /** GET /health → true when the server answers 200 with status ok|degraded. */
  async health(): Promise<boolean> {
    if (!this.apiBaseUrl()) return false
    try {
      const res = await this.fetchFn(this.url('/health'), {
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
      })
      if (!res.ok) return false
      const body = (await res.json().catch(() => ({}))) as { status?: string }
      return body.status === 'ok' || body.status === 'degraded'
    } catch {
      return false
    }
  }

  /** GET /auth/me with the device token: 'ok' | 'revoked' (401) | 'unknown' (network/5xx). */
  async checkToken(token: string): Promise<'ok' | 'revoked' | 'unknown'> {
    if (!this.apiBaseUrl()) return 'unknown'
    try {
      const res = await this.fetchFn(this.url('/auth/me'), {
        headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
        signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
      })
      if (res.ok) return 'ok'
      if (res.status === 401) return 'revoked'
      return 'unknown'
    } catch {
      return 'unknown'
    }
  }

  /** POST /auth/logout with the device token revokes it on the server (best effort). */
  async logout(token: string): Promise<boolean> {
    if (!this.apiBaseUrl()) return false
    try {
      const res = await this.fetchFn(this.url('/auth/logout'), {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: '{}',
        signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
      })
      return res.ok
    } catch {
      return false
    }
  }
}

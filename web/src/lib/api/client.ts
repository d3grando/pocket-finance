// Fetch wrapper. Web: cookie session + X-CSRF-Token.
// Desktop: Bearer device token from the preload bridge (window.pocket.auth.getToken()).
// Public API (the pages reads): ApiError, NetworkError, configureApi, apiBaseUrl, newIdempotencyKey,
// authHeaders, request, api.
import type { ApiErrorBody } from './types'

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
    public requestId: string | null = null,
    public retryAfter: number | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** Thrown when the request never reached the server (offline / DNS / CORS / timeout). UI: «Нет связи». */
export class NetworkError extends Error {
  constructor(message = 'Нет связи с сервером', public timeout = false) {
    super(message)
    this.name = 'NetworkError'
  }
}

export const isApiError = (e: unknown, code?: string): e is ApiError =>
  e instanceof ApiError && (code === undefined || e.code === code)
export const isNetworkError = (e: unknown): e is NetworkError => e instanceof NetworkError
export const isUnauthorized = (e: unknown) => e instanceof ApiError && e.status === 401

/** Human text for any error (server messages are Russian and shown as is — ARCH §10). */
export function errorText(e: unknown): string {
  if (e instanceof NetworkError) return 'Нет связи с сервером'
  if (e instanceof ApiError) {
    if (e.status === 429) return e.retryAfter ? `Слишком много попыток. Подождите ${Math.ceil(e.retryAfter)} с` : 'Слишком много попыток. Подождите 1 мин'
    if (e.status >= 500 && e.code === 'internal') return 'Не удалось сохранить. Ничего не записано'
    return e.message || 'Ошибка сервера'
  }
  return 'Что-то пошло не так'
}

type TokenProvider = () => Promise<string | null> | string | null

const config: { baseUrl: string; getToken: TokenProvider | null; csrf: string | null; timeoutMs: number } = {
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? '/api/v1',
  getToken: null,
  csrf: null,
  timeoutMs: 30_000,
}

type ApiListener = (e: { type: 'unauthorized'; path: string } | { type: 'reachable' } | { type: 'unreachable' }) => void
const listeners = new Set<ApiListener>()

/** Subscribe to client-level events (401 → auth gate, reachability → connection indicator). */
export function onApiEvent(fn: ApiListener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
const emit: ApiListener = (e) => listeners.forEach((l) => { try { l(e) } catch { /* listener errors never break requests */ } })

export function configureApi(opts: { baseUrl?: string; getToken?: TokenProvider | null; timeoutMs?: number }) {
  if (opts.baseUrl !== undefined) config.baseUrl = opts.baseUrl.replace(/\/+$/, '')
  if (opts.getToken !== undefined) config.getToken = opts.getToken
  if (opts.timeoutMs !== undefined) config.timeoutMs = opts.timeoutMs
}

/** Remember MeOut.csrf_token (fallback when the pf_csrf cookie is not readable). */
export function setCsrfToken(token: string | null) {
  config.csrf = token
}

export const apiBaseUrl = () => config.baseUrl
export const usesBearer = () => config.getToken !== null

/** UUID v4. crypto.randomUUID needs a secure context — fall back to getRandomValues (http stand). */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') {
    try { return c.randomUUID() } catch { /* insecure context */ }
  }
  const b = new Uint8Array(16)
  if (c?.getRandomValues) c.getRandomValues(b)
  else for (let i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256)
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return m ? decodeURIComponent(m[1]) : null
}

export interface RequestOptions {
  query?: Record<string, string | number | boolean | (string | number)[] | null | undefined>
  idempotencyKey?: string
  signal?: AbortSignal
  headers?: Record<string, string>
  /** ms; 0 = no timeout */
  timeoutMs?: number
}

export async function authHeaders(method: string): Promise<Record<string, string>> {
  const h: Record<string, string> = {}
  let token: string | null = null
  if (config.getToken) {
    try { token = await config.getToken() } catch { token = null }
  }
  if (token) h.Authorization = `Bearer ${token}`
  else if (method !== 'GET' && method !== 'HEAD') {
    const csrf = readCookie('pf_csrf') ?? config.csrf
    if (csrf) h['X-CSRF-Token'] = csrf
  }
  return h
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v)) v.forEach((x) => qs.append(k, String(x)))
    else qs.append(k, String(v))
  }
  const s = qs.toString()
  return `${config.baseUrl}${path}${s ? `?${s}` : ''}`
}

/** App-level 503 codes (the API itself answered); anything else on 502/503/504 is the proxy. */
const APP_503 = new Set(['unavailable', 'ai_unavailable', 'rates_unavailable'])

/** 502/504, or 503 that did not come from the API (Caddy `service_unavailable`, an HTML page). */
export function isGatewayDown(status: number, code?: string | null): boolean {
  if (status === 502 || status === 504) return true
  return status === 503 && !(code && APP_503.has(code))
}

export async function request<T>(method: string, path: string, body?: unknown, opts: RequestOptions = {}): Promise<T> {
  const auth = await authHeaders(method)
  const headers: Record<string, string> = { Accept: 'application/json', ...auth, ...opts.headers }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey
  const timeoutMs = opts.timeoutMs ?? config.timeoutMs
  const ctrl = new AbortController()
  let timedOut = false
  const timer = timeoutMs > 0 ? setTimeout(() => { timedOut = true; ctrl.abort() }, timeoutMs) : null
  const onAbort = () => ctrl.abort()
  opts.signal?.addEventListener('abort', onAbort)
  let res: Response
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: auth.Authorization ? 'omit' : 'include',
      signal: ctrl.signal,
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError' && !timedOut) throw e
    emit({ type: 'unreachable' })
    throw new NetworkError(timedOut ? 'Сервер не ответил вовремя' : 'Нет связи с сервером', timedOut)
  } finally {
    if (timer) clearTimeout(timer)
    opts.signal?.removeEventListener('abort', onAbort)
  }
  if (res.status === 204) { emit({ type: 'reachable' }); return undefined as T }
  const data = await res.json().catch(() => null)
  // a proxy answering for a stopped API (502/504, 503 without an app error code) is not «на связи»
  emit({ type: isGatewayDown(res.status, (data as ApiErrorBody | null)?.error?.code) ? 'unreachable' : 'reachable' })
  if (!res.ok) {
    const err = (data as ApiErrorBody | null)?.error
    const raDetail = (err?.details as { retry_after?: unknown } | undefined)?.retry_after
    const ra = res.headers.get('Retry-After') ?? (raDetail != null ? String(raDetail) : null)
    const apiErr = new ApiError(
      res.status,
      err?.code ?? (res.status === 401 ? 'unauthorized' : 'internal'),
      err?.message ?? `HTTP ${res.status}`,
      err?.details ?? {},
      err?.request_id ?? res.headers.get('X-Request-Id'),
      ra ? Number(ra) || null : null,
    )
    // 401 wrong_password (change password / wipe) is a form error, not a lost session.
    if (res.status === 401 && apiErr.code !== 'wrong_password') emit({ type: 'unauthorized', path })
    throw apiErr
  }
  return data as T
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => request<T>('GET', path, undefined, opts),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>('POST', path, body ?? {}, opts),
  put: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>('PUT', path, body ?? {}, opts),
  patch: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>('PATCH', path, body ?? {}, opts),
  delete: <T>(path: string, opts?: RequestOptions) => request<T>('DELETE', path, undefined, opts),
  url: buildUrl,
}

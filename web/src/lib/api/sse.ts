// SSE over fetch (needs Bearer for Electron). Contract: docs/API.md §12, ARCH §8.
// - `hello` / `ping` → heartbeat; `change` → invalidate the query keys of the changed entities;
// - reconnect with backoff 1→30 s; after a reconnect → invalidate everything;
// - no event for > 30 s → treat as dropped and reconnect;
// - while SSE is down, GET /health every 10 s keeps the connection indicator honest; a healthy
//   answer reconnects the stream at once (no waiting for the 30 s backoff);
// - a proxy answer (502/503/504 without an app error code) counts as «no connection», not «на связи».
import type { QueryClient } from '@tanstack/react-query'
import { checkHealth, connection } from '../connection'
import { apiBaseUrl, authHeaders, isGatewayDown, usesBearer } from './client'
import { entityKeys } from './keys'
import type { ChangeEvent, Entity } from './types'

export type ConnectionState = 'online' | 'reconnecting' | 'offline'

export interface SseMessage { event: string; data: string; id?: string }

/** Incremental text/event-stream parser (WHATWG rules: \n, \r\n, \r; multi-line data; comments). */
export function createSseParser(onMessage: (m: SseMessage) => void) {
  let buf = ''
  let event = ''
  let data: string[] = []
  let id: string | undefined
  const dispatch = () => {
    if (data.length) onMessage({ event: event || 'message', data: data.join('\n'), id })
    event = ''
    data = []
  }
  return (chunk: string) => {
    buf += chunk
    const lines = buf.split(/\r\n|\r|\n/)
    buf = lines.pop() ?? ''
    for (const line of lines) {
      if (line === '') { dispatch(); continue }
      if (line.startsWith(':')) continue
      const i = line.indexOf(':')
      const field = i < 0 ? line : line.slice(0, i)
      let value = i < 0 ? '' : line.slice(i + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (field === 'event') event = value
      else if (field === 'data') data.push(value)
      else if (field === 'id') id = value
    }
  }
}

/** Query-key prefixes to invalidate for a change event (deduplicated). */
export function keysForEntities(entities: Entity[]): (readonly unknown[])[] {
  const seen = new Set<string>()
  const out: (readonly unknown[])[] = []
  for (const e of entities) {
    for (const k of entityKeys[e] ?? []) {
      const s = JSON.stringify(k)
      if (!seen.has(s)) { seen.add(s); out.push(k) }
    }
  }
  return out
}

type ChangeListener = (entities: Entity[]) => void
const changeListeners = new Set<ChangeListener>()

/** Subscribe to server-side changes (SSE `change`, or everything after a reconnect). */
export function onServerChange(fn: ChangeListener): () => void {
  changeListeners.add(fn)
  return () => { changeListeners.delete(fn) }
}
const emitChange = (ents: Entity[]) => changeListeners.forEach((f) => { try { f(ents) } catch { /* ignore */ } })

export function nextBackoff(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** Math.max(0, attempt))
}

const HEARTBEAT_TIMEOUT = 35_000
const HEALTH_EVERY = 10_000

/** Subscribe to /events/stream; invalidate queries on `change`; returns an unsubscribe fn. */
export function startEventStream(qc: QueryClient, onState: (s: ConnectionState) => void = () => {}): () => void {
  let stopped = false
  let attempt = 0
  let everConnected = false
  let ctrl: AbortController | null = null
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let watchdog: ReturnType<typeof setTimeout> | null = null
  let healthTimer: ReturnType<typeof setInterval> | null = null

  const setState = (s: ConnectionState) => { try { onState(s) } catch { /* ignore */ } }

  const beat = () => {
    connection.markOk()
    if (watchdog) clearTimeout(watchdog)
    watchdog = setTimeout(() => ctrl?.abort(), HEARTBEAT_TIMEOUT)
  }

  const startHealth = () => {
    if (healthTimer) return
    healthTimer = setInterval(() => {
      void checkHealth().then((ok) => {
        // the API is back: reconnect the stream now instead of waiting for the backoff
        if (ok && !stopped && retryTimer && !connection.getSnapshot().sseConnected) {
          clearTimeout(retryTimer)
          retryTimer = null
          attempt = 0
          void connect()
        }
      })
    }, HEALTH_EVERY)
  }
  const stopHealth = () => { if (healthTimer) { clearInterval(healthTimer); healthTimer = null } }

  const schedule = (delay?: number) => {
    if (stopped) return
    const d = delay ?? nextBackoff(attempt++)
    retryTimer = setTimeout(() => { retryTimer = null; void connect() }, d)
  }

  async function connect() {
    if (stopped) return
    if (typeof navigator !== 'undefined' && !navigator.onLine) { setState('offline'); schedule(); return }
    ctrl = new AbortController()
    let res: Response
    try {
      const auth = await authHeaders('GET')
      res = await fetch(`${apiBaseUrl()}/events/stream`, {
        headers: { Accept: 'text/event-stream', ...auth },
        credentials: usesBearer() ? 'omit' : 'include',
        cache: 'no-store',
        signal: ctrl.signal,
      })
    } catch {
      if (stopped) return
      connection.setSse(false)
      setState('reconnecting')
      startHealth()
      schedule()
      return
    }
    if (!res.ok || !res.body) {
      const code = res.ok ? null : await res.json().then((d: { error?: { code?: string } } | null) => d?.error?.code ?? null).catch(() => null)
      // the API answered → reachable; a proxy 502/503/504 for a stopped API → not reachable
      if (isGatewayDown(res.status, code)) connection.markFail()
      else connection.markOk()
      // 404/501: SSE not deployed on this server → poll on focus only, retry rarely
      const unsupported = res.status === 404 || res.status === 501
      connection.setSse(false, unsupported)
      setState(unsupported ? 'online' : 'reconnecting')
      startHealth()
      schedule(unsupported || res.status === 401 ? 30_000 : undefined)
      return
    }
    const wasReconnect = everConnected
    everConnected = true
    attempt = 0
    connection.setSse(true)
    stopHealth()
    setState('online')
    beat()
    // cancelRefetch:false — join fetches already running after the reconnect instead of restarting them
    if (wasReconnect) { void qc.invalidateQueries(undefined, { cancelRefetch: false }); emitChange(Object.keys(entityKeys) as Entity[]) }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    const parse = createSseParser((m) => {
      beat()
      if (m.event === 'change') {
        let ev: ChangeEvent | null
        try { ev = JSON.parse(m.data) as ChangeEvent } catch { ev = null }
        const ents = ev?.entities?.length ? ev.entities : (Object.keys(entityKeys) as Entity[])
        for (const key of keysForEntities(ents)) void qc.invalidateQueries({ queryKey: key })
        emitChange(ents)
      }
    })
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        parse(decoder.decode(value, { stream: true }))
      }
    } catch {
      /* aborted by watchdog or network drop */
    }
    if (watchdog) clearTimeout(watchdog)
    if (stopped) return
    connection.setSse(false)
    setState('reconnecting')
    startHealth()
    schedule()
  }

  const onOnline = () => {
    if (retryTimer) clearTimeout(retryTimer)
    attempt = 0
    if (!connection.getSnapshot().sseConnected) schedule(0)
  }
  const onOffline = () => {
    ctrl?.abort()
    setState('offline')
  }
  // after a real offline period everything on screen may be stale → refetch all
  const offRecovered = connection.onRecovered(({ wasOffline }) => { if (wasOffline) void qc.invalidateQueries(undefined, { cancelRefetch: false }) })
  window.addEventListener('online', onOnline)
  window.addEventListener('offline', onOffline)
  connection.setSseExpected(true)
  void connect()

  return () => {
    stopped = true
    ctrl?.abort()
    if (retryTimer) clearTimeout(retryTimer)
    if (watchdog) clearTimeout(watchdog)
    stopHealth()
    offRecovered()
    connection.setSse(false)
    connection.setSseExpected(false)
    window.removeEventListener('online', onOnline)
    window.removeEventListener('offline', onOffline)
  }
}

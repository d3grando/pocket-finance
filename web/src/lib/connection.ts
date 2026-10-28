// Connection status store (DESIGN §8.4): navigator.onLine AND a successful
// SSE heartbeat / API response / GET /health within the last 30 s.
//   online       — fresh signal and SSE connected (or SSE not expected: no session yet / not deployed)
//   reconnecting — SSE dropped but the server answered < 30 s ago
//   offline      — browser offline or nothing from the server for > 30 s
// «Recovered» = the first server answer after a failure or an offline period (not only a direct
// offline → online jump): network cards are re-sent then and, after a real offline period, the
// «Снова на связи» banner shows and the data is refetched.
import { useSyncExternalStore } from 'react'
import { onApiEvent } from './api/client'
import { healthApi } from './api/endpoints'

export type ConnectionStatus = 'online' | 'reconnecting' | 'offline'

export interface ConnectionSnapshot {
  status: ConnectionStatus
  browserOnline: boolean
  sseConnected: boolean
  /** server proved reachable at (ms epoch) — «данные на 14:32» */
  lastOkAt: number | null
  /** set for 2 s after an offline period ended («Снова на связи») */
  justRecovered: boolean
}

export const FRESH_MS = 30_000

export interface ConnectionInputs {
  browserOnline: boolean
  sseConnected: boolean
  sseUnsupported: boolean
  /** an SSE subscription is supposed to run (a session exists); default true */
  sseExpected?: boolean
  lastOkAt: number | null
  lastFailAt: number | null
  now: number
}

/** Pure status derivation (unit-tested). */
export function deriveStatus(i: ConnectionInputs): ConnectionStatus {
  if (!i.browserOnline) return 'offline'
  const fresh = i.lastOkAt !== null && i.now - i.lastOkAt <= FRESH_MS
  const failedAfterOk = i.lastFailAt !== null && (i.lastOkAt === null || i.lastFailAt > i.lastOkAt)
  const sseOk = i.sseConnected || i.sseUnsupported || i.sseExpected === false
  if (fresh && !failedAfterOk) return sseOk ? 'online' : 'reconnecting'
  if (fresh && failedAfterOk) return 'reconnecting'
  return 'offline'
}

export interface RecoveryInfo {
  /** the status had reached «offline» (banner «Снова на связи», full refetch) */
  wasOffline: boolean
}

type Listener = () => void

export class ConnectionStore {
  private inputs: Omit<ConnectionInputs, 'now'> = {
    browserOnline: typeof navigator === 'undefined' ? true : navigator.onLine,
    sseConnected: false,
    sseUnsupported: false,
    sseExpected: false,
    lastOkAt: Date.now(), // grace period on boot: no «нет связи» flash before the first answer
    lastFailAt: null,
  }
  private snap: ConnectionSnapshot
  private listeners = new Set<Listener>()
  private recoverTimer: ReturnType<typeof setTimeout> | null = null
  private recovered = false
  /** a failed request / offline browser since the last server answer */
  private troubled = false
  /** the status reached «offline» since the last server answer */
  private wasOffline = false
  private onRecover = new Set<(info: RecoveryInfo) => void>()

  constructor() {
    this.snap = this.compute()
    if (this.snap.status === 'offline') { this.troubled = true; this.wasOffline = true }
  }

  private compute(): ConnectionSnapshot {
    const status = deriveStatus({ ...this.inputs, now: Date.now() })
    return {
      status,
      browserOnline: this.inputs.browserOnline,
      sseConnected: this.inputs.sseConnected,
      lastOkAt: this.inputs.lastOkAt,
      justRecovered: this.recovered,
    }
  }

  private update(patch: Partial<Omit<ConnectionInputs, 'now'>>) {
    Object.assign(this.inputs, patch)
    this.refresh()
  }

  /** Recompute (time-based) and notify on change. */
  refresh() {
    const prev = this.snap
    const next = this.compute()
    if (next.status === 'offline') { this.wasOffline = true; this.troubled = true }
    if (
      next.status !== prev.status || next.sseConnected !== prev.sseConnected || next.lastOkAt !== prev.lastOkAt ||
      next.browserOnline !== prev.browserOnline || next.justRecovered !== prev.justRecovered
    ) {
      this.snap = next
      this.listeners.forEach((l) => l())
    }
  }

  /** The server answered (any API response, SSE event, /health). */
  markOk() {
    const recovering = this.troubled && this.inputs.browserOnline
    const info: RecoveryInfo = { wasOffline: this.wasOffline }
    if (recovering) {
      this.troubled = false
      this.wasOffline = false
      if (info.wasOffline) {
        this.recovered = true
        if (this.recoverTimer) clearTimeout(this.recoverTimer)
        this.recoverTimer = setTimeout(() => { this.recovered = false; this.refresh() }, 2000)
      }
    }
    this.update({ lastOkAt: Date.now(), lastFailAt: null })
    if (recovering) this.onRecover.forEach((f) => { try { f(info) } catch { /* listener errors never break the store */ } })
  }

  markFail() {
    this.troubled = true
    this.update({ lastFailAt: Date.now() })
  }

  setSse(connected: boolean, unsupported = false) { this.update({ sseConnected: connected, sseUnsupported: unsupported }) }
  /** an SSE subscription runs only with a session; before login the status rests on /health */
  setSseExpected(v: boolean) { this.update({ sseExpected: v }) }
  sseExpected() { return this.inputs.sseExpected !== false }

  setBrowserOnline(v: boolean) {
    if (!v) this.troubled = true
    this.update({ browserOnline: v })
  }

  /** Called on the first server answer after a failure / offline period (re-send, refetch). */
  onRecovered(fn: (info: RecoveryInfo) => void): () => void {
    this.onRecover.add(fn)
    return () => this.onRecover.delete(fn)
  }

  subscribe = (l: Listener) => {
    this.listeners.add(l)
    return () => this.listeners.delete(l)
  }
  getSnapshot = () => this.snap
}

export const connection = new ConnectionStore()

export function useConnection(): ConnectionSnapshot {
  return useSyncExternalStore(connection.subscribe, connection.getSnapshot, connection.getSnapshot)
}

let monitorStarted = false

/** GET /health: ok only for a 200 with status ok|degraded (a proxy 503 page is not «на связи»). */
export async function checkHealth(): Promise<boolean> {
  try {
    const h = await healthApi.get()
    const ok = h?.status === 'ok' || h?.status === 'degraded'
    if (ok) connection.markOk()
    else connection.markFail()
    return ok
  } catch {
    connection.markFail()
    return false
  }
}

const HEALTH_IDLE_MS = 10_000

/** Wire browser online/offline + every API response into the store (call once at boot). */
export function startConnectionMonitor(): () => void {
  if (monitorStarted || typeof window === 'undefined') return () => {}
  monitorStarted = true
  const on = () => { connection.setBrowserOnline(true); if (!connection.sseExpected()) void checkHealth() }
  const off = () => connection.setBrowserOnline(false)
  window.addEventListener('online', on)
  window.addEventListener('offline', off)
  const offApi = onApiEvent((e) => {
    if (e.type === 'reachable') connection.markOk()
    else if (e.type === 'unreachable') connection.markFail()
  })
  const tick = setInterval(() => connection.refresh(), 5_000)
  // no session → no SSE heartbeat: keep the indicator honest with /health (login, Quick Capture login)
  const idle = setInterval(() => {
    if (!connection.sseExpected() && navigator.onLine && document.visibilityState !== 'hidden') void checkHealth()
  }, HEALTH_IDLE_MS)
  return () => {
    monitorStarted = false
    window.removeEventListener('online', on)
    window.removeEventListener('offline', off)
    offApi()
    clearInterval(tick)
    clearInterval(idle)
  }
}

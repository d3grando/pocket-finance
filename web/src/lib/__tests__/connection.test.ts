import { describe, expect, it } from 'vitest'
import { ConnectionStore, deriveStatus, FRESH_MS, type RecoveryInfo } from '../connection'

const base = { browserOnline: true, sseConnected: true, sseUnsupported: false, lastOkAt: 1_000_000, lastFailAt: null, now: 1_000_000 }

describe('deriveStatus (DESIGN §8.4)', () => {
  it('online with fresh SSE heartbeat', () => {
    expect(deriveStatus(base)).toBe('online')
  })
  it('browser offline wins', () => {
    expect(deriveStatus({ ...base, browserOnline: false })).toBe('offline')
  })
  it('SSE dropped but server answered < 30 s ago → reconnecting', () => {
    expect(deriveStatus({ ...base, sseConnected: false, now: base.now + 10_000 })).toBe('reconnecting')
  })
  it('nothing from the server for > 30 s → offline', () => {
    expect(deriveStatus({ ...base, sseConnected: false, now: base.now + FRESH_MS + 1 })).toBe('offline')
    expect(deriveStatus({ ...base, now: base.now + FRESH_MS + 1 })).toBe('offline')
  })
  it('a failure after the last success → reconnecting until stale', () => {
    expect(deriveStatus({ ...base, lastFailAt: base.now + 1, now: base.now + 2 })).toBe('reconnecting')
  })
  it('server without SSE (404/501) still counts as online', () => {
    expect(deriveStatus({ ...base, sseConnected: false, sseUnsupported: true })).toBe('online')
  })
})

describe('deriveStatus without an SSE session (login screen, Quick Capture login)', () => {
  it('no SSE expected → a fresh /health answer is «online», not a pulsing «reconnecting»', () => {
    expect(deriveStatus({ ...base, sseConnected: false, sseExpected: false })).toBe('online')
    expect(deriveStatus({ ...base, sseConnected: false, sseExpected: true })).toBe('reconnecting')
  })
})

describe('recovery (auto-retry of «нет связи» cards, «Снова на связи»)', () => {
  it('fires on the first server answer after offline, also via «reconnecting» (no direct offline → online)', () => {
    const s = new ConnectionStore()
    const got: RecoveryInfo[] = []
    s.onRecovered((i) => got.push(i))
    s.setSseExpected(true)
    s.setSse(true)
    s.markOk()
    expect(got).toHaveLength(0)
    s.setBrowserOnline(false)
    expect(s.getSnapshot().status).toBe('offline')
    s.setSse(false)
    s.setBrowserOnline(true)
    s.markFail() // the send that failed while offline
    expect(s.getSnapshot().status).toBe('reconnecting')
    expect(got).toHaveLength(0)
    s.markOk() // REST answers before SSE is back
    expect(got).toEqual([{ wasOffline: true }])
    expect(s.getSnapshot().justRecovered).toBe(true)
    s.markOk()
    expect(got).toHaveLength(1) // once per recovery
  })

  it('a failure without reaching «offline» (lost answer) also recovers on the next answer, without the banner', () => {
    const s = new ConnectionStore()
    const got: RecoveryInfo[] = []
    s.onRecovered((i) => got.push(i))
    s.markOk()
    s.markFail()
    s.markOk()
    expect(got).toEqual([{ wasOffline: false }])
    expect(s.getSnapshot().justRecovered).toBe(false)
  })
})

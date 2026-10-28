import { describe, expect, it } from 'vitest'
import { createSseParser, keysForEntities, nextBackoff, type SseMessage } from '../api/sse'

describe('SSE parser', () => {
  it('parses events split across chunks, CRLF, comments and multi-line data', () => {
    const got: SseMessage[] = []
    const push = createSseParser((m) => got.push(m))
    push('event: hello\ndata: {"server_time":"x"')
    push(',"version":"0.1.0"}\n\n: comment\r\n')
    push('event: change\r\ndata: {"entities":["transactions"],\r\ndata: "action_id":null}\r\n\r\n')
    push('event: ping\ndata: {}\n\n')
    expect(got.map((m) => m.event)).toEqual(['hello', 'change', 'ping'])
    expect(JSON.parse(got[0].data)).toEqual({ server_time: 'x', version: '0.1.0' })
    expect(got[1].data).toBe('{"entities":["transactions"],\n"action_id":null}')
  })
  it('ignores events without data', () => {
    const got: SseMessage[] = []
    const push = createSseParser((m) => got.push(m))
    push('event: x\n\n')
    expect(got).toEqual([])
  })
})

describe('entity → query keys', () => {
  it('transactions invalidate lists, stats, balances, needs-account', () => {
    const keys = keysForEntities(['transactions', 'stats']).map((k) => k[0])
    expect(keys).toEqual(expect.arrayContaining(['transactions', 'transaction', 'needs-account', 'stats', 'balances', 'accounts']))
    expect(new Set(keys).size).toBe(keys.length) // deduplicated
  })
})

describe('reconnect backoff', () => {
  it('1 → 30 s', () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(nextBackoff)).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000])
  })
})

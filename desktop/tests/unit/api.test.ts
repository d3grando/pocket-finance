import { describe, expect, it, vi } from 'vitest'
import { ApiClient, platformName, type FetchFn } from '../../src/main/api-client'
import { ConnectionMonitor } from '../../src/main/connection'
import { formatCompact, formatMoney, formatWhen } from '../../src/shared/money'

const TOKEN = 'pfd_' + 'x'.repeat(43)
const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })

function client(fetchFn: FetchFn) {
  return new ApiClient(fetchFn, () => 'https://pocket.example.com/api/v1', '0.1.0')
}

describe('ApiClient without a server address (fresh install)', () => {
  it('never sends the password or probes anything', async () => {
    const f = vi.fn<FetchFn>(async () => json(201, { token: TOKEN, device_id: 'u' }))
    const c = new ApiClient(f, () => '', '0.1.0')
    expect((await c.deviceLogin('secret', { name: 'DESKTOP-1', platform: 'windows' })).result).toEqual({ ok: false, code: 'bad_request', message: 'Укажите адрес сервера' })
    expect(await c.health()).toBe(false)
    expect(await c.checkToken(TOKEN)).toBe('unknown')
    expect(await c.logout(TOKEN)).toBe(false)
    expect(f).not.toHaveBeenCalled()
  })
})

describe('ApiClient.deviceLogin', () => {
  it('posts DeviceLoginIn and returns the token', async () => {
    const f = vi.fn<FetchFn>(async () => json(201, { token: TOKEN, device_id: 'u' }))
    const r = await client(f).deviceLogin('secret', { name: 'DESKTOP-1', platform: 'windows' })
    expect(r).toEqual({ result: { ok: true }, token: TOKEN })
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('https://pocket.example.com/api/v1/auth/device-login')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ username: 'owner', password: 'secret', device_name: 'DESKTOP-1', platform: 'windows' })
  })

  it('maps errors to Russian messages', async () => {
    const wrong = await client(async () => json(401, { error: { code: 'unauthorized', message: 'Неверный пароль' } })).deviceLogin('x', { name: 'a', platform: 'windows' })
    expect(wrong.result).toEqual({ ok: false, code: 'wrong_password', message: 'Неверный пароль' })
    const limited = await client(async () => json(429, {}, { 'Retry-After': '42' })).deviceLogin('x', { name: 'a', platform: 'windows' })
    expect(limited.result).toMatchObject({ ok: false, code: 'rate_limited' })
    expect(limited.result.ok === false && limited.result.message).toContain('42')
    const net = await client(async () => {
      throw new TypeError('fetch failed')
    }).deviceLogin('x', { name: 'a', platform: 'windows' })
    expect(net.result).toEqual({ ok: false, code: 'network', message: 'Нет связи с сервером' })
    const bad = await client(async () => json(201, { token: 'nope' })).deviceLogin('x', { name: 'a', platform: 'windows' })
    expect(bad.result).toMatchObject({ ok: false, code: 'server' })
    const empty = await client(async () => json(201, {})).deviceLogin('', { name: 'a', platform: 'windows' })
    expect(empty.result).toMatchObject({ ok: false, code: 'bad_request' })
  })

  it('health, token check, logout', async () => {
    expect(await client(async () => json(200, { status: 'ok' })).health()).toBe(true)
    expect(await client(async () => json(200, { status: 'degraded' })).health()).toBe(true)
    expect(await client(async () => json(503, {})).health()).toBe(false)
    expect(
      await client(async () => {
        throw new Error('offline')
      }).health(),
    ).toBe(false)
    expect(await client(async () => json(401, {})).checkToken(TOKEN)).toBe('revoked')
    expect(await client(async () => json(200, {})).checkToken(TOKEN)).toBe('ok')
    expect(await client(async () => json(502, {})).checkToken(TOKEN)).toBe('unknown')
    const f = vi.fn<FetchFn>(async () => json(200, { ok: true }))
    expect(await client(f).logout(TOKEN)).toBe(true)
    expect((f.mock.calls[0][1]?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`)
  })

  it('platform names follow DeviceLoginIn', () => {
    expect(platformName('win32')).toBe('windows')
    expect(platformName('darwin')).toBe('macos')
    expect(platformName('linux')).toBe('linux')
  })
})

describe('ConnectionMonitor', () => {
  it('reports online/offline transitions once', async () => {
    let ok = true
    const states: string[] = []
    const m = new ConnectionMonitor(async () => ok, (s) => states.push(s))
    await m.check()
    await m.check()
    ok = false
    await m.check()
    expect(states).toEqual(['online', 'offline'])
    expect(m.state).toBe('offline')
  })
})

describe('money format (DESIGN §3.2)', () => {
  const N = ' '
  it('matches the reference examples', () => {
    expect(formatMoney('1250000', 'VND')).toBe(`1${N}250${N}000${N}₫`)
    expect(formatMoney('-420000', 'VND', { sign: 'always' })).toBe(`−420${N}000${N}₫`)
    expect(formatMoney('300', 'USDT', { sign: 'always' })).toBe(`+300${N}USDT`)
    expect(formatMoney('12.4', 'USD', { approx: true })).toBe(`≈${N}$12.40`)
    expect(formatMoney('-180', 'USD')).toBe('−$180.00')
    expect(formatMoney('3', 'TON')).toBe(`3${N}TON`)
    expect(formatMoney('0.02150000', 'BTC')).toBe(`0.0215${N}BTC`)
    expect(formatMoney('4990.5', 'RUB')).toBe(`4${N}990.50${N}₽`)
    expect(formatCompact('1250000', 'VND')).toBe(`1.25м${N}₫`)
    expect(formatCompact('450000', 'VND')).toBe(`450к${N}₫`)
    expect(formatCompact('-12450000', 'VND')).toBe(`−12.45м${N}₫`)
  })
  it('no float drift, half-even rounding, null → dash', () => {
    expect(formatMoney('150000.00000000', 'VND')).toBe(`150${N}000${N}₫`)
    expect(formatMoney('0.125', 'USD')).toBe('$0.12')
    expect(formatMoney('0.135', 'USD')).toBe('$0.14')
    expect(formatMoney('999999.5', 'VND')).toBe(`1${N}000${N}000${N}₫`)
    expect(formatMoney(null, 'VND')).toBe('—')
    expect(formatMoney('0', 'VND')).toBe(`0${N}₫`)
  })
  it('relative time in Asia/Ho_Chi_Minh', () => {
    const now = new Date('2026-10-27T10:00:00Z') // 17:00 in HCM
    expect(formatWhen('2026-10-27T14:32:00+07:00', now)).toBe('14:32')
    expect(formatWhen('2026-10-26T22:40:00+07:00', now)).toBe('вчера 22:40')
  })
})

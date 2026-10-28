import { describe, expect, it, vi } from 'vitest'
import { normalizeDesktopLocation, routes } from '../routes'

describe('routes contract (ARCH §13)', () => {
  it('mounts every contract path', () => {
    const top = routes.map((r) => r.path)
    expect(top).toEqual(expect.arrayContaining(['/login', '/quick', '/capture', '/']))
    const kids = routes.find((r) => r.path === '/')!.children!.map((r) => (r.index ? '' : r.path))
    expect(kids).toEqual(expect.arrayContaining(['', 'onboarding', 'add', 'operations', 'analytics', 'accounts', 'settings/*']))
  })
})

describe('desktop hash routing', () => {
  const run = (pathname: string, search = '', hash = '') => {
    const replaceState = vi.fn()
    normalizeDesktopLocation({ pathname, search, hash } as Location, { replaceState } as unknown as History)
    return replaceState.mock.calls[0]?.[2]
  }
  it('app://pocket/quick → /#/quick', () => {
    expect(run('/quick')).toBe('/#/quick')
    expect(run('/operations', '?tx=5')).toBe('/#/operations?tx=5')
  })
  it('keeps an existing hash route and the bare index', () => {
    expect(run('/index.html', '', '#/quick')).toBeUndefined()
    expect(run('/index.html')).toBeUndefined()
    expect(run('/')).toBeUndefined()
  })
})

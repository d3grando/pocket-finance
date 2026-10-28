// Contract between web (lib/desktop.ts) and the real Electron preload (desktop/src/preload/index.ts):
// «shown» is window.onShown, platform is process.platform, device name comes from config.get().
import { afterEach, describe, expect, it, vi } from 'vitest'
import { __resetDesktopForTests, desktop, initDesktopBridge, normalizePlatform, type PocketBridge } from '../desktop'

type Cb = (e?: { edge?: 'top' | 'bottom' | 'left' | 'right' }) => void

/** Same shape as desktop/src/shared/ipc.ts PocketBridge (only what the web touches). */
function realPreload(over: Partial<PocketBridge> = {}) {
  const shown: Cb[] = []
  const b = {
    isDesktop: true,
    platform: 'win32',
    windowKind: 'popup',
    auth: { getToken: async () => null, setToken: async () => ({ ok: true }), clearToken: async () => {} },
    config: {
      get: async () => ({ serverUrl: 'https://pocket.example.com', apiBaseUrl: 'https://pocket.example.com/api/v1', hotkey: 'Ctrl+Alt+Space', autostart: true, platform: 'win32', deviceName: 'DESKTOP-7Q', signedIn: false }),
    },
    window: {
      hide: async () => {}, openFull: async () => {}, resize: async () => {}, setPinned: async () => {},
      onShown: (cb: Cb) => { shown.push(cb); return () => { shown.splice(shown.indexOf(cb), 1) } },
      onHidden: () => () => {},
    },
    ...over,
  }
  ;(window as unknown as { pocket: unknown }).pocket = b
  return { shown, fire: (e?: Parameters<Cb>[0]) => shown.slice().forEach((cb) => cb(e)) }
}

afterEach(() => {
  delete (window as unknown as { pocket?: unknown }).pocket
  __resetDesktopForTests()
})

describe('normalizePlatform', () => {
  it('maps process.platform to the API enum', () => {
    expect(normalizePlatform('win32')).toBe('windows')
    expect(normalizePlatform('darwin')).toBe('macos')
    expect(normalizePlatform('linux')).toBe('linux')
  })
  it('accepts already normalized values and rejects the rest', () => {
    expect(normalizePlatform('windows')).toBe('windows')
    expect(normalizePlatform('macos')).toBe('macos')
    expect(normalizePlatform('freebsd')).toBeNull()
    expect(normalizePlatform(undefined)).toBeNull()
  })
})

describe('desktop bridge adapter (real preload shape)', () => {
  it('onShow subscribes through window.onShown and passes the edge', () => {
    const p = realPreload()
    const cb = vi.fn()
    const off = desktop.onShow(cb)
    expect(p.shown).toHaveLength(1)
    p.fire({ edge: 'top' })
    expect(cb).toHaveBeenCalledWith({ edge: 'top' })
    off()
    expect(p.shown).toHaveLength(0)
    p.fire({ edge: 'bottom' })
    expect(cb).toHaveBeenCalledTimes(1)
  })

  it('still works with the legacy top-level onShow and without any bridge', () => {
    const legacy: Cb[] = []
    ;(window as unknown as { pocket: unknown }).pocket = { onShow: (cb: Cb) => { legacy.push(cb) } }
    const cb = vi.fn()
    const off = desktop.onShow(cb)
    legacy[0]()
    expect(cb).toHaveBeenCalledWith({})
    expect(typeof off).toBe('function')
    delete (window as unknown as { pocket?: unknown }).pocket
    expect(() => desktop.onShow(cb)()).not.toThrow()
  })

  it('platform is sent to the API as windows|macos|linux, never win32', () => {
    realPreload()
    expect(desktop.platform()).toBe('windows')
    realPreload({ platform: 'darwin' })
    expect(desktop.platform()).toBe('macos')
  })

  it('device name comes from config.get().deviceName after init', async () => {
    realPreload()
    expect(desktop.deviceName()).toBe('Pocket Desktop')
    await initDesktopBridge()
    expect(desktop.deviceName()).toBe('DESKTOP-7Q')
  })
})

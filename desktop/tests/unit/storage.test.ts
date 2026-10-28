import { defaultHotkey } from '../../src/shared/ipc'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { initAutostart, launchedHidden, setAutostart, type LoginItemPort } from '../../src/main/autostart'
import { ConfigStore, DEFAULTS, sanitizeConfig } from '../../src/main/config'
import { TokenStore, isValidDeviceToken, type CryptoPort } from '../../src/main/token-store'

const TOKEN = 'pfd_' + 'A'.repeat(43)

/** safeStorage double: XOR "encryption" so the plaintext never appears in the file. */
function fakeCrypto(available = true): CryptoPort & { calls: number } {
  const c = {
    calls: 0,
    isEncryptionAvailable: () => available,
    encryptString: (s: string) => {
      c.calls++
      return Buffer.from([...Buffer.from(s, 'utf8')].map((b) => b ^ 0x5a))
    },
    decryptString: (b: Buffer) => Buffer.from([...b].map((x) => x ^ 0x5a)).toString('utf8'),
  }
  return c
}

let dir: string
beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pocket-unit-'))
})
afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true })
})

describe('TokenStore (safeStorage)', () => {
  it('stores the token encrypted and reads it back after a restart', () => {
    const file = path.join(dir, 'device-token.bin')
    const crypto = fakeCrypto()
    const s1 = new TokenStore(file, crypto)
    expect(s1.get()).toBeNull()
    expect(s1.set(TOKEN)).toEqual({ ok: true, persisted: true })
    expect(fs.readFileSync(file).toString('utf8')).not.toContain('pfd_')
    const s2 = new TokenStore(file, crypto)
    expect(s2.get()).toBe(TOKEN)
    expect(s2.persisted).toBe(true)
  })

  it('never writes plaintext when encryption is unavailable (memory only)', () => {
    const file = path.join(dir, 'device-token.bin')
    const s = new TokenStore(file, fakeCrypto(false))
    expect(s.set(TOKEN)).toEqual({ ok: true, persisted: false })
    expect(s.get()).toBe(TOKEN)
    expect(fs.existsSync(file)).toBe(false)
  })

  it('rejects malformed tokens and drops undecryptable files', () => {
    const file = path.join(dir, 'device-token.bin')
    const s = new TokenStore(file, fakeCrypto())
    expect(s.set('not-a-token').ok).toBe(false)
    expect(isValidDeviceToken('pfd_short')).toBe(false)
    fs.writeFileSync(file, Buffer.from('garbage'))
    const broken = new TokenStore(file, {
      isEncryptionAvailable: () => true,
      encryptString: () => Buffer.alloc(0),
      decryptString: () => {
        throw new Error('DPAPI: another user')
      },
    })
    expect(broken.get()).toBeNull()
    expect(fs.existsSync(file)).toBe(false)
  })

  it('clear() forgets the token on disk and in memory', () => {
    const file = path.join(dir, 'device-token.bin')
    const s = new TokenStore(file, fakeCrypto())
    s.set(TOKEN)
    s.clear()
    expect(s.get()).toBeNull()
    expect(fs.existsSync(file)).toBe(false)
  })
})

describe('ConfigStore', () => {
  it('defaults: no server (asked on the first run unless PF_DEFAULT_SERVER_URL is built in), Ctrl+Alt+Space, autostart on', () => {
    const c = new ConfigStore(path.join(dir, 'config.json')).get()
    expect(c).toEqual(DEFAULTS)
    expect(c.serverUrl).toBe('')
    expect(c.hotkey).toBe(defaultHotkey(process.platform)) // ⌘⇧Space on macOS
    expect(c.autostart).toBe(true)
  })

  it('persists updates and sanitizes bad values', () => {
    const file = path.join(dir, 'config.json')
    const s = new ConfigStore(file)
    s.update({ hotkey: 'alt+shift+space', serverUrl: 'https://money.example.com/' })
    const again = new ConfigStore(file).get()
    expect(again.hotkey).toBe('Alt+Shift+Space')
    expect(again.serverUrl).toBe('https://money.example.com')
    expect(sanitizeConfig({ serverUrl: 'http://evil.example.com', hotkey: 'A', autostart: 'yes' })).toEqual(DEFAULTS)
    fs.writeFileSync(file, '{broken json')
    expect(new ConfigStore(file).get()).toEqual(DEFAULTS)
  })
})

describe('autostart', () => {
  function port(supported = true): LoginItemPort & { state: boolean; sets: boolean[] } {
    const p = {
      supported,
      state: false,
      sets: [] as boolean[],
      set(v: boolean) {
        p.state = v
        p.sets.push(v)
      },
      get: () => p.state,
    }
    return p
  }

  it('first run of the installed app enables autostart', () => {
    const p = port()
    expect(initAutostart({ autostart: false, autostartInitialized: false }, p)).toEqual({ autostart: true, autostartInitialized: true })
    expect(p.state).toBe(true)
  })

  it('later runs keep the user choice (off stays off)', () => {
    const p = port()
    initAutostart({ autostart: false, autostartInitialized: true }, p)
    expect(p.sets).toEqual([false])
    expect(setAutostart(true, p)).toBe(true)
  })

  it('dev / unsupported platforms do not touch login items', () => {
    const p = port(false)
    const cfg = { autostart: true, autostartInitialized: false }
    expect(initAutostart(cfg, p)).toBe(cfg)
    expect(p.sets).toEqual([])
  })

  it('--hidden marks an autostart launch', () => {
    expect(launchedHidden(['Pocket.exe', '--hidden'])).toBe(true)
    expect(launchedHidden(['Pocket.exe'])).toBe(false)
  })
})

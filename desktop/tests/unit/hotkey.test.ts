import { describe, expect, it } from 'vitest'
import { acceleratorFromEvent, checkAccelerator, formatAccelerator, MSG_RESERVED, normalizeAccelerator, RESERVED_ACCELERATORS } from '../../src/shared/accelerator'
import { HOTKEY_CANDIDATES, HotkeyManager, MSG_TAKEN, type ShortcutPort } from '../../src/main/hotkey'
import { sanitizeConfig } from '../../src/main/config'
import { defaultHotkey } from '../../src/shared/ipc'

/** globalShortcut double: `taken` = combinations owned by other programs. */
function fakeShortcuts(taken: string[] = []) {
  const registered = new Map<string, () => void>()
  const port: ShortcutPort = {
    register(acc, cb) {
      if (acc === 'Ctrl+Alt+F24') throw new Error('conversion failure')
      if (taken.includes(acc) || registered.has(acc)) return false
      registered.set(acc, cb)
      return true
    },
    unregister(acc) {
      registered.delete(acc)
    },
  }
  return { port, registered, press: (acc: string) => registered.get(acc)?.() }
}

describe('normalizeAccelerator', () => {
  it('normalizes case, spacing, aliases and modifier order', () => {
    expect(normalizeAccelerator('ctrl + alt + space')).toBe('Ctrl+Alt+Space')
    expect(normalizeAccelerator('Alt+Control+p')).toBe('Ctrl+Alt+P')
    expect(normalizeAccelerator('Shift+Win+F5')).toBe('Shift+Super+F5')
    expect(normalizeAccelerator('CmdOrCtrl+Shift+Enter')).toBe('Ctrl+Shift+Enter')
  })
  it('rejects combinations that are not a safe global hotkey', () => {
    expect(normalizeAccelerator('Space')).toBeNull()
    expect(normalizeAccelerator('Shift+A')).toBeNull() // would hijack typing
    expect(normalizeAccelerator('Ctrl+Alt')).toBeNull()
    expect(normalizeAccelerator('Ctrl+A+B')).toBeNull()
    expect(normalizeAccelerator('Ctrl+Ctrl+A')).toBeNull()
    expect(normalizeAccelerator('Ctrl+Привет')).toBeNull()
    expect(normalizeAccelerator('')).toBeNull()
    expect(normalizeAccelerator('Ctrl++')).toBeNull()
  })
})

describe('system shortcuts are never a global hotkey', () => {
  const reserved = [
    'Ctrl+C', 'Ctrl+V', 'Ctrl+X', 'Ctrl+A', 'Ctrl+Z', 'Ctrl+Y', 'ctrl+shift+z', 'Control+Insert',
    'Alt+F4', 'Alt+Tab', 'Alt+Shift+Tab', 'Ctrl+Alt+Del', 'Ctrl+Alt+Delete',
    'Win+L', 'Win+D', 'Super+Tab', 'Win+E', 'Win+R', 'Win+1', 'Win+Space', 'Win+Left', 'Win+Shift+S', 'Ctrl+Win+D', 'Ctrl+Win+Right',
  ]
  it.each(reserved)('%s is reserved', (acc) => {
    expect(checkAccelerator(acc)).toEqual({ ok: false, reason: 'reserved' })
    expect(normalizeAccelerator(acc)).toBeNull()
  })
  it('ordinary combinations stay allowed; bare keys stay invalid', () => {
    for (const acc of ['Ctrl+Alt+Space', 'Ctrl+Shift+V', 'Ctrl+Alt+C', 'Alt+Shift+Space', 'Ctrl+Super+P', 'Shift+Super+F5']) {
      expect(checkAccelerator(acc).ok, acc).toBe(true)
    }
    expect(checkAccelerator('V')).toEqual({ ok: false, reason: 'invalid' })
    expect(checkAccelerator('Shift+V')).toEqual({ ok: false, reason: 'invalid' })
    for (const c of HOTKEY_CANDIDATES) expect(RESERVED_ACCELERATORS.has(c), c).toBe(false)
  })
  it('recording Ctrl+V / Alt+F4 says why; HotkeyManager refuses and keeps the old one', () => {
    const ev = (code: string, key: string, m: Partial<{ ctrlKey: boolean; altKey: boolean; shiftKey: boolean; metaKey: boolean }>) =>
      ({ code, key, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...m })
    expect(acceleratorFromEvent(ev('KeyV', 'м', { ctrlKey: true }))).toEqual({ kind: 'invalid', message: MSG_RESERVED })
    expect(acceleratorFromEvent(ev('F4', 'F4', { altKey: true }))).toEqual({ kind: 'invalid', message: MSG_RESERVED })
    expect(acceleratorFromEvent(ev('KeyL', 'l', { metaKey: true }))).toEqual({ kind: 'invalid', message: MSG_RESERVED })
    const f = fakeShortcuts()
    const m = new HotkeyManager(f.port, () => {})
    m.init('Ctrl+Alt+Space')
    const r = m.apply('Ctrl+V')
    expect(r).toMatchObject({ ok: false, reason: 'invalid', message: MSG_RESERVED })
    expect([...f.registered.keys()]).toEqual(['Ctrl+Alt+Space'])
    expect(f.registered.has('Ctrl+V')).toBe(false)
  })
  it('a reserved hotkey already saved in config.json falls back to the default', () => {
    expect(sanitizeConfig({ hotkey: 'Ctrl+V' }).hotkey).toBe(defaultHotkey(process.platform))
    expect(sanitizeConfig({ hotkey: 'Alt+F4' }).hotkey).toBe(defaultHotkey(process.platform))
  })
})

describe('acceleratorFromEvent', () => {
  const ev = (code: string, key: string, mods: Partial<{ ctrlKey: boolean; altKey: boolean; shiftKey: boolean; metaKey: boolean }> = {}) => ({
    code,
    key,
    ctrlKey: false,
    altKey: false,
    shiftKey: false,
    metaKey: false,
    ...mods,
  })
  it('records layout-independent accelerators', () => {
    expect(acceleratorFromEvent(ev('Space', ' ', { ctrlKey: true, altKey: true }))).toEqual({ kind: 'ok', accelerator: 'Ctrl+Alt+Space' })
    // Russian layout: key is «з», code is KeyP
    expect(acceleratorFromEvent(ev('KeyP', 'з', { ctrlKey: true, shiftKey: true }))).toEqual({ kind: 'ok', accelerator: 'Ctrl+Shift+P' })
  })
  it('waits while only modifiers are pressed and rejects bare keys', () => {
    expect(acceleratorFromEvent(ev('ControlLeft', 'Control', { ctrlKey: true }))).toEqual({ kind: 'pending' })
    expect(acceleratorFromEvent(ev('KeyA', 'a')).kind).toBe('invalid')
  })
  it('formats for humans', () => {
    expect(formatAccelerator('Ctrl+Alt+Space')).toBe('Ctrl + Alt + Space')
    expect(formatAccelerator('Super+Shift+P', 'win32')).toBe('Win + Shift + P')
  })
})

describe('HotkeyManager', () => {
  it('registers the default hotkey and toggles on press', () => {
    const f = fakeShortcuts()
    let n = 0
    const m = new HotkeyManager(f.port, () => n++)
    expect(m.init('Ctrl+Alt+Space')).toEqual({ ok: true, accelerator: 'Ctrl+Alt+Space' })
    expect(m.status).toBe('ok')
    f.press('Ctrl+Alt+Space')
    expect(n).toBe(1)
  })

  it('taken at startup: reports it, keeps no hotkey, suggests free ones', () => {
    const f = fakeShortcuts(['Ctrl+Alt+Space'])
    const m = new HotkeyManager(f.port, () => {})
    const r = m.init('Ctrl+Alt+Space')
    expect(r.ok).toBe(false)
    if (!r.ok) {
      expect(r.reason).toBe('taken')
      expect(r.message).toBe(MSG_TAKEN)
      expect(r.suggestions.length).toBeGreaterThan(0)
      expect(r.suggestions).not.toContain('Ctrl+Alt+Space')
    }
    expect(m.status).toBe('taken')
    expect(m.active).toBeNull()
    expect(f.registered.size).toBe(0) // suggestion probes are released
  })

  it('changing to a taken combination keeps the old one working', () => {
    const f = fakeShortcuts(['Ctrl+Shift+P'])
    const m = new HotkeyManager(f.port, () => {})
    m.init('Ctrl+Alt+Space')
    const r = m.apply('Ctrl+Shift+P')
    expect(r.ok).toBe(false)
    expect(m.active).toBe('Ctrl+Alt+Space')
    expect([...f.registered.keys()]).toEqual(['Ctrl+Alt+Space'])
    expect(m.status).toBe('ok')
  })

  it('changing to a free combination swaps registrations', () => {
    const f = fakeShortcuts()
    const m = new HotkeyManager(f.port, () => {})
    m.init('Ctrl+Alt+Space')
    expect(m.apply('alt+shift+space')).toEqual({ ok: true, accelerator: 'Alt+Shift+Space' })
    expect([...f.registered.keys()]).toEqual(['Alt+Shift+Space'])
  })

  it('re-applying the active hotkey is a success, not «taken»', () => {
    const f = fakeShortcuts()
    const m = new HotkeyManager(f.port, () => {})
    m.init('Ctrl+Alt+Space')
    expect(m.apply('Ctrl+Alt+Space').ok).toBe(true)
  })

  it('invalid accelerators and Electron conversion errors', () => {
    const f = fakeShortcuts()
    const m = new HotkeyManager(f.port, () => {})
    m.init('Ctrl+Alt+Space')
    const r1 = m.apply('Shift+A')
    expect(r1.ok === false && r1.reason).toBe('invalid')
    const r2 = m.apply('Ctrl+Alt+F24') // Electron throws → treated as not registrable
    expect(r2.ok).toBe(false)
    expect(m.active).toBe('Ctrl+Alt+Space')
  })

  it('suspend/resume around recording; a new hotkey chosen while suspended is registered on resume', () => {
    const f = fakeShortcuts(['Ctrl+Alt+K'])
    const m = new HotkeyManager(f.port, () => {})
    m.init('Ctrl+Alt+Space')
    m.suspend()
    expect(f.registered.size).toBe(0)
    expect(m.apply('Ctrl+Alt+K').ok).toBe(false) // taken by another program
    expect(m.apply('Ctrl+Alt+P')).toEqual({ ok: true, accelerator: 'Ctrl+Alt+P' })
    expect(f.registered.size).toBe(0) // only probed
    m.resume()
    expect([...f.registered.keys()]).toEqual(['Ctrl+Alt+P'])
    expect(m.active).toBe('Ctrl+Alt+P')
  })

  it('suggestions skip taken and the current hotkey', () => {
    const f = fakeShortcuts(['Ctrl+Shift+Space'])
    const m = new HotkeyManager(f.port, () => {})
    m.init('Ctrl+Alt+Space')
    const s = m.suggestions()
    expect(s).toHaveLength(3)
    expect(s).not.toContain('Ctrl+Alt+Space')
    expect(s).not.toContain('Ctrl+Shift+Space')
    for (const c of s) expect(HOTKEY_CANDIDATES).toContain(c)
  })
})

describe('macOS default hotkey', () => {
  it('is ⌘⇧Space on macOS and Ctrl+Alt+Space elsewhere, and both are valid accelerators', async () => {
    const { defaultHotkey } = await import('../../src/shared/ipc')
    expect(defaultHotkey('darwin')).toBe('Shift+Super+Space')
    expect(defaultHotkey('win32')).toBe('Ctrl+Alt+Space')
    expect(normalizeAccelerator(defaultHotkey('darwin'))).toBe('Shift+Super+Space')
    expect(formatAccelerator(defaultHotkey('darwin'), 'darwin')).toContain('Cmd')
    expect(checkAccelerator(defaultHotkey('darwin')).ok).toBe(true)
  })
})

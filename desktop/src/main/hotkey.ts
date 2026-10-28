// Global hotkey manager (pure logic over an injected globalShortcut-like port; unit-tested).
// globalShortcut.register() returns false when another application already owns the
// combination — that is the "taken" check required by the spec; we then keep the previous
// hotkey working and offer free alternatives.
import { checkAccelerator, MSG_RESERVED, normalizeAccelerator } from '../shared/accelerator'
import type { HotkeyResult, HotkeyStatus } from '../shared/ipc'

export interface ShortcutPort {
  register(accelerator: string, callback: () => void): boolean
  unregister(accelerator: string): void
}

export const HOTKEY_CANDIDATES = [
  'Ctrl+Alt+Space',
  'Ctrl+Shift+Space',
  'Alt+Shift+Space',
  'Ctrl+Alt+P',
  'Ctrl+Alt+K',
  'Ctrl+Shift+P',
  'Ctrl+Alt+M',
  'Ctrl+Alt+Enter',
]

export const MSG_TAKEN = 'Сочетание занято другой программой. Выберите другое.'
export const MSG_INVALID = 'Нужна клавиша вместе с Ctrl, Alt или Win'

export class HotkeyManager {
  private current: string | null = null
  private desired: string | null = null
  private suspended = false
  private _status: HotkeyStatus = 'off'

  constructor(
    private readonly port: ShortcutPort,
    private readonly onTrigger: () => void,
  ) {}

  /** Accelerator that is actually registered right now (null = none). */
  get active(): string | null {
    return this.current
  }

  get status(): HotkeyStatus {
    return this._status
  }

  private tryRegister(acc: string): boolean {
    try {
      return this.port.register(acc, () => this.onTrigger())
    } catch {
      // Electron throws on accelerators it cannot convert
      return false
    }
  }

  /**
   * Register `input` as the hotkey. On failure the previously working hotkey stays registered.
   * Re-applying the active accelerator is a no-op success (register() would return false for
   * a combination we already own).
   */
  apply(input: string): HotkeyResult {
    const c = checkAccelerator(input)
    if (!c.ok) return { ok: false, reason: 'invalid', message: c.reason === 'reserved' ? MSG_RESERVED : MSG_INVALID, suggestions: this.suggestions() }
    const acc = c.accelerator
    if (acc === this.current && !this.suspended) {
      this.desired = acc
      this._status = 'ok'
      return { ok: true, accelerator: acc }
    }
    if (this.suspended) {
      // while recording nothing is registered: just verify that the combination is free
      if (!this.tryRegister(acc)) {
        return { ok: false, reason: 'taken', message: MSG_TAKEN, suggestions: this.suggestions(acc) }
      }
      this.port.unregister(acc)
      this.current = acc
      this.desired = acc
      this._status = 'ok'
      return { ok: true, accelerator: acc }
    }
    if (!this.tryRegister(acc)) {
      if (this.current === null) {
        this.desired = acc
        this._status = 'taken'
      }
      return { ok: false, reason: 'taken', message: MSG_TAKEN, suggestions: this.suggestions(acc) }
    }
    if (this.current && this.current !== acc) this.port.unregister(this.current)
    this.current = acc
    this.desired = acc
    this._status = 'ok'
    return { ok: true, accelerator: acc }
  }

  /** Startup: register the configured hotkey; if invalid/taken, remember the state for the UI. */
  init(input: string): HotkeyResult {
    const r = this.apply(input)
    if (!r.ok) {
      this.desired = normalizeAccelerator(input)
      this._status = r.reason === 'invalid' ? 'invalid' : 'taken'
    }
    return r
  }

  /** Free combinations from the candidate list (probe: register + unregister). */
  suggestions(exclude?: string, limit = 3): string[] {
    const out: string[] = []
    for (const c of HOTKEY_CANDIDATES) {
      if (out.length >= limit) break
      if (c === exclude) continue
      if (c === this.current) continue
      if (this.tryRegister(c)) {
        this.port.unregister(c)
        out.push(c)
      }
    }
    return out
  }

  /** Release the hotkey while the settings page records a new one (so it can be pressed). */
  suspend(): void {
    if (this.suspended) return
    this.suspended = true
    if (this.current) this.port.unregister(this.current)
  }

  resume(): void {
    if (!this.suspended) return
    this.suspended = false
    const acc = this.current ?? this.desired
    if (!acc) return
    if (this.tryRegister(acc)) {
      this.current = acc
      this._status = 'ok'
    } else {
      this.current = null
      this._status = 'taken'
    }
  }

  dispose(): void {
    if (this.current && !this.suspended) this.port.unregister(this.current)
    this.current = null
  }
}

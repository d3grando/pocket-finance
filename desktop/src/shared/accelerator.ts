// Electron accelerator helpers (pure; used by main for validation and by the settings page
// to turn a KeyboardEvent into an accelerator string). Docs: electronjs.org/docs/latest/api/accelerator

const MODIFIER_ORDER = ['Ctrl', 'Alt', 'Shift', 'Super'] as const
type Modifier = (typeof MODIFIER_ORDER)[number]

const MODIFIER_ALIASES: Record<string, Modifier> = {
  ctrl: 'Ctrl',
  control: 'Ctrl',
  commandorcontrol: 'Ctrl',
  cmdorctrl: 'Ctrl',
  alt: 'Alt',
  option: 'Alt',
  shift: 'Shift',
  super: 'Super',
  meta: 'Super',
  win: 'Super',
  cmd: 'Super',
  command: 'Super',
}

const NAMED_KEYS: Record<string, string> = {
  space: 'Space',
  tab: 'Tab',
  backspace: 'Backspace',
  delete: 'Delete',
  del: 'Delete',
  insert: 'Insert',
  ins: 'Insert',
  enter: 'Enter',
  return: 'Enter',
  up: 'Up',
  down: 'Down',
  left: 'Left',
  right: 'Right',
  home: 'Home',
  end: 'End',
  pageup: 'PageUp',
  pagedown: 'PageDown',
  plus: 'Plus',
  printscreen: 'PrintScreen',
}

const PUNCTUATION = new Set(['-', '=', ',', '.', '/', ';', "'", '[', ']', '\\', '`'])

function normalizeKey(raw: string): string | null {
  const k = raw.trim()
  if (!k) return null
  const lower = k.toLowerCase()
  if (NAMED_KEYS[lower]) return NAMED_KEYS[lower]
  if (/^[a-z]$/i.test(k)) return k.toUpperCase()
  if (/^[0-9]$/.test(k)) return k
  const f = /^f([1-9]|1[0-9]|2[0-4])$/i.exec(k)
  if (f) return `F${f[1]}`
  if (PUNCTUATION.has(k)) return k
  return null
}

/**
 * Combinations a global hotkey must never take over (in normalized form): it would swallow the key
 * in every program — clipboard and undo, closing/switching windows, the secure attention sequence,
 * Windows system shortcuts. Every Win+<key> without another modifier is reserved by Windows.
 */
export const RESERVED_ACCELERATORS: ReadonlySet<string> = new Set([
  // clipboard / select all / undo / redo
  'Ctrl+C', 'Ctrl+V', 'Ctrl+X', 'Ctrl+A', 'Ctrl+Z', 'Ctrl+Y', 'Ctrl+Shift+Z', 'Ctrl+Insert',
  // close / switch windows
  'Alt+F4', 'Ctrl+F4', 'Alt+Tab', 'Alt+Shift+Tab', 'Ctrl+Alt+Tab',
  // secure attention sequence
  'Ctrl+Alt+Delete',
  // Windows: snipping, moving windows between monitors, virtual desktops, Narrator
  'Shift+Super+S', 'Shift+Super+Left', 'Shift+Super+Right', 'Shift+Super+Up', 'Shift+Super+Down',
  'Ctrl+Super+D', 'Ctrl+Super+Left', 'Ctrl+Super+Right', 'Ctrl+Super+F4', 'Ctrl+Super+Enter',
])

export const MSG_RESERVED = 'Это системное сочетание (копировать, вставить, закрыть окно…). Выберите другое.'

export type AcceleratorCheck = { ok: true; accelerator: string } | { ok: false; reason: 'invalid' | 'reserved' }

/** Win+<key> alone (Win+L, Win+D, Win+Tab…) is the system's on Windows (Cmd+<key> on macOS). */
const superOnly = (acc: string) => /^Super\+[^+]+$/.test(acc)

/**
 * Validate a user/stored accelerator: normalized form, or why it cannot be a global hotkey —
 * 'invalid' (syntax, no Ctrl/Alt/Win) or 'reserved' (RESERVED_ACCELERATORS, Win+<key>).
 */
export function checkAccelerator(input: string): AcceleratorCheck {
  const acc = parseAccelerator(input)
  if (!acc) return { ok: false, reason: 'invalid' }
  if (RESERVED_ACCELERATORS.has(acc) || superOnly(acc)) return { ok: false, reason: 'reserved' }
  return { ok: true, accelerator: acc }
}

/**
 * Normalize a user/stored accelerator ("ctrl + alt + space" → "Ctrl+Alt+Space").
 * Returns null when invalid or reserved (see checkAccelerator). A global hotkey must contain Ctrl,
 * Alt or Super (Shift alone would hijack normal typing) and exactly one non-modifier key.
 */
export function normalizeAccelerator(input: string): string | null {
  const c = checkAccelerator(input)
  return c.ok ? c.accelerator : null
}

function parseAccelerator(input: string): string | null {
  if (typeof input !== 'string' || input.length > 64) return null
  // split on '+' but keep a literal "+" key written as "Plus"
  const parts = input.split('+').map((p) => p.trim())
  if (parts.some((p) => p === '')) return null
  const mods = new Set<Modifier>()
  let key: string | null = null
  for (const p of parts) {
    const m = MODIFIER_ALIASES[p.toLowerCase()]
    if (m) {
      if (mods.has(m)) return null
      mods.add(m)
      continue
    }
    if (key !== null) return null
    key = normalizeKey(p)
    if (key === null) return null
  }
  if (key === null) return null
  if (!mods.has('Ctrl') && !mods.has('Alt') && !mods.has('Super')) return null
  return [...MODIFIER_ORDER.filter((m) => mods.has(m)), key].join('+')
}

export interface KeyLike {
  key: string
  code: string
  ctrlKey: boolean
  altKey: boolean
  shiftKey: boolean
  metaKey: boolean
}

function keyFromCode(code: string): string | null {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3)
  if (/^Digit[0-9]$/.test(code)) return code.slice(5)
  if (/^Numpad[0-9]$/.test(code)) return `num${code.slice(6)}`
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code
  const map: Record<string, string> = {
    Space: 'Space',
    Tab: 'Tab',
    Backspace: 'Backspace',
    Delete: 'Delete',
    Insert: 'Insert',
    Enter: 'Enter',
    NumpadEnter: 'Enter',
    ArrowUp: 'Up',
    ArrowDown: 'Down',
    ArrowLeft: 'Left',
    ArrowRight: 'Right',
    Home: 'Home',
    End: 'End',
    PageUp: 'PageUp',
    PageDown: 'PageDown',
    Minus: '-',
    Equal: '=',
    Comma: ',',
    Period: '.',
    Slash: '/',
    Semicolon: ';',
    Quote: "'",
    BracketLeft: '[',
    BracketRight: ']',
    Backslash: '\\',
    Backquote: '`',
  }
  return map[code] ?? null
}

export type RecordResult =
  | { kind: 'pending' } // only modifiers are held so far
  | { kind: 'ok'; accelerator: string }
  | { kind: 'invalid'; message: string }

/** Settings page: turn a keydown into an accelerator (layout-independent, uses `code`). */
export function acceleratorFromEvent(e: KeyLike): RecordResult {
  if (['Control', 'Alt', 'Shift', 'Meta', 'AltGraph', 'OS'].includes(e.key)) return { kind: 'pending' }
  const key = keyFromCode(e.code)
  if (!key || key.startsWith('num')) return { kind: 'invalid', message: 'Эту клавишу нельзя назначить' }
  const parts: string[] = []
  if (e.ctrlKey) parts.push('Ctrl')
  if (e.altKey) parts.push('Alt')
  if (e.shiftKey) parts.push('Shift')
  if (e.metaKey) parts.push('Super')
  parts.push(key)
  const c = checkAccelerator(parts.join('+'))
  if (!c.ok) return { kind: 'invalid', message: c.reason === 'reserved' ? MSG_RESERVED : 'Нужна клавиша вместе с Ctrl, Alt или Win' }
  return { kind: 'ok', accelerator: c.accelerator }
}

/** Human label for UI: "Ctrl+Alt+Space" → "Ctrl + Alt + Space" (Super → Win on Windows). */
export function formatAccelerator(acc: string, platform = 'win32'): string {
  return acc
    .split('+')
    .map((p) => (p === 'Super' ? (platform === 'darwin' ? 'Cmd' : 'Win') : p))
    .join(' + ')
}

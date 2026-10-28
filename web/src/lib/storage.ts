// Storage access is always wrapped: private mode / blocked storage must never break the UI.
type Kind = 'local' | 'session'
const store = (k: Kind): Storage | null => {
  try { return k === 'local' ? window.localStorage : window.sessionStorage } catch { return null }
}
export function readStore(key: string, kind: Kind = 'local'): string | null {
  try { return store(kind)?.getItem(key) ?? null } catch { return null }
}
export function writeStore(key: string, value: string | null, kind: Kind = 'local'): void {
  try {
    const s = store(kind)
    if (!s) return
    if (value === null) s.removeItem(key)
    else s.setItem(key, value)
  } catch { /* ignore */ }
}
export function readJson<T>(key: string, kind: Kind = 'local'): T | null {
  const raw = readStore(key, kind)
  if (!raw) return null
  try { return JSON.parse(raw) as T } catch { return null }
}
export function writeJson(key: string, value: unknown, kind: Kind = 'local'): void {
  writeStore(key, value === null || value === undefined ? null : JSON.stringify(value), kind)
}

/** Keys used across the app. */
export const STORE = {
  theme: 'pf.theme', // system | light | dark (DESIGN §1.1)
  draft: 'pf.ai.draft', // AI-line text kept across a 401 redirect (session)
  lastSent: 'pf.ai.last', // ↑ in empty field
  sidebar: 'pf.sidebar.collapsed',
  passkeyOffer: 'pf.passkey.offer', // dismissed | done
  hasPasskey: 'pf.passkey.device', // this device registered a passkey
  dynamicsGran: 'pf.home.dynamics',
} as const

/**
 * Device-level view preferences that hold no personal data — they survive a sign-out. Everything
 * else under `pf.*` (the last AI line, the passkey offer state, …) belongs to the signed-in user.
 * `pf.passkey.device` stays: it only says «this device has a passkey» and keeps the Face ID-first
 * login screen (DESIGN §10.8 A) after an expired session.
 */
export const DEVICE_PREFS: readonly string[] = [STORE.theme, STORE.sidebar, STORE.dynamicsGran, STORE.hasPasskey, 'pf.density']

/**
 * Forget the user's data kept in the browser: every `pf.*` key of localStorage except
 * DEVICE_PREFS; with `session: true` also every `pf.*` key of sessionStorage (the AI-line draft).
 * Called on logout and on any 401 (a 401 keeps the draft: it is restored after signing in again).
 */
export function clearUserStorage({ session = false }: { session?: boolean } = {}): void {
  const kinds: Kind[] = session ? ['local', 'session'] : ['local']
  for (const kind of kinds) {
    try {
      const s = store(kind)
      if (!s) continue
      const keys: string[] = []
      for (let i = 0; i < s.length; i++) {
        const k = s.key(i)
        if (k && k.startsWith('pf.') && !(kind === 'local' && DEVICE_PREFS.includes(k))) keys.push(k)
      }
      for (const k of keys) s.removeItem(k)
    } catch { /* blocked storage: nothing to clear */ }
  }
}

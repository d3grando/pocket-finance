// Changing the server address (pure logic, unit-tested; index.ts wires the native dialog).
// A device token and the password typed next belong to the server in the settings, so replacing an
// existing address is confirmed in a native dialog (a page cannot fake or skip it). The very first
// address (fresh install without PF_DEFAULT_SERVER_URL, typed on the login screen) needs no confirmation.
import { hostLabel, normalizeServerUrl } from './urls'

export interface ServerChangeDeps {
  current(): string
  /** native «Сменить сервер?»; true = confirmed */
  confirm(from: string, to: string): Promise<boolean>
  /** persist the new address, forget the token, reload the pages */
  apply(serverUrl: string): void
}

export type ServerChangeResult = { ok: true; changed: boolean } | { ok: false; message: string }

export async function changeServerUrl(input: string, deps: ServerChangeDeps): Promise<ServerChangeResult> {
  const s = normalizeServerUrl(input)
  if (!s.ok) return { ok: false, message: s.message }
  const current = deps.current()
  if (s.serverUrl === current) return { ok: true, changed: false }
  if (current && !(await deps.confirm(hostLabel(current), hostLabel(s.serverUrl)))) {
    return { ok: false, message: 'Сервер не изменён' }
  }
  deps.apply(s.serverUrl)
  return { ok: true, changed: true }
}

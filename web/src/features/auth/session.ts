// Login flows: password (web cookie session / Electron device token), passkey
// (Face ID via WebAuthn, @simplewebauthn/browser), logout.
import { browserSupportsWebAuthn, platformAuthenticatorIsAvailable, startAuthentication, startRegistration } from '@simplewebauthn/browser'
import type { QueryClient } from '@tanstack/react-query'
import { ApiError, configureApi, onApiEvent, setCsrfToken } from '@/lib/api/client'
import { authApi, passkeysApi } from '@/lib/api/endpoints'
import { qk } from '@/lib/api/keys'
import type { MeOut } from '@/lib/api/types'
import { bridge, desktop, hasDesktopAuth } from '@/lib/desktop'
import { clearUserStorage, readStore, STORE, writeStore } from '@/lib/storage'

export async function loginWithPassword(qc: QueryClient, password: string): Promise<MeOut> {
  let me: MeOut
  if (hasDesktopAuth() && typeof bridge()?.auth?.setToken === 'function') {
    // Electron: password once → device token in safeStorage
    const out = await authApi.deviceLogin({ password, device_name: desktop.deviceName(), platform: desktop.platform() })
    await desktop.setToken(out.token)
    configureApi({ getToken: () => bridge()!.auth!.getToken!() })
    me = await authApi.me()
  } else {
    me = await authApi.login({ password })
  }
  setCsrfToken(me.csrf_token)
  qc.setQueryData(qk.me, me)
  return me
}

export async function webauthnAvailable(): Promise<{ supported: boolean; platform: boolean }> {
  const supported = browserSupportsWebAuthn()
  let platform = false
  if (supported) {
    try { platform = await platformAuthenticatorIsAvailable() } catch { platform = false }
  }
  return { supported, platform }
}

export async function loginWithPasskey(qc: QueryClient): Promise<MeOut> {
  const { challenge_id, options } = await passkeysApi.loginOptions('owner')
  const credential = await startAuthentication({ optionsJSON: options as never })
  const me = await passkeysApi.loginVerify({ challenge_id, credential })
  setCsrfToken(me.csrf_token)
  qc.setQueryData(qk.me, me)
  writeStore(STORE.hasPasskey, '1')
  return me
}

export function deviceLabel(): string {
  const ua = typeof navigator === 'undefined' ? '' : navigator.userAgent
  if (/iPhone/i.test(ua)) return 'iPhone'
  if (/iPad/i.test(ua)) return 'iPad'
  if (/Android/i.test(ua)) return 'Android'
  if (/Mac/i.test(ua)) return 'Mac'
  if (/Windows/i.test(ua)) return 'Windows'
  return 'Это устройство'
}

/**
 * Add a passkey for this account. Call with `password: null` first: a session opened ≤ 5 min ago
 * passes (Face ID offer right after the login); otherwise the server answers 403 reauth_required
 * (see isReauthRequired) and the caller asks for the password and calls again with it.
 */
export async function registerPasskey(qc: QueryClient, name = deviceLabel(), password: string | null = null) {
  const { challenge_id, options } = await passkeysApi.registerOptions(password)
  const credential = await startRegistration({ optionsJSON: options as never })
  const pk = await passkeysApi.registerVerify({ challenge_id, credential, name })
  writeStore(STORE.hasPasskey, '1')
  writeStore(STORE.passkeyOffer, 'done')
  void qc.invalidateQueries({ queryKey: qk.me })
  void qc.invalidateQueries({ queryKey: qk.passkeys })
  return pk
}

/** 403 reauth_required: adding a passkey needs the password (the session is not fresh). */
export const isReauthRequired = (e: unknown) => e instanceof ApiError && e.status === 403 && e.code === 'reauth_required'

export const deviceHasPasskey = () => readStore(STORE.hasPasskey) === '1'

/** The server removed the passkeys (logout-all / password change): this device has none now. */
export function forgetDevicePasskey(): void {
  writeStore(STORE.hasPasskey, null)
}

/** «Выйти»: end this session on the server, forget the user's data in this browser, empty the cache. */
export async function logout(qc: QueryClient) {
  try { await authApi.logout() } catch { /* the session may already be gone */ }
  if (hasDesktopAuth()) await desktop.clearToken()
  setCsrfToken(null)
  clearUserStorage({ session: true })
  qc.clear()
}

let cleanupInstalled = false

/** Any 401 = no session any more: drop the user's data kept in localStorage (the draft survives). */
export function installSignOutCleanup(): () => void {
  if (cleanupInstalled) return () => {}
  cleanupInstalled = true
  const off = onApiEvent((e) => { if (e.type === 'unauthorized') clearUserStorage() })
  return () => { off(); cleanupInstalled = false }
}

/** WebAuthn errors → calm Russian text; null = user cancelled (no message). */
export function passkeyErrorText(e: unknown): string | null {
  const name = (e as { name?: string })?.name
  if (name === 'NotAllowedError' || name === 'AbortError') return null
  if (name === 'InvalidStateError') return 'Этот ключ уже зарегистрирован'
  if (name === 'SecurityError') return 'Face ID недоступен на этом адресе'
  return (e as Error)?.message || 'Не удалось войти с Face ID'
}

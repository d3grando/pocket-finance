// Settings data hooks: PATCH /settings with `version` (one transparent retry on 409 — only the changed
// field is sent, so the retry cannot clobber other fields), theme application.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, qk, type SettingsOut, type SettingsPatch } from '@/lib/api'
import { errorMessage } from '@/features/operations/api'
import { notify } from '@/features/operations/ui/overlays'

export function useSettingsPatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (patch: Omit<SettingsPatch, 'version'>) => {
      const cur = qc.getQueryData<SettingsOut>(qk.settings) ?? (await api.get<SettingsOut>('/settings'))
      try {
        return await api.patch<SettingsOut>('/settings', { ...patch, version: cur.version })
      } catch (e) {
        if (e instanceof ApiError && e.code === 'version_conflict') {
          const fresh = await api.get<SettingsOut>('/settings')
          return api.patch<SettingsOut>('/settings', { ...patch, version: fresh.version })
        }
        throw e
      }
    },
    onSuccess: (s) => {
      qc.setQueryData(qk.settings, s)
      void qc.invalidateQueries({ queryKey: ['stats'] })
    },
    onError: (e) => {
      void qc.invalidateQueries({ queryKey: qk.settings })
      notify.error(errorMessage(e, 'Не удалось сохранить настройку'))
    },
  })
}

export type ThemePref = 'system' | 'light' | 'dark'

/** Apply theme now (class `.dark` on <html>, localStorage `pf.theme`) — DESIGN §1.1. */
export function applyTheme(t: ThemePref) {
  try {
    if (t === 'system') localStorage.removeItem('pf.theme')
    else localStorage.setItem('pf.theme', t)
  } catch {
    /* private mode */
  }
  const dark = t === 'dark' || (t === 'system' && typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', !!dark)
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  window.dispatchEvent(new CustomEvent('pf:theme', { detail: t }))
  // the shell's theme store (lib/theme.ts) re-reads `pf.theme` on a storage event → its toggles stay in sync
  try {
    window.dispatchEvent(new StorageEvent('storage', { key: 'pf.theme', newValue: t === 'system' ? null : t }))
  } catch {
    /* old browsers */
  }
}

export const TIMEZONES = [
  'Asia/Ho_Chi_Minh',
  'Asia/Bangkok',
  'Asia/Singapore',
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Asia/Dubai',
  'Asia/Almaty',
  'Asia/Novosibirsk',
  'Asia/Yekaterinburg',
  'Europe/Moscow',
  'Europe/Istanbul',
  'Europe/Berlin',
  'Europe/London',
  'UTC',
  'America/New_York',
]

export function tzOffsetLabel(tz: string, now = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'shortOffset' }).formatToParts(now)
    const off = parts.find((p) => p.type === 'timeZoneName')?.value ?? ''
    return off.replace('GMT', 'UTC') || 'UTC'
  } catch {
    return ''
  }
}

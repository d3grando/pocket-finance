// Settings → Вход и устройства: passkeys (Face ID) add/rename/delete, password change, web sessions and
// Electron devices with [Отозвать].
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { browserSupportsWebAuthn } from '@simplewebauthn/browser'
import { KeyRound, Laptop, LogOut, Monitor, ScanFace, Smartphone, Trash2 } from 'lucide-react'
import { api, ApiError, authApi, qk, usersApi, type DeviceOut, type MeOut, type PasskeyOut, type SessionOut } from '@/lib/api'
import { forgetDevicePasskey } from '@/features/auth/session'
import { usePasskeyAdd } from '@/features/auth/usePasskeyAdd'
import { useSignOut } from '@/features/auth/useSignOut'
import { errorMessage, useToday } from '@/features/operations/api'
import { formatDateTime } from '@/features/operations/lib/dates'
import { Badge, Button, Checkbox, Field, Input, Skeleton } from '@/features/operations/ui/kit'
import { notify } from '@/features/operations/ui/overlays'
import { SettingsSection } from '../ui'

function guessDeviceName(): string {
  const ua = typeof navigator === 'undefined' ? '' : navigator.userAgent
  if (/iPhone/.test(ua)) return 'iPhone'
  if (/iPad/.test(ua)) return 'iPad'
  if (/Android/.test(ua)) return 'Android'
  if (/Mac/.test(ua)) return 'Mac'
  if (/Windows/.test(ua)) return 'Windows'
  return 'Это устройство'
}

export function uaLabel(ua: string | null): string {
  if (!ua) return 'Неизвестный браузер'
  const os = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android' : /Mac OS X/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : ''
  const br = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : /Electron/.test(ua) ? 'Pocket' : 'Браузер'
  return [br, os].filter(Boolean).join(' · ')
}

function Passkeys({ me }: { me?: MeOut }) {
  const qc = useQueryClient()
  const { tz, today } = useToday()
  const q = useQuery({ queryKey: qk.passkeys, queryFn: () => api.get<PasskeyOut[]>('/passkeys') })
  const [name, setName] = useState(guessDeviceName())
  const supported = typeof window !== 'undefined' && browserSupportsWebAuthn()
  // no password first (a fresh session passes); 403 reauth_required → the password dialog
  const add = usePasskeyAdd({
    onDone: (p) => notify.info(`Face ID / ключ «${p.name}» добавлен — можно входить без пароля`),
    onError: (e) => {
      const n = (e as Error)?.name
      if (n === 'NotAllowedError' || n === 'AbortError') notify.info('Добавление отменено')
      else if (n === 'InvalidStateError') notify.error('Этот ключ уже добавлен')
      else if (e instanceof ApiError && e.code === 'gone') notify.error('Время ожидания вышло, попробуйте ещё раз')
      else notify.error(errorMessage(e, 'Не удалось добавить ключ'))
    },
  })
  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/passkeys/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.passkeys })
      void qc.invalidateQueries({ queryKey: qk.me })
    },
    onError: (e) => notify.error(errorMessage(e, 'Не удалось удалить ключ')),
  })
  const canRegister = !me || me.auth === 'session'
  return (
    <SettingsSection title="Face ID и ключи входа" description="Вход без пароля на iPhone, Mac и Windows Hello.">
      {q.isLoading ? (
        <Skeleton className="h-16" />
      ) : (
        <ul className="mb-3">
          {(q.data ?? []).map((p) => (
            <li key={p.id} className="flex min-h-11 items-center gap-3 border-b border-border py-1 last:border-0">
              <ScanFace className="size-4 text-muted-foreground" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{p.name}</span>
                <span className="block text-xs text-muted-foreground">
                  добавлен {formatDateTime(p.created_at, tz, today)}
                  {p.last_used_at ? ` · вход ${formatDateTime(p.last_used_at, tz, today)}` : ''}
                </span>
              </span>
              <Button size="sm" variant="ghost" className="text-destructive" aria-label={`Удалить ключ ${p.name}`} loading={del.isPending && del.variables === p.id} onClick={() => del.mutate(p.id)}>
                <Trash2 /> Удалить
              </Button>
            </li>
          ))}
          {q.data?.length === 0 && <li className="text-sm text-muted-foreground">Ключей нет — вход только по паролю</li>}
        </ul>
      )}
      {!supported ? (
        <p className="text-sm text-muted-foreground">Этот браузер не поддерживает Face ID / ключи входа.</p>
      ) : !canRegister ? (
        <p className="text-sm text-muted-foreground">Добавить ключ можно из браузера (веб-версия или PWA), не из приложения для Windows.</p>
      ) : (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); add.start(name.trim() || guessDeviceName()) }}>
          <Field label="Название" htmlFor="pk-name">
            <Input id="pk-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} className="w-48" />
          </Field>
          <Button type="submit" variant="primary" loading={add.busy}>
            <ScanFace /> Включить Face ID
          </Button>
        </form>
      )}
      {add.dialog}
    </SettingsSection>
  )
}

/** «Удалить ключи входа (passkey)» — on by default for «Сменить пароль» and «Выйти со всех устройств». */
function RemovePasskeysCheckbox({ id, checked, onChange }: { id: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm">
      <Checkbox id={id} checked={checked} onCheckedChange={onChange} label="Удалить ключи входа (passkey)" /> Удалить ключи входа (passkey)
    </label>
  )
}

function passkeysGone(qc: ReturnType<typeof useQueryClient>) {
  forgetDevicePasskey()
  void qc.invalidateQueries({ queryKey: qk.passkeys })
  void qc.invalidateQueries({ queryKey: qk.me })
}

function PasswordForm() {
  const qc = useQueryClient()
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [revoke, setRevoke] = useState(true)
  const [removeKeys, setRemoveKeys] = useState(true)
  const m = useMutation({
    mutationFn: () => usersApi.changePassword({ current_password: cur, new_password: next, revoke_other_sessions: revoke, remove_passkeys: removeKeys }),
    onSuccess: () => {
      setCur('')
      setNext('')
      setAgain('')
      if (removeKeys) passkeysGone(qc)
      const parts = ['Пароль изменён', revoke && 'другие сессии завершены', removeKeys && 'ключи входа удалены'].filter(Boolean)
      notify.info(parts.join(', '))
    },
  })
  const err = next && next.length < 12 ? 'Минимум 12 символов' : again && again !== next ? 'Пароли не совпадают' : null
  const apiErr = m.error ? (m.error instanceof ApiError && m.error.status === 401 ? 'Неверный текущий пароль' : errorMessage(m.error)) : null
  return (
    <SettingsSection title="Пароль">
      <form className="grid gap-3 md:max-w-sm" onSubmit={(e) => { e.preventDefault(); if (!err && cur && next && next === again) m.mutate() }}>
        <Field label="Текущий пароль" htmlFor="pw-cur">
          <Input id="pw-cur" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
        </Field>
        <Field label="Новый пароль" htmlFor="pw-new" hint="не короче 12 символов">
          <Input id="pw-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label="Ещё раз" htmlFor="pw-again" error={err ?? apiErr}>
          <Input id="pw-again" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
        </Field>
        <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm">
          <Checkbox checked={revoke} onCheckedChange={setRevoke} label="Завершить другие сессии и устройства" /> Завершить другие сессии и устройства
        </label>
        <RemovePasskeysCheckbox id="pw-remove-passkeys" checked={removeKeys} onChange={setRemoveKeys} />
        <Button type="submit" variant="primary" disabled={!cur || !next || next !== again || !!err} loading={m.isPending}>
          <KeyRound /> Сменить пароль
        </Button>
      </form>
    </SettingsSection>
  )
}

function SessionsAndDevices({ me }: { me?: MeOut }) {
  const qc = useQueryClient()
  const { tz, today } = useToday()
  const isSession = !me || me.auth === 'session'
  const sessions = useQuery({ queryKey: qk.sessions, queryFn: () => api.get<SessionOut[]>('/auth/sessions'), enabled: isSession })
  const devices = useQuery({ queryKey: qk.devices, queryFn: () => api.get<DeviceOut[]>('/devices') })
  const revokeSession = useMutation({ mutationFn: (id: string) => api.delete(`/auth/sessions/${id}`), onSuccess: () => void qc.invalidateQueries({ queryKey: qk.sessions }), onError: (e) => notify.error(errorMessage(e)) })
  const [removeKeys, setRemoveKeys] = useState(true)
  const logoutAll = useMutation({
    // Keep this session; revoke every other web session and every Electron device token
    // (and, unless unticked, every passkey — a key added from a stolen session must not survive).
    mutationFn: () => authApi.logoutAll({ keepCurrent: true, removePasskeys: removeKeys }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: qk.sessions })
      void qc.invalidateQueries({ queryKey: qk.devices })
      if (removeKeys) passkeysGone(qc)
      const keys = removeKeys && r.passkeys_removed !== undefined ? `, ключей ${r.passkeys_removed}` : ''
      notify.info(`Выход выполнен: сессий ${r.sessions_revoked}, устройств ${r.devices_revoked}${keys}`)
    },
    onError: (e) => notify.error(errorMessage(e)),
  })
  const revokeDevice = useMutation({
    mutationFn: (id: string) => api.delete(`/devices/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.devices })
      notify.info('Устройство отозвано — его токен больше не работает')
    },
    onError: (e) => notify.error(errorMessage(e)),
  })
  return (
    <SettingsSection title="Сессии и устройства" description="Отзыв срабатывает сразу: устройство попросит войти заново.">
      <h3 className="mb-1 text-[13px] font-semibold text-muted-foreground">Приложение для Windows</h3>
      {devices.isLoading ? (
        <Skeleton className="h-12" />
      ) : devices.data?.length ? (
        <ul className="mb-4">
          {devices.data.map((d) => (
            <li key={d.id} className="flex min-h-11 items-center gap-3 border-b border-border py-1 last:border-0">
              <Monitor className="size-4 text-muted-foreground" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">
                  {d.name} {d.is_current && <Badge tone="primary">это устройство</Badge>}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {d.platform} · вход {formatDateTime(d.created_at, tz, today)}
                  {d.last_used_at ? ` · активность ${formatDateTime(d.last_used_at, tz, today)}` : ''}
                </span>
              </span>
              <Button size="sm" variant="ghost" className="text-destructive" loading={revokeDevice.isPending && revokeDevice.variables === d.id} onClick={() => revokeDevice.mutate(d.id)}>
                Отозвать
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mb-4 text-sm text-muted-foreground">Ни одно устройство не подключено.</p>
      )}
      {isSession && (
        <>
          <h3 className="mb-1 text-[13px] font-semibold text-muted-foreground">Браузеры</h3>
          {sessions.isLoading ? (
            <Skeleton className="h-12" />
          ) : (
            <ul>
              {(sessions.data ?? []).map((s) => {
                const mobile = /iPhone|Android|iPad/.test(s.user_agent ?? '')
                const Icon = mobile ? Smartphone : Laptop
                return (
                  <li key={s.id} className="flex min-h-11 items-center gap-3 border-b border-border py-1 last:border-0">
                    <Icon className="size-4 text-muted-foreground" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {uaLabel(s.user_agent)} {s.is_current && <Badge tone="primary">эта сессия</Badge>}
                      </span>
                      <span className="block text-xs text-muted-foreground">
                        {s.ip ?? '—'} · активность {formatDateTime(s.last_seen_at, tz, today)}
                      </span>
                    </span>
                    {!s.is_current && (
                      <Button size="sm" variant="ghost" className="text-destructive" loading={revokeSession.isPending && revokeSession.variables === s.id} onClick={() => revokeSession.mutate(s.id)}>
                        Отозвать
                      </Button>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </>
      )}
      <div className="mt-4 grid justify-items-start gap-1">
        <RemovePasskeysCheckbox id="logout-all-remove-passkeys" checked={removeKeys} onChange={setRemoveKeys} />
        <Button variant="outline" className="text-destructive" loading={logoutAll.isPending} onClick={() => logoutAll.mutate()}>
          Выйти со всех устройств
        </Button>
      </div>
    </SettingsSection>
  )
}

/** «Выйти»: end only this session (web/PWA; in the Windows app — this device). */
function SignOut() {
  const { signOut, busy } = useSignOut()
  return (
    <SettingsSection title="Выход" description="Завершить сессию на этом устройстве. Остальные устройства останутся в системе.">
      <Button variant="outline" loading={busy} onClick={() => void signOut()} data-testid="settings-logout">
        <LogOut /> Выйти
      </Button>
    </SettingsSection>
  )
}

export function SecuritySection() {
  const me = useQuery({ queryKey: qk.me, queryFn: () => api.get<MeOut>('/auth/me'), staleTime: 60_000 })
  return (
    <div className="grid gap-4">
      <Passkeys me={me.data} />
      <PasswordForm />
      <SessionsAndDevices me={me.data} />
      <SignOut />
    </div>
  )
}

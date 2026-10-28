// Login (DESIGN §10.8): A — Face ID first (device has a passkey), B — password.
// One password field (single user), eye toggle, «Неверный пароль», 429 countdown, offline → disabled.
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { CornerDownLeft, Eye, EyeOff, ScanFace } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { LogoMark } from '@/components/pf/Logo'
import { Spinner } from '@/components/pf/Spinner'
import { ApiError, apiBaseUrl, errorText } from '@/lib/api/client'
import type { MeOut } from '@/lib/api/types'
import { useConnection } from '@/lib/connection'
import { cn } from '@/lib/utils'
import { deviceHasPasskey, loginWithPasskey, loginWithPassword, passkeyErrorText, webauthnAvailable } from './session'

interface Props {
  onDone: (me: MeOut) => void
  /** Quick Capture: compact, device-token login, no Face ID */
  variant?: 'page' | 'quick'
  notice?: string | null
}

export function serverHost(): string {
  const base = apiBaseUrl()
  try { return new URL(base, window.location.href).host } catch { return window.location.host }
}

export function LoginForm({ onDone, variant = 'page', notice }: Props) {
  const qc = useQueryClient()
  const conn = useConnection()
  const offline = conn.status === 'offline'
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState<'password' | 'passkey' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [wait, setWait] = useState(0)
  const [webauthn, setWebauthn] = useState({ supported: false, platform: false })
  const [mode, setMode] = useState<'passkey' | 'password'>(() => (variant === 'page' && deviceHasPasskey() ? 'passkey' : 'password'))

  useEffect(() => {
    if (variant === 'quick') return
    let alive = true
    void webauthnAvailable().then((w) => { if (alive) setWebauthn(w) })
    return () => { alive = false }
  }, [variant])

  useEffect(() => {
    if (wait <= 0) return
    const t = setTimeout(() => setWait((w) => w - 1), 1000)
    return () => clearTimeout(t)
  }, [wait])

  const fail = (e: unknown) => {
    if (e instanceof ApiError && e.status === 429) {
      setWait(Math.ceil(e.retryAfter ?? 60))
      setError('Слишком много попыток. Подождите 1 мин')
    } else if (e instanceof ApiError && e.status === 401) {
      setError('Неверный пароль')
    } else {
      setError(errorText(e))
    }
  }

  const submitPassword = async (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!password || busy || wait > 0) return
    setBusy('password')
    setError(null)
    try {
      const me = await loginWithPassword(qc, password)
      onDone(me)
    } catch (err) {
      fail(err)
    } finally {
      setBusy(null)
    }
  }

  const submitPasskey = async () => {
    if (busy) return
    setBusy('passkey')
    setError(null)
    try {
      const me = await loginWithPasskey(qc)
      onDone(me)
    } catch (err) {
      const msg = err instanceof ApiError ? errorText(err) : passkeyErrorText(err)
      if (msg) setError(msg)
    } finally {
      setBusy(null)
    }
  }

  const canPasskey = variant === 'page' && webauthn.supported
  const pwField = (
    <form onSubmit={submitPassword} className="flex w-full flex-col gap-2" data-testid="login-form">
      <div className="relative">
        <Input
          type={show ? 'text' : 'password'}
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(e) => { setPassword(e.target.value); setError(null) }}
          placeholder="Пароль"
          aria-label="Пароль"
          aria-invalid={!!error || undefined}
          data-testid="login-password"
          className={cn('h-11 pr-10 text-input-ai', error && 'border-destructive')}
          disabled={offline}
        />
        <button
          type="button"
          onClick={() => setShow((v) => !v)}
          className="absolute top-1/2 right-2 -translate-y-1/2 rounded-sm p-1 text-muted-foreground hover:text-foreground"
          aria-label={show ? 'Скрыть пароль' : 'Показать пароль'}
        >
          {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </button>
      </div>
      {error && <p className="text-small text-destructive" role="alert" data-testid="login-error">{wait > 0 ? `Слишком много попыток. Подождите ${wait} с` : error}</p>}
      <Button type="submit" className="h-11 w-full" disabled={offline || !password || busy !== null || wait > 0} aria-busy={busy === 'password' || undefined} data-testid="login-submit">
        {busy === 'password' && <Spinner size={14} />} Войти <CornerDownLeft className="opacity-70" />
      </Button>
    </form>
  )

  return (
    <div className={cn('flex w-full flex-col items-center', variant === 'page' ? 'max-w-[360px] gap-6' : 'max-w-[320px] gap-4')}>
      <div className="flex flex-col items-center gap-2">
        <LogoMark size={variant === 'page' ? 48 : 36} />
        <h1 className={variant === 'page' ? 'text-title-1' : 'text-title-2'}>{variant === 'page' ? 'Pocket' : 'Вход в Pocket'}</h1>
        {variant === 'quick' && <p className="text-small text-muted-foreground">{serverHost()}</p>}
      </div>
      {notice && <p className="rounded-md bg-warning-soft px-3 py-2 text-center text-small text-warning">{notice}</p>}
      {mode === 'passkey' && canPasskey ? (
        <div className="flex w-full flex-col items-center gap-3">
          <Button className="h-12 w-full text-body" onClick={submitPasskey} disabled={offline || busy !== null} data-testid="login-passkey">
            {busy === 'passkey' ? <Spinner size={14} /> : <ScanFace className="size-5" />} Войти с Face ID
          </Button>
          {error && <p className="text-small text-destructive" role="alert">{error}</p>}
          <button type="button" className="text-small text-muted-foreground hover:text-foreground" onClick={() => { setMode('password'); setError(null) }}>
            Войти по паролю
          </button>
        </div>
      ) : (
        <>
          {pwField}
          {canPasskey && (
            <button type="button" className="text-small text-muted-foreground hover:text-foreground disabled:opacity-50" onClick={submitPasskey} disabled={offline || busy !== null} data-testid="login-passkey-link">
              Войти с Face ID
            </button>
          )}
        </>
      )}
      {variant === 'quick' && (
        <p className="text-center text-small text-muted-foreground">Пароль нужен один раз — дальше приложение входит само.</p>
      )}
      <p className={cn('flex items-center gap-1.5 text-caption', offline ? 'text-warning' : 'text-muted-foreground')} data-testid="login-status">
        <span className={cn('size-1.5 rounded-full', offline ? 'bg-warning' : conn.status === 'online' ? 'bg-income' : 'pf-pulse bg-warning')} />
        {offline ? 'Нет связи с сервером' : conn.status === 'online' ? `${serverHost()} · на связи` : `${serverHost()} · переподключение…`}
      </p>
    </div>
  )
}

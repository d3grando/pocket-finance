// Adding a passkey (Face ID / Windows Hello / security key) from the web — shared by Settings →
// «Вход и устройства» and the one-time offer on the dashboard. The first attempt goes without a
// password: a session opened a few minutes ago passes (the offer right after a password login).
// An older session gets 403 reauth_required → a password dialog, then the same ceremony again.
// A key added from a stolen cookie session would otherwise outlive «Выйти везде».
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { KeyRound } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/pf/Spinner'
import { ApiError, errorText } from '@/lib/api/client'
import type { PasskeyOut } from '@/lib/api/types'
import { deviceLabel, isReauthRequired, registerPasskey } from './session'

export interface PasskeyAddOptions {
  onDone: (p: PasskeyOut) => void
  /** WebAuthn / API errors outside the password dialog (cancelled ceremony, expired challenge…) */
  onError: (e: unknown) => void
}

export function usePasskeyAdd({ onDone, onError }: PasskeyAddOptions) {
  const qc = useQueryClient()
  const [ask, setAsk] = useState<{ name: string } | null>(null)
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const close = () => {
    setAsk(null)
    setPassword('')
    setError(null)
  }

  const run = async (name: string, pw: string | null) => {
    setBusy(true)
    setError(null)
    try {
      const pk = await registerPasskey(qc, name, pw)
      close()
      onDone(pk)
    } catch (e) {
      if (isReauthRequired(e)) {
        setAsk({ name })
        if (pw !== null) setError('Нужен пароль от Pocket')
      } else if (pw !== null && e instanceof ApiError && e.status === 401) {
        setError('Неверный пароль')
      } else if (pw !== null && e instanceof ApiError && e.status === 429) {
        setError(errorText(e))
      } else {
        close()
        onError(e)
      }
    } finally {
      setBusy(false)
    }
  }

  /** Start adding a key named `name` (no password first). */
  const start = (name = deviceLabel()) => {
    if (!busy) void run(name, null)
  }

  const dialog = (
    <Dialog open={ask !== null} onOpenChange={(open) => { if (!open && !busy) close() }}>
      <DialogContent data-testid="passkey-password-dialog" showCloseButton={false}>
        <form
          className="grid gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (ask && password && !busy) void run(ask.name, password)
          }}
        >
          <DialogHeader>
            <DialogTitle>Подтвердите паролем</DialogTitle>
            <DialogDescription>Чтобы добавить ключ входа (Face ID, Windows Hello), введите пароль от Pocket.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5 text-sm">
            <label htmlFor="passkey-password" className="font-medium">Пароль</label>
            <Input
              id="passkey-password"
              type="password"
              autoComplete="current-password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              maxLength={256}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? 'passkey-password-error' : undefined}
            />
            {error && <span id="passkey-password-error" role="alert" className="text-small text-destructive">{error}</span>}
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={close} disabled={busy}>Отмена</Button>
            <Button type="submit" disabled={!password || busy}>
              {busy ? <Spinner size={14} /> : <KeyRound />} Продолжить
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )

  return { start, busy, dialog }
}

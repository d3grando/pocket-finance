// One-time offer after a password login (DESIGN §10.8): «Входить по Face ID на этом iPhone?»
import { useEffect, useState } from 'react'
import { ScanFace } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/pf/Spinner'
import { deviceHasPasskey, deviceLabel, passkeyErrorText, webauthnAvailable } from '@/features/auth/session'
import { usePasskeyAdd } from '@/features/auth/usePasskeyAdd'
import { useMe } from '@/lib/api/hooks'
import { ApiError } from '@/lib/api/client'
import { readStore, STORE, writeStore } from '@/lib/storage'

export function PasskeyOffer() {
  const me = useMe()
  const [eligible, setEligible] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // right after a password login the session is fresh → no password; later → the password dialog
  const add = usePasskeyAdd({
    onDone: () => setEligible(false),
    onError: (e) => setError(e instanceof ApiError ? e.message : passkeyErrorText(e)),
  })

  useEffect(() => {
    if (me.data?.auth !== 'session' || deviceHasPasskey() || readStore(STORE.passkeyOffer)) return
    let alive = true
    void webauthnAvailable().then((w) => { if (alive) setEligible(w.platform) })
    return () => { alive = false }
  }, [me.data?.auth])

  if (!eligible) return null
  const dismiss = () => { writeStore(STORE.passkeyOffer, 'dismissed'); setEligible(false) }
  const enable = () => {
    setError(null)
    add.start()
  }
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-4" data-testid="passkey-offer">
      <span className="flex size-9 items-center justify-center rounded-full bg-primary-soft text-primary"><ScanFace className="size-5" /></span>
      <div className="min-w-0 flex-1">
        <p className="text-body font-medium">Входить по Face ID на этом {deviceLabel() === 'Это устройство' ? 'устройстве' : deviceLabel()}?</p>
        {error && <p className="text-small text-destructive">{error}</p>}
      </div>
      <Button size="sm" onClick={enable} disabled={add.busy}>{add.busy && <Spinner size={14} />} Включить</Button>
      <Button size="sm" variant="ghost" onClick={dismiss}>Не сейчас</Button>
      {add.dialog}
    </div>
  )
}

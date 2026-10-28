// Вход (DESIGN §10.8): Face ID (passkey) or password; session 90 days (cookie).
import { useEffect } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { LoginForm } from '@/features/auth/LoginForm'
import { useMe } from '@/lib/api/hooks'
import { qk } from '@/lib/api/keys'
import type { MeOut } from '@/lib/api/types'

export default function LoginPage() {
  const nav = useNavigate()
  const loc = useLocation()
  const qc = useQueryClient()
  const state = (loc.state ?? {}) as { from?: string; expired?: boolean }
  const me = useMe({ retry: false })

  useEffect(() => { document.title = 'Вход · Pocket'; return () => { document.title = 'Pocket' } }, [])

  const done = (m: MeOut) => {
    qc.setQueryData(qk.me, m)
    const from = state.from && !state.from.startsWith('/login') ? state.from : '/'
    nav(m.onboarding_done ? from : '/onboarding', { replace: true })
  }

  if (me.data && !state.expired) return <Navigate to={me.data.onboarding_done ? (state.from ?? '/') : '/onboarding'} replace />

  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4 pt-safe pb-safe">
      <LoginForm
        onDone={done}
        notice={state.expired ? 'Сессия закончилась — войдите снова. Набранный текст сохранён' : null}
      />
    </main>
  )
}

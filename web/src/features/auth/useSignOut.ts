// «Выйти» (this session only): POST /auth/logout → the user's data in this browser is forgotten,
// the react-query cache is emptied → /login. Used by the shell (sidebar, mobile bar) and Settings.
import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { logout } from './session'

export function useSignOut() {
  const qc = useQueryClient()
  const nav = useNavigate()
  const [busy, setBusy] = useState(false)
  const signOut = useCallback(async () => {
    setBusy(true)
    try {
      await logout(qc)
    } finally {
      setBusy(false)
    }
    nav('/login', { replace: true })
  }, [qc, nav])
  return { signOut, busy }
}

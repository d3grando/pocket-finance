// ☾ / ☀ quick toggle (full choice incl. «Как в системе» lives in Settings).
import { useQueryClient } from '@tanstack/react-query'
import { Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { settingsApi } from '@/lib/api/endpoints'
import { qk } from '@/lib/api/keys'
import type { SettingsOut } from '@/lib/api/types'
import { useTheme } from '@/lib/theme'

export function ThemeToggle({ className }: { className?: string }) {
  const { resolved, setChoice } = useTheme()
  const qc = useQueryClient()
  const toggle = () => {
    const next = resolved === 'dark' ? 'light' : 'dark'
    setChoice(next)
    // best effort: remember on the server so other devices follow (settings.theme)
    const s = qc.getQueryData<SettingsOut>(qk.settings)
    if (s) settingsApi.patch({ version: s.version, theme: next }).then((out) => qc.setQueryData(qk.settings, out)).catch(() => {})
  }
  return (
    <Button type="button" variant="ghost" size="icon-sm" onClick={toggle} className={className} aria-label={resolved === 'dark' ? 'Светлая тема' : 'Тёмная тема'} data-testid="theme-toggle">
      {resolved === 'dark' ? <Sun /> : <Moon />}
    </Button>
  )
}

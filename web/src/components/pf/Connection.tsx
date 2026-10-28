// Connection indicator + offline banner (DESIGN §8.4).
import { RefreshCw, Wifi, WifiOff } from 'lucide-react'
import { useConnection, type ConnectionStatus } from '@/lib/connection'
import { formatTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

const LABEL: Record<ConnectionStatus, string> = {
  online: 'На связи',
  reconnecting: 'Переподключение…',
  offline: 'Нет связи',
}

export function syncedLabel(lastOkAt: number | null): string {
  return lastOkAt ? formatTime(new Date(lastOkAt).toISOString()) : '—'
}

export function ConnectionDot({ withLabel = false, className }: { withLabel?: boolean; className?: string }) {
  const c = useConnection()
  const tip = c.status === 'online' ? `На связи · синхронизировано ${syncedLabel(c.lastOkAt)}` : c.status === 'offline' ? `Нет связи · данные на ${syncedLabel(c.lastOkAt)}` : 'Переподключение…'
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn('inline-flex items-center gap-1.5 text-caption text-muted-foreground', className)}
          data-testid="connection-dot"
          data-status={c.status}
          role="status"
          aria-label={tip}
          tabIndex={0}
        >
          <span
            className={cn(
              'size-1.5 rounded-full',
              c.status === 'online' && 'bg-income',
              c.status === 'reconnecting' && 'pf-pulse bg-warning',
              c.status === 'offline' && 'bg-warning',
            )}
          />
          {withLabel && <span className={cn(c.status !== 'online' && 'text-warning')}>{LABEL[c.status]}</span>}
        </span>
      </TooltipTrigger>
      <TooltipContent>{tip}</TooltipContent>
    </Tooltip>
  )
}

export function OfflineBanner({ className, onCheck }: { className?: string; onCheck?: () => void }) {
  const c = useConnection()
  if (c.status === 'offline') {
    return (
      <div
        role="alert"
        data-testid="offline-banner"
        className={cn('z-[70] flex items-center gap-2 bg-warning-soft px-4 py-2 text-small text-warning', className)}
      >
        <WifiOff className="size-4 shrink-0" />
        <span className="flex min-w-0 flex-1 flex-col sm:flex-row sm:items-center sm:gap-3">
          <span className="sm:flex-1">Нет связи с сервером. Записи не сохраняются.</span>
          {c.lastOkAt && <span className="num text-caption sm:text-small" data-testid="offline-stale">данные на {syncedLabel(c.lastOkAt)}</span>}
        </span>
        {onCheck && (
          <button type="button" onClick={onCheck} className="inline-flex items-center gap-1 rounded-sm px-2 py-0.5 font-medium hover:bg-warning/10">
            <RefreshCw className="size-3.5" /> Проверить
          </button>
        )}
      </div>
    )
  }
  if (c.justRecovered) {
    return (
      <div role="status" className={cn('z-[70] flex items-center gap-2 bg-income-soft px-4 py-2 text-small text-income', className)}>
        <Wifi className="size-4" /> Снова на связи
      </div>
    )
  }
  return null
}

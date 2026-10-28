// 12–14 px arc spinner (DESIGN §7.2.1, §8.1).
import { cn } from '@/lib/utils'

export function Spinner({ size = 12, className, label = 'Загрузка' }: { size?: number; className?: string; label?: string }) {
  return (
    <svg
      role="status"
      aria-label={label}
      viewBox="0 0 16 16"
      width={size}
      height={size}
      className={cn('animate-spin text-current', className)}
    >
      <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeOpacity="0.2" strokeWidth="2" />
      <path d="M14.5 8A6.5 6.5 0 0 0 8 1.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

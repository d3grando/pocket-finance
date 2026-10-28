// Pocket mark (DESIGN §13.2) inline SVG + wordmark.
import { useId } from 'react'
import { cn } from '@/lib/utils'

export function LogoMark({ size = 24, className, offline = false }: { size?: number; className?: string; offline?: boolean }) {
  const id = useId().replace(/:/g, '')
  return (
    <svg viewBox="0 0 512 512" width={size} height={size} className={cn('shrink-0', className)} aria-hidden>
      <defs>
        <linearGradient id={`${id}bg`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6E60EE" />
          <stop offset="1" stopColor="#4A3BC6" />
        </linearGradient>
      </defs>
      <rect width="512" height="512" rx="116" fill={`url(#${id}bg)`} />
      <g transform="translate(-22 -16)" fill="none" stroke="#FFFFFF" strokeWidth="64" strokeLinecap="round" strokeLinejoin="round">
        <path d="M196 160 V352" />
        <path d="M196 160 H284 A72 72 0 0 1 284 304 H196" />
      </g>
      <g transform="translate(-22 -16)">
        <circle cx="344" cy="384" r="32" fill={offline ? '#8992A5' : '#F5C451'} />
        <circle cx="344" cy="384" r="17" fill="none" stroke={offline ? '#6b7385' : '#D9A12E'} strokeWidth="6" />
      </g>
    </svg>
  )
}

export function Logo({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      <LogoMark size={size} />
      <span className="font-semibold tracking-[-0.02em]" style={{ fontSize: Math.round(size * 0.66) }}>Pocket</span>
    </span>
  )
}

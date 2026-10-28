// Overlays for the pages: popover, dropdown menu, responsive sheet (right panel on desktop, bottom drawer on
// mobile — DESIGN §9.7/§10.4), dialog and the page toaster (sonner with its own id, so it never
// duplicates the shell's global toaster).
import type { ReactNode } from 'react'
import { Dialog as RDialog, DropdownMenu as RMenu, Popover as RPopover } from 'radix-ui'
import { Drawer } from 'vaul'
import { Toaster, toast, type ExternalToast } from 'sonner'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useIsMobile } from './hooks'
import { keepOpenOnToast } from '@/lib/keep-open-on-toast'

// ----------------------------------------------------------------- popover
export function Popover({ trigger, children, open, onOpenChange, align = 'start', className, side }: { trigger: ReactNode; children: ReactNode; open?: boolean; onOpenChange?: (v: boolean) => void; align?: 'start' | 'center' | 'end'; side?: 'top' | 'bottom' | 'left' | 'right'; className?: string }) {
  return (
    <RPopover.Root open={open} onOpenChange={onOpenChange}>
      <RPopover.Trigger asChild>{trigger}</RPopover.Trigger>
      <RPopover.Portal>
        <RPopover.Content
          align={align}
          side={side}
          sideOffset={6}
          collisionPadding={12}
          className={cn('z-40 max-h-[min(70vh,var(--radix-popover-content-available-height))] w-72 overflow-auto rounded-md border border-border bg-popover p-2 text-popover-foreground shadow-md outline-none', 'data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-[0.98]', className)}
        >
          {children}
        </RPopover.Content>
      </RPopover.Portal>
    </RPopover.Root>
  )
}
export const PopoverClose = RPopover.Close

// ----------------------------------------------------------------- dropdown menu
export interface MenuItem {
  label: ReactNode
  icon?: ReactNode
  onSelect: () => void
  danger?: boolean
  disabled?: boolean
  hint?: ReactNode
}

export function Menu({ trigger, items, align = 'end', label }: { trigger: ReactNode; items: (MenuItem | 'sep')[]; align?: 'start' | 'end'; label?: string }) {
  return (
    <RMenu.Root modal={false}>
      <RMenu.Trigger asChild aria-label={label}>
        {trigger}
      </RMenu.Trigger>
      <RMenu.Portal>
        <RMenu.Content align={align} sideOffset={4} collisionPadding={12} className="z-40 min-w-48 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md">
          {items.map((it, i) =>
            it === 'sep' ? (
              <RMenu.Separator key={i} className="my-1 h-px bg-border" />
            ) : (
              <RMenu.Item
                key={i}
                disabled={it.disabled}
                onSelect={it.onSelect}
                className={cn(
                  'flex h-8 cursor-default items-center gap-2 rounded-sm px-2 text-sm outline-none select-none data-[disabled]:opacity-50 data-[highlighted]:bg-accent [&_svg]:size-4 [&_svg]:text-muted-foreground',
                  it.danger && 'text-destructive [&_svg]:text-destructive',
                )}
              >
                {it.icon}
                <span className="flex-1">{it.label}</span>
                {it.hint}
              </RMenu.Item>
            ),
          )}
        </RMenu.Content>
      </RMenu.Portal>
    </RMenu.Root>
  )
}

// ----------------------------------------------------------------- sheet / dialog
export { keepOpenOnToast }

export function Sheet({ open, onOpenChange, title, description, children, footer, width = 420 }: { open: boolean; onOpenChange: (v: boolean) => void; title: ReactNode; description?: ReactNode; children: ReactNode; footer?: ReactNode; width?: number }) {
  const mobile = useIsMobile()
  if (mobile) {
    return (
      <Drawer.Root open={open} onOpenChange={onOpenChange} repositionInputs={false}>
        <Drawer.Portal>
          <Drawer.Overlay className="fixed inset-0 z-50 bg-black/40 dark:bg-black/60" />
          <Drawer.Content className="fixed inset-x-0 bottom-0 z-50 flex max-h-[92dvh] flex-col rounded-t-2xl border-t border-border bg-card shadow-lg outline-none" aria-describedby={undefined} onPointerDownOutside={keepOpenOnToast} onInteractOutside={keepOpenOnToast}>
            <div className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-full bg-muted" aria-hidden />
            <div className="flex items-center justify-between gap-2 px-4 pt-2 pb-1">
              <Drawer.Title className="text-[17px] font-semibold">{title}</Drawer.Title>
            </div>
            {description && <Drawer.Description className="px-4 text-sm text-muted-foreground">{description}</Drawer.Description>}
            <div className="min-h-0 flex-1 overflow-y-auto px-4 pt-2 pb-4">{children}</div>
            {footer && <div className="border-t border-border px-4 pt-3 pb-[max(12px,env(safe-area-inset-bottom))]">{footer}</div>}
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>
    )
  }
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0 dark:bg-black/60" />
        <RDialog.Content
          style={{ width: `min(${width}px, 100vw)` }}
          className="fixed inset-y-0 right-0 z-50 flex flex-col border-l border-border bg-card shadow-lg outline-none data-[state=open]:animate-in data-[state=open]:slide-in-from-right-8 data-[state=open]:fade-in-0"
          aria-describedby={undefined}
          onInteractOutside={keepOpenOnToast}
        >
          <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border px-5">
            <RDialog.Title className="truncate text-[17px] font-semibold">{title}</RDialog.Title>
            <RDialog.Close className="grid size-8 place-items-center rounded-sm text-muted-foreground hover:bg-surface-2 hover:text-foreground" aria-label="Закрыть">
              <X className="size-4" />
            </RDialog.Close>
          </div>
          {description && <RDialog.Description className="px-5 pt-3 text-sm text-muted-foreground">{description}</RDialog.Description>}
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer && <div className="border-t border-border px-5 py-3">{footer}</div>}
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  )
}

export function Dialog({ open, onOpenChange, title, description, children, footer }: { open: boolean; onOpenChange: (v: boolean) => void; title: ReactNode; description?: ReactNode; children?: ReactNode; footer?: ReactNode }) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-50 bg-black/40 dark:bg-black/60" />
        <RDialog.Content className="fixed top-1/2 left-1/2 z-50 flex max-h-[85dvh] w-[min(480px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border bg-card shadow-lg outline-none" aria-describedby={undefined} onInteractOutside={keepOpenOnToast}>
          <div className="flex items-start justify-between gap-2 px-5 pt-4">
            <RDialog.Title className="text-[17px] font-semibold">{title}</RDialog.Title>
            <RDialog.Close className="grid size-8 place-items-center rounded-sm text-muted-foreground hover:bg-surface-2" aria-label="Закрыть">
              <X className="size-4" />
            </RDialog.Close>
          </div>
          {description && <RDialog.Description className="px-5 pt-1 text-sm text-muted-foreground">{description}</RDialog.Description>}
          {children && <div className="min-h-0 overflow-y-auto px-5 py-4">{children}</div>}
          {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-border px-5 py-3">{footer}</div>}
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  )
}

// ----------------------------------------------------------------- toasts
export const TOASTER_ID = 'web-b'

/** One per page. Bottom-center on mobile (above the nav), bottom-right on desktop; 6 s; pause on hover. */
export function PageToaster() {
  const mobile = useIsMobile()
  return <Toaster id={TOASTER_ID} position={mobile ? 'bottom-center' : 'bottom-right'} duration={6000} mobileOffset={{ bottom: 88 }} toastOptions={{ className: 'text-sm' }} />
}

export const notify = {
  info: (msg: ReactNode, o: ExternalToast = {}) => toast(msg, { toasterId: TOASTER_ID, ...o }),
  success: (msg: ReactNode, o: ExternalToast = {}) => toast.success(msg, { toasterId: TOASTER_ID, ...o }),
  error: (msg: ReactNode, o: ExternalToast = {}) => toast.error(msg, { toasterId: TOASTER_ID, ...o }),
}

// Ctrl+K outside Home (DESIGN §9.7): modal 640 px, AI line on top (Enter sends — the
// result card shows right here), below «Перейти» and «Действия». Text that looks like an operation
// gets the first option «Записать: кофе 85к ⏎».
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { CornerDownLeft, Moon, Plus, Sparkles } from 'lucide-react'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Kbd } from '@/components/pf/Kbd'
import { AiCommandBar } from '@/features/assistant/AiCommandBar'
import { useAssistant } from '@/features/assistant/AssistantProvider'
import { CardStack } from '@/features/assistant/CardStack'
import { useTheme } from '@/lib/theme'
import { SIDEBAR_NAV } from './nav'
import { looksLikeOperation } from './palette'

interface Props {
  open: boolean
  onOpenChange: (v: boolean) => void
  onManual: () => void
}

export function CommandPalette({ open, onOpenChange, onManual }: Props) {
  const nav = useNavigate()
  const { toggle } = useTheme()
  const { send } = useAssistant()
  const [text, setText] = useState('')
  const go = (to: string) => { onOpenChange(false); nav(to) }
  const item = 'flex h-9 w-full items-center gap-3 rounded-sm px-2 text-left text-body text-foreground hover:bg-accent focus-visible:bg-accent focus-visible:outline-none'
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="top-[12vh] max-w-[640px] translate-y-0 gap-0 overflow-hidden p-0 shadow-lg" showCloseButton={false} data-testid="command-palette">
        <DialogTitle className="sr-only">Командная палитра</DialogTitle>
        <DialogDescription className="sr-only">Запись операции или переход по разделам</DialogDescription>
        <div className="border-b p-2">
          <AiCommandBar variant="palette" autoFocus primary={false} onValueChange={setText} />
        </div>
        <div className="max-h-[50vh] overflow-y-auto p-2">
          <CardStack max={2} compact className="mb-2 empty:mb-0" />
          {looksLikeOperation(text) && (
            <button type="button" className={item} onClick={() => void send(text.trim())} data-testid="palette-record">
              <Sparkles className="size-4 text-primary" />
              <span className="flex-1 truncate">Записать: {text.trim()}</span>
              <Kbd><CornerDownLeft className="size-3" /></Kbd>
            </button>
          )}
          <div className="px-2 pt-1 pb-1 text-caption text-muted-foreground">Перейти</div>
          {SIDEBAR_NAV.map((n) => (
            <button key={n.to} type="button" className={item} onClick={() => go(n.to)}>
              <n.icon className="size-4 text-muted-foreground" />
              <span className="flex-1">{n.label}</span>
              <Kbd>G {n.key.toUpperCase()}</Kbd>
            </button>
          ))}
          <div className="px-2 pt-2 pb-1 text-caption text-muted-foreground">Действия</div>
          <button type="button" className={item} onClick={() => { onOpenChange(false); onManual() }}>
            <Plus className="size-4 text-muted-foreground" /><span className="flex-1">Новая операция</span><Kbd>N</Kbd>
          </button>
          <button type="button" className={item} onClick={() => toggle()}>
            <Moon className="size-4 text-muted-foreground" /><span className="flex-1">Сменить тему</span>
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

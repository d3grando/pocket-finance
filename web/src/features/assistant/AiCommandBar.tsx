// <AiCommandBar> — DESIGN §9.1. Enter sends, Shift+Enter = newline, ↑ in empty field
// = last sent text, Esc clears / then blurs (Quick Capture: hides the window). The field is never
// blocked while sending; a repeated Enter with the same text is ignored by the KeyBook. Typing while
// the sent text is still in the field (muted, in flight) starts a new phrase instead of gluing onto
// it — a later Enter must never re-send the first operation.
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { CornerDownLeft, Sparkles } from 'lucide-react'
import { Kbd } from '@/components/pf/Kbd'
import { Spinner } from '@/components/pf/Spinner'
import { readStore, STORE, writeStore } from '@/lib/storage'
import { cn } from '@/lib/utils'
import { useAssistant } from './AssistantProvider'
import { registerAiInput } from './aiFocus'
import { cardHotkey, typeOver } from './logic'

export const AI_PLACEHOLDER = 'На что потратил деньги?'

export const DEFAULT_SUGGESTIONS = ['кофе 85к', 'вчера такси 70к', 'сколько потратил на еду за неделю?']

interface Props {
  variant?: 'home' | 'mobile' | 'quick' | 'palette' | 'compact'
  autoFocus?: boolean
  suggestions?: string[]
  /** Esc in an empty field (Quick Capture: hide window) */
  onEscapeEmpty?: () => void
  /** extra keydown handling before the default (return true = handled) */
  onKeyDownExtra?: (e: React.KeyboardEvent<HTMLTextAreaElement>, value: string) => boolean
  className?: string
  /** register as the global Ctrl+K target */
  primary?: boolean
  onSubmitted?: () => void
  /** the current text (command palette: «Записать: …» option) */
  onValueChange?: (value: string) => void
}

const HEIGHT: Record<NonNullable<Props['variant']>, string> = {
  home: 'min-h-[52px] rounded-xl shadow-sm',
  mobile: 'min-h-[56px] rounded-xl shadow-sm',
  quick: 'min-h-[48px] rounded-md',
  palette: 'min-h-[48px] rounded-md border-0 shadow-none',
  compact: 'min-h-[44px] rounded-xl shadow-sm',
}

export function AiCommandBar({
  variant = 'home', autoFocus, suggestions, onEscapeEmpty, onKeyDownExtra, className, primary = true, onSubmitted, onValueChange,
}: Props) {
  const { send, pendingText, hotkey, onSent } = useAssistant()
  const ref = useRef<HTMLTextAreaElement>(null)
  const [value, setValue] = useState(() => readStore(STORE.draft, 'session') ?? '')
  const [focused, setFocused] = useState(false)
  const [showSpinner, setShowSpinner] = useState(false)
  const [shake, setShake] = useState(false)
  const valueRef = useRef(value)
  useLayoutEffect(() => { valueRef.current = value })
  const pendingRef = useRef<string | null>(null)
  useLayoutEffect(() => { pendingRef.current = pendingText })

  const onValueChangeRef = useRef(onValueChange)
  useLayoutEffect(() => { onValueChangeRef.current = onValueChange })
  useEffect(() => { onValueChangeRef.current?.(value) }, [value])

  // restored draft is consumed once
  useEffect(() => { writeStore(STORE.draft, null, 'session') }, [])

  useEffect(() => (primary ? registerAiInput(ref) : undefined), [primary])

  useEffect(() => {
    if (autoFocus) ref.current?.focus({ preventScroll: variant === 'mobile' })
  }, [autoFocus, variant])

  // spinner only if the answer takes longer than 150 ms (DESIGN §7.2.1)
  const pending = pendingText !== null
  useEffect(() => {
    if (!pending) return
    const t = setTimeout(() => setShowSpinner(true), 150)
    return () => { clearTimeout(t); setShowSpinner(false) }
  }, [pending])

  // JS autosize fallback (≤ 4 lines) where field-sizing is not supported
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || (typeof CSS !== 'undefined' && CSS.supports?.('field-sizing', 'content'))) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 4 * 24 + 24)}px`
  }, [value])

  // clear the field when *its* text was sent successfully (also for card [Повторить])
  useEffect(() => onSent((text, outcome) => {
    const cur = valueRef.current.trim()
    if (outcome === 'cleared' && cur === text) setValue('')
    if (outcome === 'keep' && cur === text) {
      requestAnimationFrame(() => ref.current?.select())
    }
  }), [onSent])

  const submit = async () => {
    const text = value.trim()
    if (!text) return
    writeStore(STORE.lastSent, text)
    onSubmitted?.()
    const outcome = await send(text)
    if (outcome === 'keep') {
      setShake(true)
      setTimeout(() => setShake(false), 1200)
    }
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return
    if (onKeyDownExtra?.(e, value)) return
    const empty = value.trim() === ''
    const cmd = cardHotkey(e, empty)
    if (cmd && hotkey(cmd)) { e.preventDefault(); return }
    if (e.key === 'Enter' && !e.shiftKey && !e.altKey) {
      e.preventDefault()
      void submit()
      return
    }
    if (e.key === 'ArrowUp' && empty) {
      const last = readStore(STORE.lastSent)
      if (last) { e.preventDefault(); setValue(last) }
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      if (!empty) setValue('')
      else if (onEscapeEmpty) onEscapeEmpty()
      else ref.current?.blur()
    }
  }

  const sendingThis = pending && pendingText === value.trim()
  return (
    <div className={cn('w-full', className)}>
      <div
        className={cn(
          'relative flex items-start gap-2 border border-input bg-card px-3 transition-[border-color,box-shadow] duration-(--dur-fast)',
          'focus-within:border-ring focus-within:shadow-[var(--shadow-focus)]',
          HEIGHT[variant],
          shake && 'pf-shake border-destructive',
        )}
        onClick={() => ref.current?.focus()}
      >
        <Sparkles className="mt-[15px] size-[18px] shrink-0 text-primary" strokeWidth={1.75} aria-hidden />
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => setValue(typeOver(valueRef.current, e.target.value, pendingRef.current))}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          rows={1}
          enterKeyHint="send"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          aria-label="Запись или вопрос"
          data-testid="ai-input"
          placeholder={AI_PLACEHOLDER}
          className={cn(
            'pf-autosize max-h-[120px] min-h-[24px] flex-1 resize-none bg-transparent py-3 text-input-ai outline-none placeholder:text-muted-foreground',
            variant === 'quick' || variant === 'palette' ? 'py-3' : variant === 'mobile' ? 'py-4' : 'py-3.5',
            sendingThis && 'text-muted-foreground',
          )}
        />
        <div className="mt-2.5 flex shrink-0 items-center gap-2 self-start">
          {!focused && !value && variant !== 'mobile' && variant !== 'quick' && (
            <Kbd className="mt-1.5">Ctrl K</Kbd>
          )}
          {(value.trim() || pending) && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); void submit() }}
              aria-label="Отправить"
              aria-busy={pending || undefined}
              data-testid="ai-submit"
              className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground transition-transform duration-(--dur-instant) active:scale-[0.98] focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              {showSpinner ? <Spinner size={14} /> : <CornerDownLeft className="size-4" />}
            </button>
          )}
        </div>
      </div>
      {suggestions && suggestions.length > 0 && !value && (
        <div className="mt-2 flex gap-1.5 overflow-x-auto px-1 pb-0.5 [scrollbar-width:none]">
          {suggestions.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => { setValue(s); ref.current?.focus() }}
              className="shrink-0 rounded-full px-2 py-0.5 text-small text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

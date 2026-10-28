// Global «Ctrl+K → AI line» target registry (DESIGN §11). The most recently mounted
// primary AiCommandBar wins; when none is mounted the shell opens the command palette instead.
import type { RefObject } from 'react'

const stack: RefObject<HTMLTextAreaElement | null>[] = []

export function registerAiInput(ref: RefObject<HTMLTextAreaElement | null>): () => void {
  stack.push(ref)
  return () => {
    const i = stack.lastIndexOf(ref)
    if (i >= 0) stack.splice(i, 1)
  }
}

/** Focus the visible AI line; false when there is none (caller opens the palette). */
export function focusAiInput(text?: string): boolean {
  for (let i = stack.length - 1; i >= 0; i--) {
    const el = stack[i].current
    if (el && el.offsetParent !== null) {
      el.focus()
      if (text !== undefined) {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
        setter?.call(el, text)
        el.dispatchEvent(new Event('input', { bubbles: true }))
      }
      el.scrollIntoView({ block: 'nearest' })
      return true
    }
  }
  return false
}

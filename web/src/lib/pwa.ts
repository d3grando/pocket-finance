// PWA update flow (registerType 'prompt'): a new build never reloads the open app under
// the user's fingers. It offers «Доступна новая версия · Обновить», and applies itself only when it is
// harmless — the app went to the background with nothing typed and no dialog open. Before any reload
// the AI-line draft goes to sessionStorage (restored by <AiCommandBar>).
import { toast } from 'sonner'
import { STORE, writeStore } from './storage'

/** Nothing would be lost by a reload right now (no draft in the AI line, no dialog / sheet open). */
export function safeToReload(doc: Document = document): boolean {
  const draft = doc.querySelector<HTMLTextAreaElement>('[data-testid="ai-input"]')?.value ?? ''
  if (draft.trim()) return false
  return !doc.querySelector('[role="dialog"],[role="alertdialog"],[data-vaul-drawer]')
}

/** Keep the AI-line draft across the reload. */
export function saveDraft(doc: Document = document) {
  const draft = doc.querySelector<HTMLTextAreaElement>('[data-testid="ai-input"]')?.value
  if (draft?.trim()) writeStore(STORE.draft, draft, 'session')
}

let offered = false

export function offerUpdate(apply: () => Promise<void> | void) {
  if (offered) return
  offered = true
  const run = () => { saveDraft(); void apply() }
  const onHidden = () => {
    if (document.visibilityState === 'hidden' && safeToReload()) {
      document.removeEventListener('visibilitychange', onHidden)
      run()
    }
  }
  document.addEventListener('visibilitychange', onHidden)
  toast('Доступна новая версия Pocket', {
    id: 'pf-update',
    duration: Infinity,
    description: 'Обновится сама, когда приложение будет в фоне',
    action: { label: 'Обновить', onClick: run },
  })
}

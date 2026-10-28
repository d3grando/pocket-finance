/** A click / tap on a toast (e.g. «Отменить» after an operation made inside a modal) is not «outside»:
 *  the modal stays open and the toast button gets the click (globals.css re-enables pointer events on
 *  visible toasts). Used by features/operations/ui/overlays.tsx and the shadcn modals in components/ui. */
export function keepOpenOnToast(e: { target: EventTarget | null; preventDefault: () => void }) {
  const t = e.target
  if (t instanceof Element && t.closest('[data-sonner-toaster]')) e.preventDefault()
}

type OutsideEvent = { target: EventTarget | null; preventDefault: () => void }

/** Wraps a Radix / vaul `on…Outside` prop: the toast guard first, then the caller's own handler. */
export function guardToast<E extends OutsideEvent>(own?: (e: E) => void) {
  return (e: E) => {
    keepOpenOnToast(e)
    own?.(e)
  }
}

// Show/hide rules of the popup (pure, unit-tested; the Electron wiring is in popup.ts).
//  - tray click: toggle; a click that itself caused the blur-hide must not re-open the popup
//    (on Windows mousedown on the taskbar blurs the popup before the tray 'click' arrives);
//  - hotkey: hidden → show; visible+focused → hide; visible but not focused (pinned) → focus;
//  - blur: hide unless pinned (login screen, needs-account choice, bulk preview, open dialog);
//  - Esc: the page gets it first (DESIGN §11: close the top layer — menu, dialog, preview — and
//    clear the field before hiding). Main never swallows the key: the preload reports whether the
//    page consumed it (defaultPrevented) and main hides only for an Esc the page left alone, and
//    only if the popup was not pinned when the key was pressed. A page that never answers (hung,
//    not loaded) still gets hidden after ESC_PAGE_TIMEOUT_MS. Verdicts are matched to keys in
//    order; keys whose verdict no longer matters (timed out, popup shown again) stay in the queue
//    as void entries so that a late verdict is not taken for a newer key's.

export interface WindowPort {
  isVisible(): boolean
  isFocused(): boolean
  show(): void
  hide(): void
  focus(): void
}

export type ToggleOutcome = 'shown' | 'hidden' | 'focused' | 'suppressed' | 'ignored'

/** Time window in which a tray click right after a blur-hide is treated as "close". */
export const BLUR_CLICK_GRACE_MS = 350

/** How long main waits for the page's verdict on an Esc before hiding anyway. */
export const ESC_PAGE_TIMEOUT_MS = 500

/** A void Esc entry still waiting for its (late) verdict is dropped after this long. */
export const ESC_VOID_TTL_MS = 3000

interface EscEntry {
  /** the popup was pinned when the key was pressed → the Esc belongs to the page */
  pinned: boolean
  /** the verdict no longer matters (timed out / popup shown again): consume it, do nothing */
  void: boolean
  at: number
}

/** setTimeout-like scheduler returning a cancel function (injected in unit tests). */
export type Scheduler = (fn: () => void, ms: number) => () => void

const realScheduler: Scheduler = (fn, ms) => {
  const t = setTimeout(fn, ms)
  return () => clearTimeout(t)
}

export class VisibilityController {
  private rendererPinned = false
  private systemPinned = false
  private lastBlurHideAt = -Infinity
  /** Esc keys waiting for the page's verdict, oldest first. */
  private escQueue: EscEntry[] = []
  private cancelEscTimer: (() => void) | null = null

  constructor(
    private readonly win: WindowPort,
    private readonly now: () => number = () => Date.now(),
    private readonly schedule: Scheduler = realScheduler,
  ) {}

  /** Pinned by the page: a dialog / choice / preview is open. */
  setRendererPinned(pinned: boolean): void {
    this.rendererPinned = pinned
  }

  /** Pinned by main: the login screen is shown, or DevTools are open. */
  setSystemPinned(pinned: boolean): void {
    this.systemPinned = pinned
  }

  get pinned(): boolean {
    return this.rendererPinned || this.systemPinned
  }

  show(): ToggleOutcome {
    if (this.win.isVisible()) {
      this.win.focus()
      return 'focused'
    }
    // a fresh show: verdicts for Esc keys pressed before it must not hide the popup again
    this.voidEscapes()
    this.win.show()
    return 'shown'
  }

  hide(): ToggleOutcome {
    if (!this.win.isVisible()) return 'ignored'
    this.win.hide()
    return 'hidden'
  }

  onTrayClick(): ToggleOutcome {
    if (this.now() - this.lastBlurHideAt < BLUR_CLICK_GRACE_MS) {
      this.lastBlurHideAt = -Infinity
      return 'suppressed'
    }
    if (this.win.isVisible()) return this.hide()
    return this.show()
  }

  onHotkey(): ToggleOutcome {
    if (!this.win.isVisible()) return this.show()
    if (this.win.isFocused()) return this.hide()
    this.win.focus()
    return 'focused'
  }

  onBlur(): ToggleOutcome {
    if (this.pinned || !this.win.isVisible()) return 'ignored'
    this.win.hide()
    this.lastBlurHideAt = this.now()
    return 'hidden'
  }

  /**
   * A real Esc key press reached the popup (before-input-event). It is never swallowed: the page
   * handles it first and the preload reports back through onPageEscape().
   */
  onEscapeKey(): void {
    if (!this.win.isVisible()) return
    this.dropExpired()
    this.escQueue.push({ pinned: this.pinned, void: false, at: this.now() })
    if (!this.cancelEscTimer) this.armEscTimer()
  }

  /**
   * The page finished dispatching an Esc; `handled` — it consumed the key (closed its menu /
   * dialog / preview, cleared the field). Hides the popup for an Esc the page left alone.
   */
  onPageEscape(handled: boolean): ToggleOutcome {
    this.dropExpired()
    const entry = this.escQueue.shift()
    if (!entry) return 'ignored' // synthetic, or a key we never saw
    this.stopEscTimer()
    if (this.escQueue.some((e) => !e.void)) this.armEscTimer() // the next key gets a full timeout
    if (entry.void || handled || entry.pinned || this.pinned) return 'ignored'
    return this.hide()
  }

  /** Esc keys whose verdict still matters (diagnostics / tests). */
  get pendingEscapes(): number {
    return this.escQueue.filter((e) => !e.void).length
  }

  private armEscTimer(): void {
    this.cancelEscTimer = this.schedule(() => {
      this.cancelEscTimer = null
      const unpinned = this.escQueue.some((e) => !e.void && !e.pinned)
      this.voidEscapes()
      // the page did not answer (hung / not loaded): an Esc on an unpinned popup still hides it
      if (unpinned && !this.pinned) this.hide()
    }, ESC_PAGE_TIMEOUT_MS)
  }

  private stopEscTimer(): void {
    this.cancelEscTimer?.()
    this.cancelEscTimer = null
  }

  /** Keep the entries (a late verdict must still find its own key), but act on none of them. */
  private voidEscapes(): void {
    this.stopEscTimer()
    for (const e of this.escQueue) e.void = true
  }

  /** A void key whose verdict never came (lost during a page load) must not shift the queue forever. */
  private dropExpired(): void {
    const t = this.now()
    while (this.escQueue.length && this.escQueue[0].void && t - this.escQueue[0].at > ESC_VOID_TTL_MS) this.escQueue.shift()
  }
}

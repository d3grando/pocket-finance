import { describe, expect, it } from 'vitest'
import { BLUR_CLICK_GRACE_MS, ESC_PAGE_TIMEOUT_MS, ESC_VOID_TTL_MS, VisibilityController, type Scheduler, type WindowPort } from '../../src/main/visibility'

function fakeWindow() {
  const s = { visible: false, focused: false, shows: 0, hides: 0, focuses: 0 }
  const port: WindowPort = {
    isVisible: () => s.visible,
    isFocused: () => s.focused,
    show: () => {
      s.visible = true
      s.focused = true
      s.shows++
    },
    hide: () => {
      s.visible = false
      s.focused = false
      s.hides++
    },
    focus: () => {
      s.focused = true
      s.focuses++
    },
  }
  return { s, port }
}

function setup() {
  const { s, port } = fakeWindow()
  let t = 1000
  // fake scheduler: timers fire when advance() passes their deadline
  let timers: { at: number; fn: () => void; live: boolean }[] = []
  const schedule: Scheduler = (fn, ms) => {
    const timer = { at: t + ms, fn, live: true }
    timers.push(timer)
    return () => {
      timer.live = false
    }
  }
  const vc = new VisibilityController(port, () => t, schedule)
  const advance = (ms: number) => {
    t += ms
    const due = timers.filter((x) => x.live && x.at <= t)
    timers = timers.filter((x) => x.live && x.at > t)
    for (const x of due) x.fn()
    return t
  }
  const liveTimers = () => timers.filter((x) => x.live).length
  return { s, vc, advance, liveTimers }
}

describe('VisibilityController', () => {
  it('hotkey toggles: show, then hide', () => {
    const { s, vc } = setup()
    expect(vc.onHotkey()).toBe('shown')
    expect(s.visible).toBe(true)
    expect(vc.onHotkey()).toBe('hidden')
    expect(s.visible).toBe(false)
  })

  it('hotkey on a visible but unfocused (pinned) popup focuses it instead of hiding', () => {
    const { s, vc } = setup()
    vc.onHotkey()
    s.focused = false
    expect(vc.onHotkey()).toBe('focused')
    expect(s.visible).toBe(true)
  })

  it('blur hides unless pinned', () => {
    const { s, vc } = setup()
    vc.show()
    expect(vc.onBlur()).toBe('hidden')
    vc.show()
    vc.setRendererPinned(true)
    expect(vc.onBlur()).toBe('ignored')
    expect(s.visible).toBe(true)
    vc.setRendererPinned(false)
    vc.setSystemPinned(true) // login screen
    expect(vc.onBlur()).toBe('ignored')
    expect(vc.pinned).toBe(true)
  })

  it('Esc is handed to the page first: an Esc the page leaves alone hides the popup', () => {
    const { s, vc, liveTimers } = setup()
    vc.show()
    vc.onEscapeKey()
    expect(s.visible).toBe(true) // main does not hide on the key itself
    expect(vc.pendingEscapes).toBe(1)
    expect(vc.onPageEscape(false)).toBe('hidden')
    expect(s.visible).toBe(false)
    expect(vc.pendingEscapes).toBe(0)
    expect(liveTimers()).toBe(0)
  })

  it('Esc consumed by the page (menu closed, field cleared) keeps the popup open; the next one hides', () => {
    const { s, vc, advance } = setup()
    vc.show()
    vc.onEscapeKey() // ⋯ menu open → the page closes it and calls preventDefault
    expect(vc.onPageEscape(true)).toBe('ignored')
    advance(ESC_PAGE_TIMEOUT_MS * 2)
    expect(s.visible).toBe(true) // no late hide from the safety timer
    vc.onEscapeKey() // nothing left to close
    expect(vc.onPageEscape(false)).toBe('hidden')
    expect(s.visible).toBe(false)
  })

  it('Esc while pinned (dialog / choice / preview / login) never hides, even if the page unpins on it', () => {
    const { s, vc, advance } = setup()
    vc.show()
    vc.setRendererPinned(true)
    vc.onEscapeKey()
    vc.setRendererPinned(false) // the page closed its dialog without preventDefault()
    expect(vc.onPageEscape(false)).toBe('ignored')
    expect(s.visible).toBe(true)
    vc.setSystemPinned(true)
    vc.onEscapeKey()
    advance(ESC_PAGE_TIMEOUT_MS + 1) // no answer: still pinned → stays
    expect(s.visible).toBe(true)
  })

  it('a page that never answers (hung / not loaded) is hidden after the timeout', () => {
    const { s, vc, advance } = setup()
    vc.show()
    vc.onEscapeKey()
    advance(ESC_PAGE_TIMEOUT_MS - 1)
    expect(s.visible).toBe(true)
    advance(1)
    expect(s.visible).toBe(false)
    expect(vc.pendingEscapes).toBe(0)
    // the late verdict of that key must not touch the popup shown again meanwhile
    vc.onHotkey()
    expect(vc.onPageEscape(false)).toBe('ignored')
    expect(s.visible).toBe(true)
  })

  it('verdicts are matched to keys in order; synthetic reports without a key are ignored', () => {
    const { s, vc } = setup()
    vc.show()
    expect(vc.onPageEscape(false)).toBe('ignored') // no real key pressed (page-dispatched event)
    expect(s.visible).toBe(true)
    vc.setRendererPinned(true)
    vc.onEscapeKey() // pinned at key time
    vc.setRendererPinned(false)
    vc.onEscapeKey() // not pinned
    expect(vc.pendingEscapes).toBe(2)
    expect(vc.onPageEscape(false)).toBe('ignored') // verdict of the first (pinned) key
    expect(s.visible).toBe(true)
    expect(vc.onPageEscape(false)).toBe('hidden')
  })

  it('a fresh show drops verdicts of Esc keys pressed before it', () => {
    const { s, vc, advance, liveTimers } = setup()
    vc.show()
    vc.onEscapeKey()
    vc.hide() // e.g. the page hid the popup itself
    vc.show()
    expect(vc.pendingEscapes).toBe(0)
    expect(liveTimers()).toBe(0)
    expect(vc.onPageEscape(false)).toBe('ignored')
    advance(ESC_PAGE_TIMEOUT_MS + 1)
    expect(s.visible).toBe(true)
  })

  it('a late verdict of a key pressed before a re-show is not taken for the next key', () => {
    const { s, vc } = setup()
    vc.show()
    vc.setRendererPinned(true)
    vc.onEscapeKey() // pinned Esc; its verdict is delayed (the window hides right after)
    vc.setRendererPinned(false)
    vc.onTrayClick() // hidden
    vc.show() // shown again
    vc.onEscapeKey() // text in the field → the page will clear it (handled)
    expect(vc.onPageEscape(false)).toBe('ignored') // late verdict of the first key
    expect(s.visible).toBe(true)
    expect(vc.onPageEscape(true)).toBe('ignored') // the second key: consumed by the page
    expect(s.visible).toBe(true)
    expect(vc.pendingEscapes).toBe(0)
  })

  it('a void key whose verdict never comes expires and stops shifting the queue', () => {
    const { s, vc, advance } = setup()
    vc.show()
    vc.onEscapeKey() // lost (page was loading): no verdict ever
    advance(ESC_PAGE_TIMEOUT_MS) // timeout hides
    expect(s.visible).toBe(false)
    vc.show()
    advance(ESC_VOID_TTL_MS + 1)
    vc.onEscapeKey()
    expect(vc.onPageEscape(false)).toBe('hidden') // matched to its own key, not the lost one
  })

  it('Esc on a hidden popup is not tracked', () => {
    const { vc } = setup()
    vc.onEscapeKey()
    expect(vc.pendingEscapes).toBe(0)
  })

  it('tray click toggles', () => {
    const { s, vc, advance } = setup()
    expect(vc.onTrayClick()).toBe('shown')
    advance(1000)
    expect(vc.onTrayClick()).toBe('hidden')
    expect(s.visible).toBe(false)
  })

  it('tray click right after the blur it caused does not re-open the popup', () => {
    const { s, vc, advance } = setup()
    vc.onTrayClick()
    advance(2000)
    // Windows: mousedown on the taskbar blurs the popup, then the tray click arrives
    vc.onBlur()
    advance(50)
    expect(vc.onTrayClick()).toBe('suppressed')
    expect(s.visible).toBe(false)
    // the next click works normally
    advance(50)
    expect(vc.onTrayClick()).toBe('shown')
  })

  it('a tray click long after a blur-hide opens again', () => {
    const { s, vc, advance } = setup()
    vc.show()
    vc.onBlur()
    advance(BLUR_CLICK_GRACE_MS + 10)
    expect(vc.onTrayClick()).toBe('shown')
    expect(s.visible).toBe(true)
  })

  it('show() on a visible popup only focuses (window is never recreated)', () => {
    const { s, vc } = setup()
    vc.show()
    s.focused = false
    expect(vc.show()).toBe('focused')
    expect(s.shows).toBe(1)
    expect(s.focuses).toBe(1)
  })
})

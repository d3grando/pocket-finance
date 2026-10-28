import { describe, expect, it } from 'vitest'
import { AUTOHIDE_RESERVE, MARGIN, computePopupBounds, computeResize, detectTaskbarEdge, type DisplayLike, type Rect } from '../../src/main/position'

const SIZE = { width: 420, height: 450 }

// 1920×1080 @100 %, taskbar 48 px at the bottom (Windows 11 default)
const bottom: DisplayLike = { bounds: { x: 0, y: 0, width: 1920, height: 1080 }, workArea: { x: 0, y: 0, width: 1920, height: 1032 } }
const trayBottom: Rect = { x: 1780, y: 1040, width: 24, height: 32 }

function inside(b: Rect, area: Rect) {
  expect(b.x).toBeGreaterThanOrEqual(area.x)
  expect(b.y).toBeGreaterThanOrEqual(area.y)
  expect(b.x + b.width).toBeLessThanOrEqual(area.x + area.width)
  expect(b.y + b.height).toBeLessThanOrEqual(area.y + area.height)
}

describe('detectTaskbarEdge', () => {
  it('finds the taskbar side from the work area', () => {
    expect(detectTaskbarEdge(bottom).edge).toBe('bottom')
    expect(detectTaskbarEdge({ bounds: bottom.bounds, workArea: { x: 0, y: 40, width: 1920, height: 1040 } }).edge).toBe('top')
    expect(detectTaskbarEdge({ bounds: bottom.bounds, workArea: { x: 62, y: 0, width: 1858, height: 1080 } }).edge).toBe('left')
    expect(detectTaskbarEdge({ bounds: bottom.bounds, workArea: { x: 0, y: 0, width: 1858, height: 1080 } }).edge).toBe('right')
  })

  it('auto-hide taskbar: uses the edge nearest to the tray icon', () => {
    const d: DisplayLike = { bounds: bottom.bounds, workArea: bottom.bounds }
    expect(detectTaskbarEdge(d, { x: 1900, y: 1075 })).toEqual({ edge: 'bottom', autoHide: true })
    expect(detectTaskbarEdge(d, { x: 1900, y: 5 })).toEqual({ edge: 'top', autoHide: true })
    expect(detectTaskbarEdge(d, { x: 3, y: 1000 })).toEqual({ edge: 'left', autoHide: true })
    expect(detectTaskbarEdge(d, null)).toEqual({ edge: 'bottom', autoHide: true })
  })
})

describe('computePopupBounds', () => {
  it('bottom taskbar: above the tray icon, centered on it, inside the work area', () => {
    const p = computePopupBounds({ trayBounds: trayBottom, display: bottom, size: SIZE })
    expect(p.edge).toBe('bottom')
    expect(p.bounds.width).toBe(420)
    expect(p.bounds.height).toBe(450)
    expect(p.bounds.y + p.bounds.height).toBe(1032 - MARGIN)
    // icon at x=1792 → centered would overflow right edge → clamped
    expect(p.bounds.x).toBe(1920 - 420 - MARGIN)
    inside(p.bounds, bottom.workArea)
  })

  it('bottom taskbar, icon in the middle: centered on the icon', () => {
    const p = computePopupBounds({ trayBounds: { x: 950, y: 1040, width: 20, height: 32 }, display: bottom, size: SIZE })
    expect(p.bounds.x).toBe(960 - 210)
  })

  it('top taskbar: under the tray, grows down', () => {
    const d: DisplayLike = { bounds: bottom.bounds, workArea: { x: 0, y: 48, width: 1920, height: 1032 } }
    const p = computePopupBounds({ trayBounds: { x: 1780, y: 8, width: 24, height: 32 }, display: d, size: SIZE })
    expect(p.edge).toBe('top')
    expect(p.bounds.y).toBe(48 + MARGIN)
    inside(p.bounds, d.workArea)
  })

  it('left taskbar: to the right of it, vertically at the icon', () => {
    const d: DisplayLike = { bounds: bottom.bounds, workArea: { x: 62, y: 0, width: 1858, height: 1080 } }
    const p = computePopupBounds({ trayBounds: { x: 18, y: 1000, width: 24, height: 24 }, display: d, size: SIZE })
    expect(p.edge).toBe('left')
    expect(p.bounds.x).toBe(62 + MARGIN)
    expect(p.bounds.y + p.bounds.height).toBe(1080 - MARGIN) // clamped to the bottom
    inside(p.bounds, d.workArea)
  })

  it('right taskbar', () => {
    const d: DisplayLike = { bounds: bottom.bounds, workArea: { x: 0, y: 0, width: 1858, height: 1080 } }
    const p = computePopupBounds({ trayBounds: { x: 1878, y: 500, width: 24, height: 24 }, display: d, size: SIZE })
    expect(p.edge).toBe('right')
    expect(p.bounds.x + p.bounds.width).toBe(1858 - MARGIN)
    expect(p.bounds.y).toBe(512 - 225)
  })

  it('second monitor to the left with negative coordinates and 150 % scaling (DIPs)', () => {
    // 2560×1440 @150 % → 1707×960 DIP, placed left of the primary
    const d: DisplayLike = {
      bounds: { x: -1707, y: 120, width: 1707, height: 960 },
      workArea: { x: -1707, y: 120, width: 1707, height: 928 },
      scaleFactor: 1.5,
    }
    const p = computePopupBounds({ trayBounds: { x: -120, y: 1050, width: 16, height: 24 }, display: d, size: SIZE })
    expect(p.edge).toBe('bottom')
    inside(p.bounds, d.workArea)
    expect(p.bounds.y + p.bounds.height).toBe(120 + 928 - MARGIN)
  })

  it('no tray bounds (hotkey / Linux): tray corner of the display', () => {
    const p = computePopupBounds({ trayBounds: { x: 0, y: 0, width: 0, height: 0 }, cursor: { x: 500, y: 500 }, display: bottom, size: SIZE })
    expect(p.bounds).toEqual({ x: 1920 - 420 - MARGIN, y: 1032 - 450 - MARGIN, width: 420, height: 450 })
  })

  it('auto-hide taskbar leaves room for the taskbar to slide in', () => {
    const d: DisplayLike = { bounds: bottom.bounds, workArea: bottom.bounds }
    const p = computePopupBounds({ trayBounds: { x: 1780, y: 1060, width: 24, height: 20 }, display: d, size: SIZE })
    expect(p.bounds.y + p.bounds.height).toBe(1080 - AUTOHIDE_RESERVE - MARGIN)
  })

  it('small screen: the popup shrinks to fit, never off-screen', () => {
    const d: DisplayLike = { bounds: { x: 0, y: 0, width: 400, height: 380 }, workArea: { x: 0, y: 0, width: 400, height: 350 } }
    const p = computePopupBounds({ trayBounds: { x: 380, y: 355, width: 16, height: 20 }, display: d, size: SIZE })
    inside(p.bounds, d.workArea)
    expect(p.bounds.width).toBe(400 - 2 * MARGIN)
    expect(p.bounds.height).toBe(350 - 2 * MARGIN)
  })

  it('returns integer coordinates', () => {
    const p = computePopupBounds({ trayBounds: { x: 1001.5, y: 1040, width: 23, height: 32 }, display: bottom, size: SIZE })
    for (const v of Object.values(p.bounds)) expect(Number.isInteger(v)).toBe(true)
  })
})

describe('computeResize', () => {
  const area = bottom.workArea
  it('bottom taskbar: grows up, bottom edge fixed, max 640', () => {
    const cur = { x: 1492, y: 574, width: 420, height: 450 }
    const r = computeResize(cur, 900, 'bottom', area, { min: 300, max: 640 })
    expect(r.height).toBe(640)
    expect(r.y + r.height).toBe(cur.y + cur.height)
    expect(r.x).toBe(cur.x)
  })
  it('top taskbar: grows down, top edge fixed', () => {
    const cur = { x: 1492, y: 56, width: 420, height: 450 }
    const r = computeResize(cur, 560, 'top', { x: 0, y: 48, width: 1920, height: 1032 }, { min: 300, max: 640 })
    expect(r.y).toBe(56)
    expect(r.height).toBe(560)
  })
  it('clamps to the work area and the minimum', () => {
    const cur = { x: 0, y: 20, width: 420, height: 450 }
    const r = computeResize(cur, 100, 'bottom', area, { min: 300, max: 640 })
    expect(r.height).toBe(300)
    expect(r.y).toBeGreaterThanOrEqual(area.y + MARGIN)
  })
})

describe('macOS menu bar', () => {
  // 1512×982 MacBook: menu bar 37 px at the top, Dock 70 px at the bottom
  const mac: DisplayLike = { bounds: { x: 0, y: 0, width: 1512, height: 982 }, workArea: { x: 0, y: 37, width: 1512, height: 875 } }
  const size = { width: 420, height: 450 }

  it('opens under the menu bar icon, not above the Dock', () => {
    const tray = { x: 1000, y: 0, width: 30, height: 37 }
    const p = computePopupBounds({ trayBounds: tray, display: mac, size, platform: 'darwin' })
    expect(p.edge).toBe('top')
    expect(p.bounds.y).toBe(37 + MARGIN)
    expect(p.bounds.x).toBe(Math.round(1015 - 420 / 2)) // centred under the icon
    // an icon near the right edge: the popup is kept on screen
    const r = computePopupBounds({ trayBounds: { x: 1440, y: 0, width: 30, height: 37 }, display: mac, size, platform: 'darwin' })
    expect(r.bounds.x + r.bounds.width).toBe(1512 - MARGIN)
  })

  it('by the hotkey (no icon bounds) goes to the top-right corner', () => {
    const p = computePopupBounds({ trayBounds: null, cursor: { x: 700, y: 500 }, display: mac, size, platform: 'darwin' })
    expect(p.edge).toBe('top')
    expect(p.bounds.y).toBe(37 + MARGIN)
    expect(p.bounds.x + p.bounds.width).toBe(1512 - MARGIN)
  })

  it('Windows logic is unchanged for the same geometry', () => {
    const p = computePopupBounds({ trayBounds: null, display: mac, size, platform: 'win32' })
    expect(p.edge).toBe('bottom')
  })
})

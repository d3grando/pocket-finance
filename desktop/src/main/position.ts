// Popup placement next to the tray (pure, unit-tested). All coordinates are Electron DIPs:
// screen.getDisplayNearestPoint() returns bounds/workArea already divided by the display's
// scale factor, and BrowserWindow.setBounds() takes DIPs, so per-monitor scaling is handled
// as long as we pick the display that actually holds the tray icon / cursor.
import type { TaskbarEdge } from '../shared/ipc'

export interface Point {
  x: number
  y: number
}
export interface Rect extends Point {
  width: number
  height: number
}
export interface DisplayLike {
  id?: number
  bounds: Rect
  workArea: Rect
  scaleFactor?: number
}
export interface Size {
  width: number
  height: number
}

/** Gap between the popup and the taskbar / screen edge. */
export const MARGIN = 8
/** Space kept free for an auto-hidden taskbar that slides in over the work area. */
export const AUTOHIDE_RESERVE = 48

export function isUsableRect(r: Rect | null | undefined): r is Rect {
  return !!r && Number.isFinite(r.x) && Number.isFinite(r.y) && r.width > 0 && r.height > 0
}

export function center(r: Rect): Point {
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
}

/**
 * Which side of the display the taskbar is on. The taskbar is wherever the work area is
 * smaller than the full bounds. If they are equal (auto-hide taskbar, or none), fall back
 * to the screen edge nearest to the tray icon / cursor, and finally to 'bottom' (Windows default).
 */
export function detectTaskbarEdge(display: DisplayLike, anchor?: Point | null): { edge: TaskbarEdge; autoHide: boolean } {
  const b = display.bounds
  const w = display.workArea
  const gaps: Record<TaskbarEdge, number> = {
    top: w.y - b.y,
    left: w.x - b.x,
    right: b.x + b.width - (w.x + w.width),
    bottom: b.y + b.height - (w.y + w.height),
  }
  let best: TaskbarEdge = 'bottom'
  let bestGap = 0
  for (const e of ['bottom', 'top', 'left', 'right'] as TaskbarEdge[]) {
    if (gaps[e] > bestGap) {
      best = e
      bestGap = gaps[e]
    }
  }
  if (bestGap > 0) return { edge: best, autoHide: false }
  if (!anchor) return { edge: 'bottom', autoHide: true }
  const dist: Record<TaskbarEdge, number> = {
    top: Math.abs(anchor.y - b.y),
    bottom: Math.abs(b.y + b.height - anchor.y),
    left: Math.abs(anchor.x - b.x),
    right: Math.abs(b.x + b.width - anchor.x),
  }
  let nearest: TaskbarEdge = 'bottom'
  for (const e of ['bottom', 'top', 'left', 'right'] as TaskbarEdge[]) {
    if (dist[e] < dist[nearest]) nearest = e
  }
  return { edge: nearest, autoHide: true }
}

function clamp(v: number, min: number, max: number): number {
  if (max < min) return min
  return Math.min(Math.max(v, min), max)
}

export interface PlaceInput {
  /** Tray icon bounds (Windows/macOS). Empty/zero on Linux or when the icon is in the overflow area. */
  trayBounds?: Rect | null
  /** Cursor position — used when tray bounds are unknown (e.g. opened by the hotkey). */
  cursor?: Point | null
  /** Display chosen with screen.getDisplayNearestPoint(anchor). */
  display: DisplayLike
  size: Size
  margin?: number
  /** process.platform. On macOS the status items always live in the menu bar at the top. */
  platform?: string
}

export interface Placement {
  bounds: Rect
  edge: TaskbarEdge
  anchor: Point
}

/** Area the popup may occupy: the work area, minus room for an auto-hidden taskbar. */
function usableArea(display: DisplayLike, edge: TaskbarEdge, autoHide: boolean): Rect {
  const a = { ...display.workArea }
  if (!autoHide) return a
  const r = AUTOHIDE_RESERVE
  if (edge === 'bottom') a.height -= r
  else if (edge === 'top') {
    a.y += r
    a.height -= r
  } else if (edge === 'left') {
    a.x += r
    a.width -= r
  } else {
    a.width -= r
  }
  return a
}

/**
 * Place the popup next to the tray: centered on the icon along the taskbar, pressed against the
 * taskbar across it, always fully inside the work area of the display holding the icon.
 * Used both for clicks on the tray icon and for the global hotkey (then the anchor is the
 * cursor's display, popup goes to that display's tray corner).
 */
export function computePopupBounds(input: PlaceInput): Placement {
  const m = input.margin ?? MARGIN
  const tray = isUsableRect(input.trayBounds) ? input.trayBounds : null
  const anchorFromTray = tray ? center(tray) : null
  // macOS: the work area is cut at the top (menu bar) and usually much more at the bottom (Dock),
  // so «the biggest gap» would pick the Dock; the tray icon is always in the menu bar.
  const { edge, autoHide } = input.platform === 'darwin'
    ? { edge: 'top' as TaskbarEdge, autoHide: false }
    : detectTaskbarEdge(input.display, anchorFromTray ?? input.cursor ?? null)
  const area = usableArea(input.display, edge, autoHide)

  // Without a tray icon position (hotkey, Linux) use the tray corner of this display:
  // Windows keeps the notification area at the end of the taskbar (right / bottom).
  const anchor: Point = anchorFromTray ?? {
    x: edge === 'left' ? area.x : area.x + area.width,
    y: edge === 'top' ? area.y : area.y + area.height,
  }

  const width = Math.min(input.size.width, Math.max(area.width - 2 * m, 1))
  const height = Math.min(input.size.height, Math.max(area.height - 2 * m, 1))
  const minX = area.x + m
  const maxX = area.x + area.width - width - m
  const minY = area.y + m
  const maxY = area.y + area.height - height - m

  let x: number
  let y: number
  switch (edge) {
    case 'top':
      x = clamp(anchor.x - width / 2, minX, maxX)
      y = minY
      break
    case 'left':
      x = minX
      y = clamp(anchor.y - height / 2, minY, maxY)
      break
    case 'right':
      x = maxX
      y = clamp(anchor.y - height / 2, minY, maxY)
      break
    case 'bottom':
    default:
      x = clamp(anchor.x - width / 2, minX, maxX)
      y = maxY
      break
  }
  return {
    bounds: { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) },
    edge,
    anchor,
  }
}

/**
 * Resize the visible popup to a new content height. It grows away from the taskbar: with the
 * taskbar at the top the top edge stays put, otherwise the bottom edge stays put (grows up).
 */
export function computeResize(
  current: Rect,
  height: number,
  edge: TaskbarEdge,
  workArea: Rect,
  limits: { min: number; max: number },
  margin = MARGIN,
): Rect {
  const maxByArea = workArea.height - 2 * margin
  const h = Math.round(clamp(height, limits.min, Math.min(limits.max, Math.max(maxByArea, limits.min))))
  let y = edge === 'top' ? current.y : current.y + current.height - h
  y = clamp(y, workArea.y + margin, workArea.y + workArea.height - h - margin)
  return { x: current.x, y: Math.round(y), width: current.width, height: h }
}

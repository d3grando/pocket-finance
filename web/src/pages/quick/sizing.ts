// Quick Capture window sizing (DESIGN §10.10): 420×450 by default, grows up to 640
// only for what does not fit (preview, account choice) and shrinks back afterwards.
export const BASE_HEIGHT = 450
export const MAX_HEIGHT = 640
export const FOOTER_HEIGHT = 32
export const MAX_RECENT = 4

/**
 * How many «Последние» rows fit into the base window: `fixed` = everything except the recent block,
 * `labelH` = the block's caption, `rowH` = one row. 0 → the block is hidden.
 */
export function recentRowsThatFit(fixed: number, labelH: number, rowH: number, base = BASE_HEIGHT, footer = FOOTER_HEIGHT): number {
  const room = base - footer - fixed - labelH
  if (rowH <= 0) return MAX_RECENT
  return Math.max(0, Math.min(MAX_RECENT, Math.floor(room / rowH)))
}

/** Window height for the measured content: never below 450, never above 640. */
export function quickWindowHeight(content: number, footer = FOOTER_HEIGHT): number {
  return Math.min(MAX_HEIGHT, Math.max(BASE_HEIGHT, Math.ceil(content + footer)))
}

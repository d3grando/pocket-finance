// Command palette helpers (DESIGN §9.7).

/** «кофе 23к», «такси 70 000», «15$ подписка» — a number and a word → offer «Записать: …». */
export function looksLikeOperation(text: string): boolean {
  const t = text.trim()
  return t.length >= 3 && /\d/.test(t) && /[a-zа-яёăâđêôơư]/i.test(t)
}

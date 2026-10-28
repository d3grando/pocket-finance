// Small helpers shared by the desktop-native pages.
export const $ = <T extends HTMLElement = HTMLElement>(sel: string): T => {
  const el = document.querySelector<T>(sel)
  if (!el) throw new Error(`missing ${sel}`)
  return el
}

/** Text-only element factory (never innerHTML with data → no XSS from server strings). */
export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: { class?: string; text?: string; title?: string; attrs?: Record<string, string> } = {},
  children: (Node | string | null | undefined)[] = [],
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag)
  if (props.class) el.className = props.class
  if (props.text !== undefined) el.textContent = props.text
  if (props.title) el.title = props.title
  for (const [k, v] of Object.entries(props.attrs ?? {})) el.setAttribute(k, v)
  for (const c of children) if (c !== null && c !== undefined) el.append(c)
  return el
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

export function bindConnectionDot(dot: HTMLElement, label?: HTMLElement): void {
  const apply = (s: string) => {
    dot.classList.toggle('online', s === 'online')
    dot.classList.toggle('offline', s === 'offline')
    const t = s === 'online' ? 'На связи' : s === 'offline' ? 'Нет связи' : 'Проверяю связь…'
    dot.title = t
    if (label) label.textContent = s === 'offline' ? 'Нет связи' : ''
  }
  void window.pocket.connection.get().then(apply)
  window.pocket.connection.onChange(apply)
}

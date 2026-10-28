// Lite Quick Capture (used when the packaged web build is absent): DESIGN §10.10 essentials —
// input with focus, Enter → POST /assistant/messages (Idempotency-Key, same key on retry),
// result card with Отменить/Изменить and the other card buttons, KPI strip, last 4 operations,
// connection indicator, SSE + refetch on show.
import './styles.css'
import './capture.css'
import { formatCompact, formatMoney, formatWhen } from '../shared/money'
import type { TaskbarEdge } from '../shared/ipc'
import {
  ApiError,
  call,
  initApi,
  NetworkError,
  newKey,
  startEvents,
  type AssistantResponse,
  type Button,
  type Card,
  type MutationResult,
  type SummaryOut,
  type Tx,
  type TxPage,
  type UndoResult,
} from './capture-api'
import { $, bindConnectionDot, h } from './common'

const root = $('#root')
const input = $<HTMLTextAreaElement>('#input')
const bar = $('#bar')
const spinner = $('#spinner')
const result = $('#result')
const offline = $('#offline')

interface Attempt {
  text: string
  key: string
  editId: number | null
  at: number
}
let lastAttempt: Attempt | null = null // kept until success → retry reuses the key
let lastSent = ''
let editId: number | null = null
let busy = false
let cards: Card[] = []
let freshTxId: number | null = null
let lastSync: Date | null = null
let conn = 'unknown'

// ---------- result cards ----------
function cardClass(c: Card): string {
  if (c.kind === 'needs_account') return 'warn'
  if (c.kind === 'error') return 'err'
  if (c.kind === 'unclear' || c.kind === 'answer') return 'info'
  if (c.kind === 'preview') return 'prev'
  return ''
}
const MARKS: Record<string, string> = { needs_account: '◔', error: '!', unclear: '?', answer: '◷', preview: '⧉', offline: '⌁' }

function buttonEl(b: Button): HTMLButtonElement {
  const label = b.hotkey && /^[0-9]$/.test(b.hotkey) ? `${b.hotkey} ${b.label}` : b.label
  const style = b.style === 'primary' ? 'primary' : b.style === 'outline' ? '' : 'ghost'
  const el = h('button', { class: `btn ${style}`.trim(), text: label, attrs: { 'data-kind': b.action.kind, 'data-id': b.id } })
  if (b.hotkey) el.title = b.hotkey
  el.addEventListener('click', () => void runAction(b, el))
  return el
}

function renderCards(list: Card[]): void {
  cards = list.slice(-2)
  result.replaceChildren(
    ...cards.map((c) => {
      const lines: string[] = []
      if (c.question) lines.push(c.question)
      if (c.kind === 'preview' && c.change_summary) lines.push(c.change_summary)
      if (c.lines?.length) lines.push(c.lines.map((l) => (l.value ? `${l.label} ${l.value}` : l.label)).join(' · '))
      if (c.total) lines.push(c.total)
      const sub = [c.subtitle, c.kind === 'answer' ? c.headline : null, c.text].filter(Boolean).join(' · ')
      return h('div', { class: `card ${cardClass(c)}`.trim(), attrs: { 'data-kind': c.kind } }, [
        h('span', { class: 'mark', text: MARKS[c.kind] ?? '✓' }),
        h('div', { class: 'title num', text: c.approx ? `${c.title}  ${c.approx}` : c.title }),
        sub ? h('div', { class: 'sub', text: sub }) : null,
        lines.length ? h('div', { class: 'lines', text: lines.join('\n') }) : null,
        c.note ? h('div', { class: 'note', text: c.note }) : null,
        c.buttons?.length ? h('div', { class: 'actions' }, c.buttons.map(buttonEl)) : null,
      ])
    }),
  )
  const modal = cards.some((c) => c.kind === 'preview' || c.kind === 'needs_account')
  void window.pocket.window.setPinned(modal)
  const preview = cards.some((c) => c.kind === 'preview')
  root.classList.toggle('previewing', preview)
  if (preview) {
    requestAnimationFrame(() => {
      void window.pocket.window.resize(Math.min(640, Math.max(450, root.scrollHeight + result.scrollHeight)))
      result.querySelector<HTMLButtonElement>('[data-kind="confirm_preview"]')?.focus()
    })
  } else void window.pocket.window.resize(450)
}

function clearCards(): void {
  renderCards([])
}

function localCard(kind: string, title: string, subtitle: string | null, buttons: Button[] = []): Card {
  return { kind, title, subtitle, buttons }
}

function txTitle(t: Tx): string {
  const money = formatMoney(t.amount, t.currency)
  const name = t.category?.name ?? t.title ?? ''
  return name ? `${money} · ${name}` : money
}

function createdCard(m: MutationResult): Card {
  return {
    kind: 'created',
    title: txTitle(m.transaction),
    subtitle: m.transaction.account?.name ?? null,
    action_id: m.action_id,
    transaction: m.transaction,
    buttons: [
      { id: 'undo', label: 'Отменить', hotkey: 'Ctrl+Z', action: { kind: 'undo', action_id: m.action_id } },
      { id: 'edit', label: 'Изменить', hotkey: 'Ctrl+E', action: { kind: 'edit', transaction_id: m.transaction.id } },
    ],
  }
}

function undoneLine(u: UndoResult): void {
  cards = []
  void window.pocket.window.setPinned(false)
  root.classList.remove('previewing')
  const redo: Button = { id: 'redo', label: 'Вернуть', action: { kind: 'redo', action_id: u.undo_action_id } }
  result.replaceChildren(h('div', { class: 'undone', attrs: { 'data-kind': 'undone' } }, [h('span', { text: `↶ ${u.summary || 'Отменено'}` }), buttonEl(redo)]))
}

function showError(e: unknown, retry: boolean): void {
  if (e instanceof NetworkError) {
    renderCards([localCard('offline', 'Не сохранено — нет связи', null, retry ? [{ id: 'retry', label: 'Повторить ⏎', action: { kind: 'retry' } }] : [])])
    return
  }
  if (e instanceof ApiError && e.status === 401) return // main switches to the login screen
  const msg = e instanceof ApiError ? e.message : 'Что-то пошло не так'
  renderCards([localCard('error', msg, null, retry ? [{ id: 'retry', label: 'Повторить', action: { kind: 'retry' } }] : [])])
}

// ---------- send ----------
async function send(textArg?: string): Promise<void> {
  const text = (textArg ?? input.value).trim()
  if (!text || busy) return
  const reuse = lastAttempt && lastAttempt.text === text && lastAttempt.editId === editId && Date.now() - lastAttempt.at < 10 * 60_000
  const attempt: Attempt = reuse && lastAttempt ? lastAttempt : { text, key: newKey(), editId, at: Date.now() }
  lastAttempt = attempt
  busy = true
  const spin = setTimeout(() => spinner.classList.remove('hidden'), 150)
  bar.classList.add('busy')
  try {
    const res = await call<AssistantResponse>('POST', '/assistant/messages', { text, context: { edit_transaction_id: attempt.editId } }, attempt.key)
    lastAttempt = null
    lastSent = text
    const list = res.cards ?? []
    const keep = list.some((c) => c.keep_input)
    if (!keep) {
      if (input.value.trim() === text) input.value = ''
      setEdit(null)
    } else {
      input.select()
      if (list.some((c) => c.kind === 'error' || c.kind === 'unclear')) shake()
    }
    const created = list.find((c) => c.transaction)?.transaction
    freshTxId = created?.id ?? null
    renderCards(list)
    void refresh()
  } catch (e) {
    showError(e, true)
  } finally {
    clearTimeout(spin)
    spinner.classList.add('hidden')
    bar.classList.remove('busy')
    busy = false
    input.focus()
  }
}

function shake(): void {
  bar.classList.remove('shake')
  void bar.offsetWidth
  bar.classList.add('shake')
}

function setEdit(id: number | null, label?: string): void {
  editId = id
  $('#edit-hint').classList.toggle('hidden', id === null)
  if (id !== null) $('#edit-text').textContent = `Изменяю: ${label ?? 'операцию'} — напишите новую сумму или категорию`
  input.placeholder = id === null ? 'На что потратил деньги?' : 'Например: 250к или кофе'
}

// ---------- card buttons ----------
const clickKeys = new WeakMap<HTMLElement, string>()
function keyFor(el: HTMLElement): string {
  let k = clickKeys.get(el)
  if (!k) {
    k = newKey()
    clickKeys.set(el, k)
  }
  return k
}

async function runAction(b: Button, el: HTMLElement): Promise<void> {
  const a = b.action
  try {
    switch (a.kind) {
      case 'undo':
      case 'redo': {
        if (!a.action_id) return
        const u = await call<UndoResult>('POST', `/actions/${encodeURIComponent(a.action_id)}/undo`, {}, keyFor(el))
        undoneLine(u)
        void refresh()
        break
      }
      case 'edit': {
        const card = cards.find((c) => c.transaction?.id === a.transaction_id)
        setEdit(a.transaction_id ?? null, card?.title)
        input.focus()
        break
      }
      case 'choose_account': {
        if (a.transaction_id === undefined) return
        const m = await call<MutationResult>('POST', `/transactions/${a.transaction_id}/account`, { account_id: a.account_id ?? null }, keyFor(el))
        renderCards([createdCard(m)])
        void refresh()
        break
      }
      case 'later':
        clearCards()
        break
      case 'set_category': {
        const card = cards.find((c) => c.transaction?.id === a.transaction_id)
        const m = await call<MutationResult>('PATCH', `/transactions/${a.transaction_id}`, { category_id: a.category_id, version: card?.transaction?.version }, keyFor(el))
        renderCards([createdCard(m)])
        void refresh()
        break
      }
      case 'confirm_preview': {
        if (!a.preview_token) return
        const r = await call<AssistantResponse>('POST', `/assistant/previews/${encodeURIComponent(a.preview_token)}/confirm`, {}, keyFor(el))
        renderCards(r.cards ?? [])
        void refresh()
        break
      }
      case 'cancel_preview':
        if (a.preview_token) await call('POST', `/assistant/previews/${encodeURIComponent(a.preview_token)}/cancel`, {}).catch(() => undefined)
        clearCards()
        break
      case 'send_text':
        if (a.text) {
          input.value = a.text
          await send(a.text)
        }
        break
      case 'retry':
        if (lastAttempt) await send(lastAttempt.text)
        break
      case 'manual':
        await window.pocket.window.openFull('/add')
        break
      case 'change_category':
        await window.pocket.window.openFull(a.transaction_id ? `/operations?tx=${a.transaction_id}` : '/operations')
        break
      case 'open':
        await window.pocket.window.openFull(a.route && a.route.startsWith('/') ? a.route : '/')
        break
      default:
        break
    }
  } catch (e) {
    showError(e, false)
  } finally {
    if (document.activeElement === document.body) input.focus()
  }
}

// ---------- data ----------
function setText(sel: string, text: string): void {
  $(sel).textContent = text
}

function negate(v: string | null): string | null {
  if (v === null) return null
  const t = v.trim()
  if (/^-?0*(\.0*)?$/.test(t.replace('-', ''))) return t
  return t.startsWith('-') ? t.slice(1) : `-${t}`
}

function signedAmount(t: Tx): string {
  const money = formatMoney(t.amount, t.currency)
  if (t.kind === 'expense') return `−${money}`
  if (t.kind === 'income' || t.kind === 'refund') return `+${money}`
  return money
}

async function refresh(): Promise<void> {
  try {
    const [s, page] = await Promise.all([
      call<SummaryOut>('GET', '/stats/summary?currency=VND'),
      call<TxPage>('GET', '/transactions?limit=4'),
    ])
    setText('#kpi-today', formatMoney(negate(s.spent_today.value), s.spent_today.currency || 'VND'))
    setText('#kpi-month', formatCompact(negate(s.spent_month.value), s.spent_month.currency || 'VND'))
    setText('#kpi-income', formatCompact(s.income_month.value, s.income_month.currency || 'VND', { sign: 'always' }))
    const n = s.needs_account_count ?? 0
    const needs = $('#needs')
    needs.textContent = `Разобрать ${n}`
    needs.classList.toggle('hidden', n <= 0)
    const items = page.items ?? []
    $('#recent').replaceChildren(
      ...(items.length
        ? items.map((t) =>
            h('li', { class: t.id === freshTxId ? 'fresh' : '', attrs: { 'data-id': String(t.id) } }, [
              h('span', { class: 't', text: t.category?.name ?? t.title ?? '' }),
              h('span', { class: 'w num', text: formatWhen(t.occurred_at) }),
              h('span', { class: `a num${t.kind === 'income' || t.kind === 'refund' ? ' plus' : ''}`, text: signedAmount(t) }),
            ]),
          )
        : [h('li', { class: 'empty', text: 'Пока нет операций' })]),
    )
    if (freshTxId !== null) {
      const id = freshTxId
      setTimeout(() => $('#recent').querySelector(`[data-id="${id}"]`)?.classList.remove('fresh'), 1200)
      freshTxId = null
    }
    lastSync = new Date()
    setText('#stale', '')
    setText('#sync', formatWhen(lastSync.toISOString()))
  } catch (e) {
    if (lastSync) setText('#stale', `· данные на ${formatWhen(lastSync.toISOString())}`)
    if (!(e instanceof NetworkError) && !(e instanceof ApiError)) throw e
  }
}

// ---------- keyboard ----------
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    void send()
    return
  }
  if (e.key === 'ArrowUp' && !input.value && lastSent) {
    e.preventDefault()
    input.value = lastSent
    return
  }
  if (input.value) return
  const btn = (kind: string) => result.querySelector<HTMLButtonElement>(`.btn[data-kind="${kind}"]`)
  if (e.ctrlKey && !e.shiftKey && !e.altKey && e.code === 'KeyZ') {
    const b = btn('undo') ?? btn('redo')
    if (b) {
      e.preventDefault()
      b.click()
    }
  } else if (e.ctrlKey && !e.shiftKey && !e.altKey && e.code === 'KeyE') {
    const b = btn('edit')
    if (b) {
      e.preventDefault()
      b.click()
    }
  } else if (/^[0-9]$/.test(e.key) && !e.ctrlKey && !e.altKey) {
    const b = [...result.querySelectorAll<HTMLButtonElement>('.btn[data-kind="choose_account"]')].find((x) => x.title === e.key)
    if (b) {
      e.preventDefault()
      b.click()
    }
  }
})

// Esc reaches the page first (DESIGN §9.1/§11): close the top layer (preview / account choice →
// edit mode), then clear the field. Only an Esc left alone (no preventDefault) hides the popup —
// main decides that after the dispatch (see src/preload/index.ts).
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || e.defaultPrevented || e.ctrlKey || e.altKey || e.shiftKey || e.metaKey) return
  const cancel = result.querySelector<HTMLButtonElement>('.btn[data-kind="cancel_preview"], .btn[data-kind="later"]')
  if (cancel) {
    e.preventDefault()
    cancel.click()
  } else if (editId !== null) {
    e.preventDefault()
    setEdit(null)
    input.focus()
  } else if (input.value) {
    e.preventDefault()
    input.value = ''
    input.focus()
  }
})

$('#edit-cancel').addEventListener('click', () => {
  setEdit(null)
  input.focus()
})
$('#full').addEventListener('click', () => void window.pocket.window.openFull('/'))
$('#full2').addEventListener('click', () => void window.pocket.window.openFull('/'))
$('#settings').addEventListener('click', () => void window.pocket.window.openSettings())
$('#needs').addEventListener('click', () => void window.pocket.window.openFull('/operations?status=needs_account'))

// ---------- window / connection ----------
window.pocket.window.onShown(({ edge }: { edge: TaskbarEdge }) => {
  input.focus()
  root.classList.remove('enter-up', 'enter-down')
  void root.offsetWidth
  root.classList.add(edge === 'top' ? 'enter-down' : 'enter-up')
  void refresh()
})

bindConnectionDot($('#dot'))
window.pocket.connection.onChange((s) => {
  const was = conn
  conn = s
  offline.classList.toggle('hidden', s !== 'offline')
  if (s === 'online' && was === 'offline') {
    void refresh()
    // auto-retry an unsent entry only if its card is still shown and it is < 5 min old
    if (lastAttempt && Date.now() - lastAttempt.at < 5 * 60_000 && result.querySelector('[data-kind="offline"]')) void send(lastAttempt.text)
  }
})
void window.pocket.connection.get().then((s) => {
  conn = s
  offline.classList.toggle('hidden', s !== 'offline')
})

async function start(): Promise<void> {
  await initApi()
  input.focus()
  await refresh()
  startEvents(
    () => void refresh(),
    () => void refresh(),
  )
  setInterval(() => {
    if (document.visibilityState === 'visible') void refresh()
  }, 60_000)
}
void start()

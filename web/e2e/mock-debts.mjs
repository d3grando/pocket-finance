// Debts part of the Playwright mock API (docs/API.md §15, DEBTS.md). Balances are derived from the live
// operations (like the backend: the balance of the person's service account), so undo / delete of an
// operation changes the debt by itself. Plain JS numbers are fine for a mock.
import { randomUUID } from 'node:crypto'

const DATIVE = { коле: 'Коля', пете: 'Петя', васе: 'Вася', лене: 'Лена', диме: 'Дима', мише: 'Миша' }
export const nominative = (w) => DATIVE[w.toLowerCase()] ?? (w[0].toUpperCase() + w.slice(1))
const fmt = (n, cur = 'VND') => {
  const s = String(Math.round(Math.abs(Number(n)) * (cur === 'VND' ? 1 : 100)) / (cur === 'VND' ? 1 : 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  return cur === 'VND' ? `${s} ₫` : `${s} ${cur}`
}
const USD = { VND: 1 / 26000, USD: 1, USDT: 1, TON: 5.4, RUB: 1 / 81 }
const dayShift = (iso, days) => new Date(Date.parse(`${iso}T12:00:00Z`) - days * 86400_000).toISOString().slice(0, 10)

/** sign of an operation on the debt balance */
const DELTA = { lend: 1, repay_out: 1, repay_in: -1, borrow: -1 }

export function createDebts(ctx) {
  const { accounts, cats, today, emit, newTxId } = ctx
  const debtAccountRef = (d) => ({ id: 1000 + d.id, name: `Долг: ${d.name}`, icon: 'HandCoins' })
  const ops = () => ctx.S.txs.filter((t) => !t.deleted && t.debt).slice().sort((a, b) => (a.occurred_on < b.occurred_on ? -1 : a.occurred_on > b.occurred_on ? 1 : a.id - b.id))
  const deltaOf = (t) => (t.debt.op === 'forgive' ? (t.kind === 'expense' ? -1 : 1) : DELTA[t.debt.op]) * Number(t.amount)

  function balancesOf(d) {
    const bal = new Map() // cur → { amount, open_since }
    for (const t of ops().filter((x) => x.debt.id === d.id)) {
      const cur = bal.get(t.currency) ?? { amount: 0, open_since: null }
      const before = cur.amount
      const after = before + deltaOf(t)
      if (Math.abs(after) < 1e-9) bal.set(t.currency, { amount: 0, open_since: null })
      else bal.set(t.currency, { amount: after, open_since: before === 0 || Math.sign(before) !== Math.sign(after) ? t.occurred_on : cur.open_since })
    }
    return [...bal.entries()].filter(([, v]) => v.amount !== 0).map(([currency, v]) => ({
      currency, amount: String(v.amount), direction: v.amount > 0 ? 'owed_to_me' : 'i_owe',
      approx_usd: USD[currency] ? (v.amount * USD[currency]).toFixed(2) : null,
      approx_vnd: USD[currency] ? String(Math.round(v.amount * USD[currency] * 26000)) : null, open_since: v.open_since,
    }))
  }

  function out(d, only) {
    const balances = balancesOf(d).filter((b) => !only || b.direction === only)
    const all = balancesOf(d)
    const status = !all.length ? 'settled' : all.every((b) => b.direction === 'owed_to_me') ? 'owed_to_me' : all.every((b) => b.direction === 'i_owe') ? 'i_owe' : 'mixed'
    const last = ops().filter((t) => t.debt.id === d.id).at(-1)
    return { id: d.id, name: d.name, handle: d.handle, aliases: d.aliases, note: d.note, account_id: 1000 + d.id, is_archived: d.is_archived, status, balances, last_activity_on: last?.occurred_on ?? null, remind_snoozed_until: null, version: d.version, created_at: d.created_at }
  }

  function list(includeSettled) {
    const owed = []; const iowe = []; const settled = []
    let ov = 0; let iv = 0
    for (const d of ctx.S.debts) {
      const b = balancesOf(d)
      if (b.some((x) => x.direction === 'owed_to_me')) owed.push(out(d, 'owed_to_me'))
      if (b.some((x) => x.direction === 'i_owe')) iowe.push(out(d, 'i_owe'))
      if (!b.length && includeSettled) settled.push(out(d))
      for (const x of b) { const v = Math.abs(Number(x.approx_vnd ?? 0)); if (x.direction === 'owed_to_me') ov += v; else iv += v }
    }
    return { owed_to_me: owed, i_owe: iowe, settled, totals: { owed_to_me_vnd: String(ov), owed_to_me_usd: (ov / 26000).toFixed(2), i_owe_vnd: String(iv), i_owe_usd: (iv / 26000).toFixed(2), owed_to_me_count: owed.length, i_owe_count: iowe.length, missing_rates: false } }
  }

  function detail(d) {
    const hist = []
    const run = new Map()
    for (const t of ops().filter((x) => x.debt.id === d.id)) {
      const after = (run.get(t.currency) ?? 0) + deltaOf(t)
      run.set(t.currency, after)
      hist.push({ transaction: pub(t), op: t.debt.op, currency: t.currency, delta: String(deltaOf(t)), balance_after: String(after) })
    }
    return { debt: out(d), history: hist.reverse(), total: hist.length, limit: 50, offset: 0 }
  }

  const find = (name) => ctx.S.debts.find((d) => d.name.toLowerCase() === name.toLowerCase())
  function getOrCreate(name) {
    let d = find(name)
    if (!d) { d = { id: ++ctx.S.debtSeq, name, handle: null, aliases: [], note: null, is_archived: false, version: 1, created_at: new Date().toISOString() }; ctx.S.debts.push(d) }
    return d
  }
  const pub = (t) => { const { deleted, ...rest } = t; void deleted; return rest }

  /** record one operation → { t, actionId, d, balance_after, excess } or { error } */
  function record({ op, d, amount, currency, accountId, accountNone, occurred_on }) {
    const bal = balancesOf(d).find((b) => b.currency === currency)
    const cur = bal ? Number(bal.amount) : 0
    if (op === 'repay') op = cur < 0 ? 'repay_out' : 'repay_in'
    if ((op === 'repay_in' && cur <= 0) || (op === 'repay_out' && cur >= 0) || (op === 'forgive' && cur === 0)) return { error: `${d.name}: долга в ${currency} нет` }
    if (amount == null) amount = Math.abs(cur)
    const inflow = op === 'repay_in' || op === 'borrow'
    const auto = currency === 'VND' ? 1 : currency === 'RUB' ? 5 : null
    const acc = op === 'forgive' ? null : accounts.find((a) => a.id === (accountId ?? (accountNone ? null : auto))) ?? null
    const status = op !== 'forgive' && !acc && !accountNone ? 'needs_account' : 'posted'
    const forgiveOwed = op === 'forgive' && cur > 0
    const cat = op === 'forgive' ? (forgiveOwed ? { id: 99, name: 'Переводы людям', kind: 'expense', icon: 'Send' } : { id: 98, name: 'Прочее', kind: 'income', icon: 'Circle' }) : null
    const real = acc ? { id: acc.id, name: acc.name, icon: acc.icon } : null
    const now = new Date()
    const t = {
      id: newTxId(), kind: op === 'forgive' ? (forgiveOwed ? 'expense' : 'income') : 'transfer', status, occurred_at: now.toISOString(), occurred_on: occurred_on ?? today(),
      amount: String(amount), currency,
      account: op === 'forgive' ? debtAccountRef(d) : inflow ? debtAccountRef(d) : real,
      account_amount: String(amount), account_asset: currency, account_rate: '1', is_estimated: false,
      counter_account: op === 'forgive' ? null : inflow ? real : debtAccountRef(d), counter_amount: op === 'forgive' ? null : String(amount), counter_asset: op === 'forgive' ? null : currency,
      category: cat ? { id: cat.id, name: cat.name, kind: cat.kind, icon: cat.icon, color: null, is_business: false, is_new: false } : null,
      is_business: false, merchant: null, counterparty: d.name, description: null,
      amount_usd: (Number(amount) * (USD[currency] ?? 1)).toFixed(2), amount_vnd: String(Math.round(Number(amount) * (USD[currency] ?? 1) * 26000)),
      usd_rate: null, rate_date: today(), rate_source: 'vietcombank', refund_of_id: null, recurring_id: null, source: 'web', raw_text: null,
      created_action_id: null, version: 1, created_at: now.toISOString(), updated_at: now.toISOString(), title: d.name, deleted: false,
      debt: { id: d.id, name: d.name, op },
    }
    ctx.S.txs.unshift(t)
    const actionId = randomUUID()
    t.created_action_id = actionId
    ctx.S.actions.set(actionId, { kind: 'create', txIds: [t.id], undone: false })
    const after = Number(balancesOf(d).find((b) => b.currency === currency)?.amount ?? 0)
    const excess = (op === 'repay_in' && after < 0) || (op === 'repay_out' && after > 0) ? String(Math.abs(after)) : null
    emit(['transactions', 'accounts', 'stats', 'debts'])
    return { t, actionId, d, op, balance_after: String(after), excess }
  }

  /** like the backend (ai/debt_cards.balance_text): the balance part of a card subtitle */
  function balanceText(op, name, bal, cur) {
    if (bal === 0) return 'долг закрыт'
    const amt = fmt(bal, cur)
    if (op === 'repay_in') return bal > 0 ? `осталось ${amt}` : 'долг закрыт'
    if (op === 'repay_out') return bal < 0 ? `осталось ${amt}` : 'долг закрыт'
    if (op === 'forgive') return `осталось ${amt}`
    return bal > 0 ? `${name}: долг тебе ${amt}` : `ты должен ${amt}`
  }

  const VERB = { lend: 'Дал в долг', repay_in: 'Возврат долга', borrow: 'Занял', repay_out: 'Вернул долг', forgive: 'Списан долг' }
  function card(r) {
    const { t, actionId, d, op, balance_after: after, excess } = r
    const debt = { debt_id: d.id, name: d.name, op, currency: t.currency, balance_after: after, excess }
    const title = `${fmt(t.amount, t.currency)} · ${VERB[op]}: ${d.name}`
    const owes = balanceText(op, d.name, Number(after), t.currency)
    const undo = { id: 'undo', label: 'Отменить', style: 'ghost', hotkey: 'Ctrl+Z', action: { kind: 'undo', action_id: actionId } }
    if (t.status === 'needs_account') {
      const q = op === 'repay_in' || op === 'borrow' ? 'На какой счёт?' : 'С какого счёта?'
      return { kind: 'needs_account', title, subtitle: `${owes} · ≈ $${t.amount_usd}`, question: q, action_id: actionId, transaction: pub(t), debt,
        options: [{ account_id: 3, label: 'Кошелёк', hint: null, hotkey: '1' }, { account_id: 4, label: 'Копилка', hint: null, hotkey: '2' }, { account_id: null, label: 'Без счёта', hint: null, hotkey: '0' }],
        buttons: [
          { id: 'acc-3', label: 'Кошелёк', style: 'outline', hotkey: '1', action: { kind: 'choose_account', transaction_id: t.id, account_id: 3 } },
          { id: 'acc-4', label: 'Копилка', style: 'outline', hotkey: '2', action: { kind: 'choose_account', transaction_id: t.id, account_id: 4 } },
          { id: 'acc-none', label: 'Без счёта', style: 'ghost', hotkey: '0', action: { kind: 'choose_account', transaction_id: t.id, account_id: null } },
          { id: 'later', label: 'Позже', style: 'ghost', hotkey: null, action: { kind: 'later' } }, undo,
        ] }
    }
    const real = (op === 'repay_in' || op === 'borrow' ? t.counter_account : t.account)?.name ?? 'Без счёта'
    return { kind: 'created', title, subtitle: `${op === 'forgive' ? t.category.name : real} · ${owes}`, action_id: actionId, transaction: pub(t), category_is_new: false,
      approx: t.currency === 'VND' ? null : `≈ $${t.amount_usd}`, note: excess ? `Излишек ${fmt(excess, t.currency)} — теперь ты должен: ${d.name}` : null, debt,
      buttons: [...(excess ? [op === 'repay_in'
        ? { id: 'excess', label: 'Излишек — мне', style: 'outline', hotkey: null, action: { kind: 'send_text', text: `списать мой долг: ${d.name} ${excess}` } }
        : { id: 'excess', label: 'Излишек — расход', style: 'outline', hotkey: null, action: { kind: 'send_text', text: `простить долг: ${d.name} ${excess}` } }] : []), undo,
        { id: 'edit', label: 'Изменить', style: 'ghost', hotkey: 'Ctrl+E', action: { kind: 'edit', transaction_id: t.id } }] }
  }

  /** assistant phrases: «дал в долг коле 500к», «занял у пети 1м», «коля вернул 200к», «кто мне должен» */
  function assistant(text) {
    const t = text.trim().toLowerCase()
    if (/^(кто мне должен|мои долги|долги)$/.test(t)) {
      const l = list(false)
      return { route: 'parser', changed: [], cards: [{ kind: 'answer', answer_type: 'balance', title: 'Долги', subtitle: null,
        headline: `Мне должны ≈ ${fmt(l.totals.owed_to_me_vnd)}`, headline_approx: `≈ $${l.totals.owed_to_me_usd}`, text: `Я должен ≈ ${fmt(l.totals.i_owe_vnd)}`,
        lines: [...l.owed_to_me, ...l.i_owe].map((d) => ({ label: d.name, value: d.balances.map((b) => fmt(b.amount, b.currency)).join(' · '), amount: null, currency: null, share: null, category_id: null, transaction_id: null, icon: null })),
        transactions: [], total_count: null, filter: null, debts: l,
        buttons: [{ id: 'open', label: 'Открыть долги →', style: 'ghost', hotkey: null, action: { kind: 'open', route: '/accounts?debt=all' } }] }] }
    }
    const num = '(\\d+(?:\\.\\d+)?)\\s*(к|k|м|m)?\\s*(usdt|ton)?'
    const amountOf = (n, m, coin) => { const mult = m === 'к' || m === 'k' ? 1000 : m === 'м' || m === 'm' ? 1e6 : !coin && Number(n) < 1000 ? 1000 : 1; return { amount: String(Math.round(Number(n) * mult * 1e8) / 1e8), currency: coin ? coin.toUpperCase() : 'VND' } }
    let m = new RegExp(`^дал в долг (\\S+) ${num}$`).exec(t)
    let op = m ? 'lend' : null
    let who = m?.[1]
    if (!m) { m = new RegExp(`^занял у (\\S+) ${num}$`).exec(t); if (m) { op = 'borrow'; who = { пети: 'Петя', коли: 'Коля', васи: 'Вася' }[m[1]] ?? nominative(m[1]) } }
    if (!m) { m = new RegExp(`^(\\S+) вернул(?: долг)? ${num}$`).exec(t); if (m) { op = 'repay_in'; who = m[1] } }
    if (!m) return null
    const { amount, currency } = amountOf(m[2], m[3], m[4])
    const d = getOrCreate(nominative(who))
    const r = record({ op, d, amount, currency })
    if (r.error) return { route: 'parser', changed: [], cards: [{ kind: 'unclear', title: r.error, subtitle: null, reason: 'other', question: r.error, keep_input: true, buttons: [] }] }
    return { route: 'parser', changed: ['transactions', 'accounts', 'stats', 'debts'], action_ids: [r.actionId], cards: [card(r)] }
  }

  function seed() {
    const mk = (name, op, amount, currency, daysAgo, accountId) => {
      const d = getOrCreate(name)
      record({ op, d, amount, currency, accountId, occurred_on: dayShift(today(), daysAgo) })
    }
    mk('Коля', 'lend', 500000, 'VND', 30, 1)
    mk('Коля', 'repay_in', 200000, 'VND', 7, 1)
    mk('Вася', 'lend', 50, 'USDT', 5, 3)
    mk('Петя', 'borrow', 1000000, 'VND', 2, 1)
    mk('Лена', 'lend', 100000, 'VND', 40, 2)
    mk('Лена', 'repay_in', 100000, 'VND', 10, 2)
    const misha = getOrCreate('Миша'); misha.handle = 'misha_dev'
    record({ op: 'lend', d: misha, amount: 200000, currency: 'VND', accountId: 2, occurred_on: dayShift(today(), 12) })
    record({ op: 'borrow', d: misha, amount: 20, currency: 'USDT', accountId: 3, occurred_on: dayShift(today(), 1) })
  }

  /** test hook: one operation as if recorded by another client ({op, person_name, amount, currency, account_id?, account_none?}) */
  function testOp(b) {
    const d = getOrCreate(String(b.person_name).trim())
    return record({ op: b.op, d, amount: b.amount == null ? null : Number(b.amount), currency: b.currency, accountId: b.account_id, accountNone: b.account_none, occurred_on: b.occurred_on })
  }

  /** HTTP: returns true when handled */
  function route({ p, method, b, url, key, err, send, done }) {
    if (p === '/debts' && method === 'GET') { send(200, list(url.searchParams.get('include_settled') === 'true')); return true }
    if (p === '/debts/operations' && method === 'POST') {
      if (!key) { err(428, 'idempotency_key_required', 'Нужен Idempotency-Key'); return true }
      const d = b.debt_id ? ctx.S.debts.find((x) => x.id === b.debt_id) : getOrCreate(String(b.person_name ?? '').trim())
      if (!d || !d.name) { err(422, 'validation_error', 'Кто?'); return true }
      if (b.handle && !d.handle) d.handle = b.handle
      const r = record({ op: b.op, d, amount: b.amount == null ? null : Number(b.amount), currency: b.currency, accountId: b.account_id, accountNone: b.account_none, occurred_on: b.occurred_on })
      if (r.error) { err(422, 'validation_error', r.error); return true }
      done(201, { transaction: pub(r.t), action_id: r.actionId, debt: out(d), balance_after: r.balance_after, excess: r.excess, needs_account: r.t.status === 'needs_account' })
      return true
    }
    const m = /^\/debts\/(\d+)(\/snooze)?$/.exec(p)
    if (!m) return false
    const d = ctx.S.debts.find((x) => x.id === Number(m[1]))
    if (!d) { err(404, 'not_found', 'Не найдено'); return true }
    if (m[2] && method === 'POST') { send(200, out(d)); return true }
    if (method === 'GET') { send(200, detail(d)); return true }
    if (method === 'PATCH') {
      if (b.version !== d.version) { err(409, 'version_conflict', 'Запись изменилась'); return true }
      if (b.is_archived && balancesOf(d).length) { err(422, 'validation_error', 'Сначала закройте долг'); return true }
      for (const k of ['name', 'handle', 'note', 'is_archived', 'aliases']) if (k in b) d[k] = b[k]
      d.version++
      for (const t of ctx.S.txs) if (t.debt?.id === d.id) { t.debt.name = d.name; t.counterparty = d.name }
      emit(['debts', 'transactions'])
      send(200, out(d)); return true
    }
    if (method === 'DELETE') {
      if (ctx.S.txs.some((t) => t.debt?.id === d.id && !t.deleted)) { err(422, 'validation_error', 'Есть операции — только архив'); return true }
      ctx.S.debts = ctx.S.debts.filter((x) => x !== d); send(200, { ok: true }); return true
    }
    return false
  }

  return { route, assistant, seed, testOp }
}

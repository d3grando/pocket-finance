// Mock of the Pocket API (docs/API.md subset) for Playwright smoke tests. Plain Node http, no deps.
//   node e2e/mock-api.mjs [port=15201]
// Implements cookie session + CSRF, Bearer device token, Idempotency-Key replay (no duplicates),
// SSE stream, and a tiny «parser» for the assistant. Test hooks: POST /__test/reset, GET /__test/state,
// POST /__test/emit (SSE change), POST /__test/fail-next (next assistant call → 500).
import http from 'node:http'
import { randomUUID } from 'node:crypto'
import { createDebts } from './mock-debts.mjs'

const PORT = Number(process.argv[2] ?? process.env.MOCK_PORT ?? 15201)
const PASSWORD = 'test-password-123'
const DEVICE_TOKEN = 'pfd_test'
const P = '/api/v1'

let S
function reset() {
  S = {
    sessions: new Set(),
    onboarding_done: false,
    idem: new Map(), // key → { hash, status, body }
    txSeq: 200,
    txs: [],
    actions: new Map(), // id → { kind, txIds, undone }
    openings: {},
    sse: new Set(),
    failNext: 0,
    calls: [],
    deviceLogins: [], // { device_name, platform } of POST /auth/device-login
    debts: [], // people (mock-debts.mjs); balances are derived from operations with `debt`
    debtSeq: 0,
    settings: { timezone: 'Asia/Ho_Chi_Minh', default_currency: 'VND', analytics_currency: 'USD', summary_currency: 'VND', small_amount_thousands: true, theme: 'system', desktop_hotkey: 'Ctrl+Alt+Space', telegram_user_id: null, report_monthly: true, report_daily: false, report_weekly: false, report_time: '10:00:00', debt_reminders_enabled: false, debt_reminder_days: 30, version: 1 },
  }
}
reset()

const accounts = [
  { id: 1, name: 'Карта', kind: 'bank', currency: 'VND', is_multi_asset: false, aliases: ['карта'], default_for_currencies: ['VND'], icon: 'Landmark' },
  { id: 2, name: 'Наличные', kind: 'cash', currency: 'VND', is_multi_asset: false, aliases: ['налом'], default_for_currencies: [], icon: 'Wallet' },
  { id: 3, name: 'Кошелёк', kind: 'crypto_wallet', currency: null, is_multi_asset: true, aliases: ['Кошелёк'], default_for_currencies: [], icon: 'Bot' },
  { id: 4, name: 'Копилка', kind: 'crypto_wallet', currency: null, is_multi_asset: true, aliases: ['Копилка'], default_for_currencies: [], icon: 'Bot' },
  { id: 5, name: 'Карта RUB', kind: 'card', currency: 'RUB', is_multi_asset: false, aliases: [], default_for_currencies: ['RUB'], icon: 'CreditCard' },
]
const cats = [
  ['Еда', 'expense', false, 'UtensilsCrossed'], ['Продукты', 'expense', false, 'ShoppingBasket'], ['Кофе', 'expense', false, 'Coffee'],
  ['Такси/транспорт', 'expense', false, 'CarTaxiFront'], ['Подписки', 'expense', false, 'RefreshCcw'], ['Прочее', 'expense', false, 'Circle'],
  ['Серверы/инфра', 'expense', true, 'Server'], ['Софт и AI', 'expense', true, 'Bot'], ['Клиенты', 'income', true, 'BriefcaseBusiness'],
  ['Зарплата', 'income', false, 'Banknote'],
].map(([name, kind, is_business, icon], i) => ({
  id: i + 1, name, kind, is_business, icon, color: null, aliases: [String(name).toLowerCase()], is_system: true, is_new: false,
  is_archived: false, sort_order: i, version: 1, tx_count: 0,
}))

const today = () => new Date(Date.now() + 7 * 3600_000).toISOString().slice(0, 10)
const me = (auth = 'session') => ({ user_id: 1, username: 'owner', auth, source: auth === 'device' ? 'desktop' : 'web', onboarding_done: S.onboarding_done, has_passkeys: false, csrf_token: auth === 'session' ? 'csrf-test' : null })
const err = (res, status, code, message, extra = {}) => send(res, status, { error: { code, message, details: {}, request_id: 'mock', ...extra } })
function send(res, status, body, headers = {}) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'X-Request-Id': 'mock', ...headers })
  res.end(body === undefined ? '' : JSON.stringify(body))
}
const cookies = (req) => Object.fromEntries((req.headers.cookie ?? '').split(/;\s*/).filter(Boolean).map((c) => c.split('=')))
function principal(req) {
  const a = req.headers.authorization
  if (a === `Bearer ${DEVICE_TOKEN}`) return 'device'
  if (S.sessions.has(cookies(req).pf_session)) return 'session'
  return null
}

function accountOut(a) {
  const asset = a.currency ?? 'USDT'
  const opening = S.openings[`${a.id}:${asset}`] ?? null
  let balance = opening
  if (opening != null) {
    for (const t of S.txs) if (!t.deleted && t.account?.id === a.id && t.status === 'posted') balance = String(Number(balance) + (t.kind === 'expense' ? -Number(t.amount) : Number(t.amount)))
  }
  return {
    ...a, color: null, sort_order: a.id, is_archived: false, version: 1,
    assets: [{ asset, opening_balance: opening, opening_at: opening ? new Date().toISOString() : null, balance, approx_usd: balance && asset === 'VND' ? (Number(balance) / 26000).toFixed(2) : balance, approx_vnd: balance }],
  }
}

function makeTx({ amount, currency, cat, accountId, status = 'posted', raw }) {
  const c = cats.find((x) => x.name === cat) ?? cats[5]
  const acc = accounts.find((a) => a.id === accountId) ?? null
  const now = new Date()
  const t = {
    id: ++S.txSeq, kind: c.kind === 'income' ? 'income' : 'expense', status, occurred_at: now.toISOString(), occurred_on: today(),
    amount, currency, account: acc && status === 'posted' ? { id: acc.id, name: acc.name, icon: acc.icon } : null,
    account_amount: acc ? amount : null, account_asset: acc ? currency : null, account_rate: acc ? '1' : null, is_estimated: false,
    counter_account: null, counter_amount: null, counter_asset: null,
    category: { id: c.id, name: c.name, kind: c.kind, icon: c.icon, color: null, is_business: c.is_business, is_new: false },
    is_business: c.is_business, merchant: null, counterparty: null, description: null,
    amount_usd: currency === 'VND' ? (Number(amount) / 26000).toFixed(2) : amount, amount_vnd: currency === 'VND' ? amount : String(Number(amount) * 26000),
    usd_rate: null, rate_date: today(), rate_source: 'vietcombank', refund_of_id: null, recurring_id: null, source: 'web', raw_text: raw ?? null,
    created_action_id: null, version: 1, created_at: now.toISOString(), updated_at: now.toISOString(), title: c.name, deleted: false,
  }
  S.txs.unshift(t)
  const actionId = randomUUID()
  t.created_action_id = actionId
  S.actions.set(actionId, { kind: 'create', txIds: [t.id], undone: false })
  return { t, actionId }
}
const pub = (t) => { const { deleted, ...rest } = t; void deleted; return rest }
const fmt = (n) => String(Math.round(Number(n))).replace(/\B(?=(\d{3})+(?!\d))/g, ' ')

function emit(entities) {
  const data = JSON.stringify({ entities, action_id: null, source: 'web', ts: new Date().toISOString() })
  for (const r of S.sse) r.write(`event: change\ndata: ${data}\n\n`)
}

const debts = createDebts({ get S() { return S }, accounts, cats, today, emit, newTxId: () => ++S.txSeq })

function assistant(text) {
  const t = text.trim().toLowerCase()
  const debt = debts.assistant(text)
  if (debt) return debt
  if (t.startsWith('сколько')) {
    const spent = S.txs.filter((x) => !x.deleted && x.kind === 'expense').reduce((s, x) => s + Number(x.amount_vnd), 0)
    return { route: 'parser', cards: [{ kind: 'answer', answer_type: 'stats', title: 'Сегодня потрачено', subtitle: null, headline: `−${fmt(spent)} ₫`, headline_approx: null, lines: [], transactions: [], total_count: null, filter: null, text: null, buttons: [{ id: 'open', label: 'Открыть в аналитике →', style: 'ghost', hotkey: null, action: { kind: 'open', route: '/analytics?period=today' } }] }], changed: [] }
  }
  const usdt = /(\d+(?:\.\d+)?)\s*usdt/.exec(t)
  if (usdt) {
    const { t: tx, actionId } = makeTx({ amount: usdt[1], currency: 'USDT', cat: 'Подписки', status: 'needs_account', raw: text })
    return { route: 'parser', changed: ['transactions', 'stats'], action_ids: [actionId], cards: [{
      kind: 'needs_account', title: `${usdt[1]} USDT · Подписки`, subtitle: `≈ $${usdt[1]}`, question: 'С какого счёта?', action_id: actionId, transaction: pub(tx),
      options: [{ account_id: 3, label: 'Кошелёк', hint: null, hotkey: '1' }, { account_id: 4, label: 'Копилка', hint: null, hotkey: '2' }, { account_id: null, label: 'Без счёта', hint: null, hotkey: '0' }],
      buttons: [
        { id: 'acc-3', label: 'Кошелёк', style: 'outline', hotkey: '1', action: { kind: 'choose_account', transaction_id: tx.id, account_id: 3 } },
        { id: 'acc-4', label: 'Копилка', style: 'outline', hotkey: '2', action: { kind: 'choose_account', transaction_id: tx.id, account_id: 4 } },
        { id: 'acc-none', label: 'Без счёта', style: 'ghost', hotkey: '0', action: { kind: 'choose_account', transaction_id: tx.id, account_id: null } },
        { id: 'later', label: 'Позже', style: 'ghost', hotkey: null, action: { kind: 'later' } },
        { id: 'undo', label: 'Отменить', style: 'ghost', hotkey: null, action: { kind: 'undo', action_id: actionId } },
      ],
    }] }
  }
  const m = /^(\S+)\s+(\d+(?:\.\d+)?)\s*(к|k|м|m)?$/.exec(t)
  if (!m) {
    return { route: 'none', changed: [], cards: [{ kind: 'unclear', title: 'Не понял, что записать', subtitle: null, reason: 'missing_amount', question: `«${text}» — не хватает суммы.`, keep_input: true,
      buttons: [{ id: 'manual', label: 'Вручную', style: 'ghost', hotkey: null, action: { kind: 'manual', prefill: { text } } }] }] }
  }
  const mult = m[3] === 'к' || m[3] === 'k' ? 1000 : m[3] === 'м' || m[3] === 'm' ? 1e6 : Number(m[2]) < 1000 ? 1000 : 1
  const amount = String(Math.round(Number(m[2]) * mult))
  const cat = { еда: 'Еда', кофе: 'Кофе', такси: 'Такси/транспорт', продукты: 'Продукты' }[m[1]] ?? 'Прочее'
  const { t: tx, actionId } = makeTx({ amount, currency: 'VND', cat, accountId: 1, raw: text })
  return { route: 'parser', changed: ['transactions', 'accounts', 'stats'], action_ids: [actionId], cards: [{
    kind: 'created', title: `${fmt(amount)} ₫ · ${cat}`, subtitle: 'Карта · сейчас', action_id: actionId, transaction: pub(tx), category_is_new: false, approx: null, note: null,
    buttons: [
      { id: 'undo', label: 'Отменить', style: 'ghost', hotkey: 'Ctrl+Z', action: { kind: 'undo', action_id: actionId } },
      { id: 'edit', label: 'Изменить', style: 'ghost', hotkey: 'Ctrl+E', action: { kind: 'edit', transaction_id: tx.id } },
    ],
  }] }
}

function summary() {
  const live = S.txs.filter((x) => !x.deleted)
  const sum = (f) => String(live.filter(f).reduce((s, x) => s + Number(x.amount_vnd), 0))
  const spent = sum((x) => x.kind === 'expense')
  const income = sum((x) => x.kind === 'income')
  const kpi = (v) => ({ value: v, currency: 'VND', approx_usd: (Number(v) / 26000).toFixed(2), change_ratio: null, sparkline: [] })
  return {
    currency: 'VND', today: { date_from: today(), date_to: today(), label: 'Сегодня' }, month: { date_from: `${today().slice(0, 8)}01`, date_to: today(), label: 'Октябрь 2026' },
    spent_today: kpi(spent), spent_month: kpi(spent), income_month: kpi(income), net_month: kpi(String(Number(income) - Number(spent))),
    needs_account_count: live.filter((x) => x.status === 'needs_account').length, missing_rates_count: 0,
  }
}

async function body(req) {
  const chunks = []
  for await (const c of req) chunks.push(c)
  const raw = Buffer.concat(chunks).toString('utf8')
  return { raw, json: raw ? JSON.parse(raw) : {} }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`)
  const path = url.pathname
  const method = req.method
  const { raw, json: b } = method === 'GET' ? { raw: '', json: {} } : await body(req)
  S.calls.push({ method, path, idem: req.headers['idempotency-key'] ?? null, auth: req.headers.authorization ?? null })

  // ---- test hooks
  if (path === '/__test/reset') { reset(); return send(res, 200, { ok: true }) }
  if (path === '/__test/state') return send(res, 200, { txs: S.txs.filter((x) => !x.deleted).map(pub), calls: S.calls, onboarding_done: S.onboarding_done, deviceLogins: S.deviceLogins })
  if (path === '/__test/emit') { emit(b.entities ?? ['transactions']); return send(res, 200, { ok: true }) }
  if (path === '/__test/fail-next') { S.failNext = Number(b.count ?? 1); return send(res, 200, { ok: true }) }
  if (path === '/__test/onboarded') { S.onboarding_done = true; return send(res, 200, { ok: true }) }
  if (path === '/__test/debts-seed') { debts.seed(); return send(res, 200, { ok: true }) }
  if (path === '/__test/debt-op') { const r = debts.testOp(b); return send(res, r.error ? 422 : 200, r.error ? { error: r.error } : { id: r.t.id, debt_id: r.d.id }) }
  if (path === '/__test/patch-tx') { // another client (the bot) changed an operation
    const t = S.txs.find((x) => x.id === Number(b.id)) ?? S.txs[0]
    t.amount = String(b.amount); t.amount_vnd = String(b.amount); t.account_amount = String(b.amount); t.version++
    emit(['transactions', 'stats'])
    return send(res, 200, pub(t))
  }

  if (!path.startsWith(P)) return err(res, 404, 'not_found', 'Не найдено')
  const p = path.slice(P.length)
  if (p === '/health') return send(res, 200, { status: 'ok', db: true, version: '0.1.0', server_time: new Date().toISOString() })

  if (p === '/auth/login' && method === 'POST') {
    if (b.password !== PASSWORD) return err(res, 401, 'unauthorized', 'Неверный пароль')
    const sid = randomUUID()
    S.sessions.add(sid)
    return send(res, 200, me('session'), { 'Set-Cookie': [`pf_session=${sid}; Path=/; HttpOnly; SameSite=Lax`, 'pf_csrf=csrf-test; Path=/; SameSite=Strict'] })
  }
  if (p === '/auth/device-login' && method === 'POST') {
    // same enum as the backend schema (DeviceLoginIn.platform): 'win32' etc. → 422
    if (!['windows', 'macos', 'linux'].includes(b.platform) || !b.device_name) return err(res, 422, 'validation_error', 'Проверьте введённые данные')
    S.deviceLogins.push({ device_name: b.device_name, platform: b.platform })
    if (b.password !== PASSWORD) return err(res, 401, 'unauthorized', 'Неверный пароль')
    return send(res, 201, { token: DEVICE_TOKEN, device_id: randomUUID() })
  }
  if (p === '/passkeys/login/options') return err(res, 401, 'unauthorized', 'Нет ключей')

  const who = principal(req)
  if (!who) return err(res, 401, 'unauthorized', 'Нужен вход')
  if (method !== 'GET' && who === 'session' && req.headers['x-csrf-token'] !== 'csrf-test') return err(res, 403, 'csrf_failed', 'CSRF')

  // idempotency (ARCH §5.2): same key + same body → same response, never a duplicate
  const key = req.headers['idempotency-key']
  const idemKey = key ? `${method} ${p} ${key}` : null
  if (idemKey && S.idem.has(idemKey)) {
    const saved = S.idem.get(idemKey)
    if (saved.hash !== raw) return err(res, 409, 'idempotency_mismatch', 'Другой запрос с тем же ключом')
    return send(res, saved.status, saved.body)
  }
  const done = (status, out) => { if (idemKey) S.idem.set(idemKey, { hash: raw, status, body: out }); return send(res, status, out) }

  if (p === '/auth/me') return send(res, 200, me(who))
  if (p === '/auth/logout') return send(res, 200, { ok: true }, { 'Set-Cookie': 'pf_session=; Path=/; Max-Age=0' })
  if (p === '/events/stream') {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive', 'X-Accel-Buffering': 'no' })
    res.write(`event: hello\ndata: ${JSON.stringify({ server_time: new Date().toISOString(), version: '0.1.0' })}\n\n`)
    S.sse.add(res)
    const ping = setInterval(() => res.write('event: ping\ndata: {}\n\n'), 5000)
    req.on('close', () => { clearInterval(ping); S.sse.delete(res) })
    return
  }
  if (p === '/settings' && method === 'PATCH') {
    if (b.version !== S.settings.version) return err(res, 409, 'version_conflict', 'Настройки изменились', { details: { current_version: S.settings.version } })
    const { version, ...rest } = b
    void version
    S.settings = { ...S.settings, ...rest, version: S.settings.version + 1 }
    return send(res, 200, { ...S.settings, onboarding_done: S.onboarding_done })
  }
  if (p === '/settings') return send(res, 200, { ...S.settings, onboarding_done: S.onboarding_done })
  if (p === '/stats/summary') return send(res, 200, summary())
  if (p === '/stats/by-category') {
    const by = new Map()
    for (const t of S.txs) if (!t.deleted && t.kind === 'expense') by.set(t.category.id, (by.get(t.category.id) ?? 0) + Number(t.amount_vnd))
    const total = [...by.values()].reduce((s, v) => s + v, 0)
    return send(res, 200, { range: { date_from: `${today().slice(0, 8)}01`, date_to: today(), label: 'Октябрь 2026' }, kind: 'expense', currency: 'VND', total: String(total),
      items: [...by.entries()].map(([id, v]) => { const c = cats.find((x) => x.id === id); return { category_id: id, name: c.name, icon: c.icon, color: null, is_business: c.is_business, is_new: false, amount: String(v), share: total ? (v / total).toFixed(3) : '0', count: 1 } }) })
  }
  if (p === '/stats/series') {
    const pts = Array.from({ length: 7 }, (_, i) => { const d = new Date(Date.now() - (6 - i) * 86400_000).toISOString().slice(0, 10); const e = S.txs.filter((t) => !t.deleted && t.occurred_on === d && t.kind === 'expense').reduce((s, t) => s + Number(t.amount_vnd), 0); return { date: d, label: d.slice(8), expense: String(e), income: '0', net: String(-e) } })
    return send(res, 200, { range: { date_from: pts[0].date, date_to: today(), label: '7 дней' }, granularity: 'day', currency: 'VND', points: pts })
  }
  if (p === '/transactions' && method === 'GET') {
    let items = S.txs.filter((t) => !t.deleted)
    if (url.searchParams.get('status')) items = items.filter((t) => t.status === url.searchParams.get('status'))
    const limit = Number(url.searchParams.get('limit') ?? 50)
    return send(res, 200, { items: items.slice(0, limit).map(pub), total: items.length, limit, offset: 0, totals: { expense_vnd: '0', income_vnd: '0', expense_usd: '0', income_usd: '0', count: items.length } })
  }
  if (p === '/transactions/needs-account/count') return send(res, 200, { count: S.txs.filter((t) => !t.deleted && t.status === 'needs_account').length })
  if (p === '/transactions' && method === 'POST') {
    if (!key) return err(res, 428, 'idempotency_key_required', 'Нужен Idempotency-Key')
    const c = cats.find((x) => x.id === b.category_id)
    // Defaults: ₫ → Карта, ₽ → Карта RUB; $ / crypto without an account → saved, «С какого?»
    const auto = b.currency === 'VND' ? 1 : b.currency === 'RUB' ? 5 : null
    const accountId = b.account_id ?? (b.account_none ? null : auto)
    const status = accountId === null && !b.account_none ? 'needs_account' : 'posted'
    const { t, actionId } = makeTx({ amount: b.amount, currency: b.currency, cat: c?.name, accountId, status })
    emit(['transactions', 'stats'])
    return done(201, { transaction: pub(t), action_id: actionId })
  }
  let m = /^\/transactions\/(\d+)\/account$/.exec(p)
  if (m && method === 'POST') {
    const t = S.txs.find((x) => x.id === Number(m[1]))
    const acc = accounts.find((a) => a.id === b.account_id)
    const ref = acc ? { id: acc.id, name: acc.name, icon: acc.icon } : null
    // a debt inflow (repay_in / borrow) waits for its receiver, not the source (DEBTS.md §3.1)
    if (t.debt && (t.debt.op === 'repay_in' || t.debt.op === 'borrow')) t.counter_account = ref
    else t.account = ref
    t.status = 'posted'; t.version++
    const actionId = randomUUID()
    S.actions.set(actionId, { kind: 'assign', txIds: [t.id], undone: false })
    return done(200, { transaction: pub(t), action_id: actionId })
  }
  m = /^\/actions\/([^/]+)\/undo$/.exec(p)
  if (m && method === 'POST') {
    if (!key) return err(res, 428, 'idempotency_key_required', 'Нужен Idempotency-Key')
    const a = S.actions.get(m[1])
    if (!a) return err(res, 404, 'not_found', 'Действие не найдено')
    if (a.kind === 'create') for (const id of a.txIds) { const t = S.txs.find((x) => x.id === id); t.deleted = !a.undone ? true : false }
    // like the backend: a creation changed afterwards can't be undone (the version moved on)
    if (a.kind === 'create' && !a.undone && S.txs.some((x) => a.txIds.includes(x.id) && x.version > 1)) {
      for (const id of a.txIds) S.txs.find((x) => x.id === id).deleted = false
      return err(res, 409, 'undo_conflict', 'Запись уже изменили после этого действия — отменить нельзя')
    }
    if (a.kind === 'delete') for (const id of a.txIds) { const t = S.txs.find((x) => x.id === id); t.deleted = a.undone; t.version++ }
    a.undone = !a.undone
    const t = S.txs.find((x) => x.id === a.txIds[0])
    const undoId = randomUUID()
    S.actions.set(undoId, { kind: a.kind, txIds: a.txIds, undone: !a.undone })
    emit(['transactions'])
    return done(200, { status: 'undone', undone_action_id: m[1], undo_action_id: undoId, summary: `Отменено: ${fmt(t.amount)} ${t.currency === 'VND' ? '₫' : t.currency} · ${t.debt ? `Долг: ${t.debt.name}` : t.category.name}`, changed: ['transactions', 'stats', ...(t.debt ? ['debts'] : [])] })
  }
  m = /^\/transactions\/(\d+)$/.exec(p)
  if (m) {
    const t = S.txs.find((x) => x.id === Number(m[1]))
    if (!t || t.deleted) return err(res, 404, 'not_found', 'Операция удалена')
    if (method === 'GET') return send(res, 200, pub(t))
    if (method === 'PATCH') {
      if (b.version !== t.version) return err(res, 409, 'version_conflict', 'Запись уже изменили, обновите данные', { current_version: t.version })
      if (b.amount) { t.amount = b.amount; t.amount_vnd = b.amount; t.account_amount = b.amount }
      if (b.category_id) {
        const c = cats.find((x) => x.id === b.category_id)
        if (c) { t.category = { id: c.id, name: c.name, kind: c.kind, icon: c.icon, color: null, is_business: c.is_business, is_new: false }; t.title = c.name }
      }
      t.version++
      const actionId = randomUUID()
      S.actions.set(actionId, { kind: 'update', txIds: [t.id], undone: false })
      emit(['transactions', 'stats'])
      return done(200, { transaction: pub(t), action_id: actionId })
    }
    if (method === 'DELETE') {
      const v = url.searchParams.get('version')
      if (v && Number(v) !== t.version) return err(res, 409, 'version_conflict', 'Запись уже изменили, обновите данные', { current_version: t.version })
      t.deleted = true; t.version++
      const actionId = randomUUID()
      S.actions.set(actionId, { kind: 'delete', txIds: [t.id], undone: false })
      emit(['transactions', 'stats'])
      return done(200, { transaction: pub(t), action_id: actionId })
    }
  }
  if (p === '/accounts') return send(res, 200, accounts.map(accountOut))
  if (p === '/accounts/balances') { const accs = accounts.map(accountOut); return send(res, 200, { accounts: accs, total_vnd: null, total_usd: accs.some((a) => a.assets[0].balance) ? '96.15' : null, unknown_count: accs.filter((a) => !a.assets[0].balance).length }) }
  if (p === '/accounts/onboarding' && method === 'POST') {
    for (const it of b.items ?? []) S.openings[`${it.account_id}:${it.asset}`] = it.amount
    S.onboarding_done = true
    S.lastOnboarding = b.items
    return send(res, 200, { accounts: accounts.map(accountOut), total_vnd: null, total_usd: null, unknown_count: 0 })
  }
  if (p === '/categories') return send(res, 200, cats)
  if (p === '/categories/suggest') return send(res, 200, { similar: [], all: cats })
  if (p === '/rates/latest') return send(res, 200, { usd_vnd: { base: 'USD', quote: 'VND', rate: '26000', rate_date: today(), kind: 'sell', source: 'vietcombank', fetched_at: null, is_fallback: false, label: '' }, usd_rub: null, crypto: [] })
  if (p.startsWith('/debts') && debts.route({ p, method, b, url, key, err: (st, code, msg) => err(res, st, code, msg), send: (st, body) => send(res, st, body), done })) return
  if (p === '/assistant/messages' && method === 'POST') {
    if (!key) return err(res, 428, 'idempotency_key_required', 'Нужен Idempotency-Key')
    if (S.failNext > 0) { S.failNext--; return err(res, 500, 'internal', 'boom') } // 5xx: key not stored
    const r = assistant(b.text)
    const out = { request_id: S.calls.length, ai_available: true, action_ids: r.action_ids ?? [], route: r.route, changed: r.changed, cards: r.cards }
    if (r.changed.length) emit(r.changed)
    return done(200, out)
  }
  return err(res, 404, 'not_found', `mock: ${method} ${p}`)
})

server.listen(PORT, '127.0.0.1', () => console.log(`mock api on ${PORT}`))

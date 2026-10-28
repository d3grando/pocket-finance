// Contract-shaped mock of /api/v1 for Playwright (page.route). Records mutations for assertions.
import type { Page, Route } from '@playwright/test'
import { accounts, balances, categories, page as txPage, sampleTxs, settings, tx } from '../test/fixtures'

export interface Recorded {
  method: string
  path: string
  query: Record<string, string>
  body: unknown
  headers: Record<string, string>
}

const cs = (id: number, name: string, amount: string, share: string, icon: string, is_business = false) => ({ category_id: id, name, icon, color: null, is_business, is_new: false, amount, share, count: 3 })

export async function mockApi(page: Page, opts: { many?: number } = {}): Promise<Recorded[]> {
  const rec: Recorded[] = []
  // Real-life alias lists are long (e.g. «нал», «налом», «наличные», …) — they must not widen the page.
  const accs = accounts.map((a) => (a.id === 1 ? { ...a, aliases: ['карта', 'банк', 'основной'] } : a.id === 2 ? { ...a, aliases: ['нал', 'налом', 'наличные', 'наличка', 'кэш', 'cash', 'кеш'] } : a))
  const all = opts.many
    ? Array.from({ length: opts.many }, (_, i) => tx({ id: 5000 + i, title: `Покупка ${i}`, amount: String(10000 + i * 1000), occurred_on: `2026-10-${String(27 - Math.floor(i / 25)).padStart(2, '0')}` }))
    : sampleTxs
  await page.route('**/api/v1/**', async (route: Route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname.replace(/^\/api\/v1/, '')
    const method = req.method()
    const q = Object.fromEntries(url.searchParams)
    const body = req.postData() ? JSON.parse(req.postData()!) : undefined
    if (method !== 'GET') rec.push({ method, path, query: q, body, headers: req.headers() })
    const json = (data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
    const range = { date_from: q.date_from ?? '2026-10-01', date_to: q.date_to ?? '2026-10-31', label: '' }
    const cur = q.currency ?? 'USD'
    const k = cur === 'VND' ? 26140 : 1
    const m = (v: number) => String(Math.round(v * k * 100) / 100)

    if (path === '/events/stream') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'event: hello\ndata: {"server_time":"2026-10-27T07:00:00Z","version":"0.1.0"}\n\n' })
    if (path === '/health') return json({ status: 'ok', db: true, version: '0.1.0', server_time: '2026-10-27T07:00:00Z' })
    if (path === '/auth/me') return json({ user_id: 1, username: 'owner', auth: 'session', source: 'web', onboarding_done: true, has_passkeys: true, csrf_token: 'csrf' })
    if (path === '/settings' && method === 'GET') return json(settings)
    if (path === '/settings' && method === 'PATCH') return json({ ...settings, ...body, version: settings.version + 1 })
    if (path === '/settings/server') return json({ ai_base_url: 'https://api.example.com/v1', ai_model: 'your-model', ai_configured: true, telegram_bot_configured: true, telegram_bot_username: 'pocket_test_bot', version: '0.1.0', public_url: 'https://pocket.example.com' })
    if (path === '/categories' && method === 'GET') return json(categories)
    if (path === '/accounts' && method === 'GET') return json(accs)
    if (path === '/accounts/balances') return json({ ...balances, accounts: accs })
    if (path === '/transactions/needs-account/count') return json({ count: 1 })
    if (path === '/transactions' && method === 'GET') {
      let items = all
      if (q.status === 'needs_account') items = items.filter((t) => t.status === 'needs_account')
      if (q.q) items = items.filter((t) => t.title.toLowerCase().includes(q.q.toLowerCase()))
      if (q.account_id) items = items.filter((t) => String(t.account?.id) === q.account_id)
      const off = Number(q.offset ?? 0)
      const lim = Number(q.limit ?? 50)
      return json({ ...txPage(items.slice(off, off + lim), items.length, off), limit: lim })
    }
    let mt: RegExpExecArray | null
    if ((mt = /^\/transactions\/(\d+)$/.exec(path))) {
      const t = all.find((x) => x.id === Number(mt![1])) ?? sampleTxs[0]
      if (method === 'GET') return json(t)
      if (method === 'PATCH') return json({ transaction: { ...t, ...body, version: t.version + 1 }, action_id: 'act-1' })
      if (method === 'DELETE') return json({ transaction: t, action_id: 'act-del' })
    }
    if (/^\/transactions\/\d+\/history$/.test(path)) return json([{ id: 1, entity_type: 'transaction', entity_id: '1', operation: 'create', before: null, after: {}, action_id: null, source: 'telegram', created_at: '2026-10-27T14:32:05+07:00' }])
    if (/^\/transactions\/\d+\/account$/.test(path)) return json({ transaction: { ...sampleTxs[5], status: 'posted' }, action_id: 'act-acc' })
    if (/^\/actions\/[^/]+\/undo$/.test(path)) return json({ status: 'undone', undone_action_id: 'x', undo_action_id: 'y', summary: 'Отменено', changed: ['transactions'] })
    if (path === '/transactions' && method === 'POST') return json({ transaction: tx({ id: 9999, ...body }), action_id: 'act-new' }, 201)
    // stats
    if (path === '/stats/compare') return json({ currency: cur, current: { range, expense: m(476.3), income: m(700.1), net: m(223.8) }, previous: { range, expense: m(425.1), income: m(730), net: m(304.9) }, expense_change_ratio: '0.12', income_change_ratio: '-0.04', by_category: [] })
    if (path === '/stats/daily-average') return json({ range, currency: cur, days: 27, total: m(476.3), average: m(17.64) })
    if (path === '/stats/business') return json({ range, currency: cur, business_income: m(1100), business_expense: m(320), business_net: m(780), personal_income: m(47.6), personal_expense: m(156.3), expense_by_category: [], income_by_category: [] })
    if (path === '/stats/series') {
      const monthG = q.granularity === 'month'
      const points = monthG
        ? Array.from({ length: 12 }, (_, i) => ({ date: `${i < 2 ? 2025 : 2026}-${String(((i + 10) % 12) + 1).padStart(2, '0')}-01`, label: '', expense: m(300 + ((i * 37) % 200)), income: m(600), net: m(200) }))
        : Array.from({ length: 31 }, (_, i) => ({ date: `2026-10-${String(i + 1).padStart(2, '0')}`, label: '', expense: m(i < 27 ? 5 + ((i * 13) % 30) : 0), income: m(i % 7 === 0 ? 120 : 0), net: '0' }))
      return json({ range, granularity: q.granularity ?? 'day', currency: cur, points })
    }
    if (path === '/stats/by-category') {
      const prev = q.date_from !== '2026-10-01'
      const items = [cs(1, 'Еда', m(prev ? 140 : 180.2), '0.378', 'UtensilsCrossed'), cs(4, 'Такси/транспорт', m(prev ? 81 : 68), '0.143', 'CarTaxiFront'), cs(14, 'Серверы/инфра', m(64), '0.134', 'Server', true), cs(2, 'Продукты', m(55), '0.115', 'ShoppingBasket'), cs(3, 'Кофе', m(40), '0.084', 'Coffee'), cs(5, 'Подписки', m(30), '0.063', 'RefreshCcw'), cs(15, 'Софт и AI', m(20), '0.042', 'Bot', true), cs(30, 'Вейпы', m(10), '0.021', 'Tag'), cs(31, 'Подарки', m(9.1), '0.02', 'Gift')]
      return json({ range, kind: q.kind ?? 'expense', currency: cur, total: m(476.3), items: q.kind === 'income' ? [cs(20, 'Клиенты', m(700.1), '1', 'BriefcaseBusiness', true)] : items })
    }
    if (path === '/stats/top') return json({ range, currency: cur, items: [1, 2, 3, 4, 5].map((i) => ({ transaction_id: i, occurred_on: '2026-10-12', title: ['Серверы/инфра · vast.ai', 'Жильё · Аренда', 'Покупки · Клавиатура', 'Еда · Ресторан', 'Софт и AI · GPT Pro'][i - 1], category: null, amount: m(900 / i), currency: cur, original_amount: '21300000', original_currency: 'VND' })) })
    // settings sections
    if (path === '/merchants') return json([{ id: 4, name: 'vast.ai', aliases: ['васт', 'vast'], handle: null, category_id: 14, category_name: 'Серверы/инфра', default_account_id: null, hits: 9, last_used_at: null }])
    if (path === '/my-accounts') return json([])
    if (path === '/recurring') return json([{ id: 2, title: 'Hetzner', kind: 'expense', amount: '45', currency: 'USD', account_id: null, category_id: 14, merchant_id: null, description: null, frequency: 'monthly', interval: 1, day_of_month: 5, weekday: null, month: null, next_due_on: '2026-11-05', remind_time: '10:00:00', is_active: true, last_reminded_on: '2026-10-05', version: 4 }])
    if (path === '/passkeys') return json([{ id: 'p1', name: 'iPhone', created_at: '2026-10-20T10:00:00+07:00', last_used_at: '2026-10-27T09:00:00+07:00' }])
    if (path === '/devices') return json([{ id: 'd1', name: 'DESKTOP-ABC', platform: 'windows', created_at: '2026-10-20T10:00:00+07:00', last_used_at: '2026-10-27T09:00:00+07:00', is_current: false }])
    if (path === '/auth/sessions') return json([{ id: 's1', user_agent: 'Mozilla/5.0 (iPhone) Safari/605', ip: '1.2.3.4', created_at: '2026-10-20T10:00:00+07:00', last_seen_at: '2026-10-27T10:00:00+07:00', expires_at: '2027-01-20T10:00:00+07:00', is_current: true }])
    if (path === '/rates/latest') return json({ usd_vnd: { base: 'USD', quote: 'VND', rate: '26140', rate_date: '2026-10-27', kind: 'sell', source: 'vietcombank', fetched_at: null, is_fallback: false, label: '$1 = 26 140 ₫ (VCB, 27.10)' }, usd_rub: null, crypto: [] })
    if (path.startsWith('/reports/')) return json({ kind: 'monthly', range, text_html: '', text_plain: 'Октябрь 2026\nРасходы: 12 450 000 ₫' })
    if (method !== 'GET') return json({ ok: true })
    return json({ error: { code: 'not_found', message: `mock: ${method} ${path}`, details: {}, request_id: null } }, 404)
  })
  return rec
}

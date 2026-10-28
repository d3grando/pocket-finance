import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpError, mockApi, renderPage } from './test/utils'
import { accounts, categories, page, sampleTxs, settings, tx } from './test/fixtures'
import { OperationsView } from './OperationsView'
import { ManualTxForm } from './components/ManualTxForm'

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-27T07:00:00Z'))
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const base = {
  'GET /settings': settings,
  'GET /categories': categories,
  'GET /accounts': accounts,
  'GET /transactions/needs-account/count': { count: 1 },
}

describe('OperationsView', () => {
  it('lists operations grouped by day with totals, using the URL period', async () => {
    const api = mockApi({ ...base, 'GET /transactions': page(sampleTxs) })
    renderPage(<OperationsView />, { route: '/operations', path: '/operations' })
    expect(await screen.findByText('латте · Highlands')).toBeInTheDocument()
    expect(screen.getByText(/Сегодня · вт, 27 октября/)).toBeInTheDocument()
    expect(screen.getByText(/Вчера · пн, 26 октября/)).toBeInTheDocument()
    expect(screen.getByText(/6 операций/)).toBeInTheDocument()
    expect(screen.getAllByText('Выбрать счёт').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /Разобрать: 1/ })).toBeInTheDocument()
    const q = api.find('GET', '/transactions')[0].query
    expect(q.get('date_from')).toBe('2026-10-01')
    expect(q.get('date_to')).toBe('2026-10-31')
    expect(q.get('limit')).toBe('100')
  })

  it('filters from URL (needs_account) and search box (debounced q)', async () => {
    const api = mockApi({ ...base, 'GET /transactions': (c) => page(c.query.get('q') === 'кофе' ? [sampleTxs[0]] : sampleTxs) })
    renderPage(<OperationsView />, { route: '/operations?status=needs_account&cat=5', path: '/operations' })
    await screen.findAllByText('латте · Highlands')
    const first = api.find('GET', '/transactions')[0].query
    expect(first.get('status')).toBe('needs_account')
    expect(first.getAll('category_id')).toEqual(['5'])
    expect(first.get('date_from')).toBeNull()
    expect(screen.getByText('Разобрать: без счёта')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.type(screen.getByRole('searchbox', { name: 'Поиск операций' }), 'кофе')
    await waitFor(() => expect(api.find('GET', '/transactions').some((c) => c.query.get('q') === 'кофе')).toBe(true), { timeout: 2000 })
  })

  it('edits inline: Enter sends only changed fields + version', async () => {
    const api = mockApi({
      ...base,
      'GET /transactions': page(sampleTxs),
      'PATCH /transactions/:id': (c) => ({ transaction: { ...sampleTxs[0], amount: (c.body as { amount: string }).amount, version: 2 }, action_id: 'act-edit' }),
    })
    renderPage(<OperationsView />, { route: '/operations', path: '/operations' })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /латте · Highlands/ }))
    const form = await screen.findByRole('form', { name: 'Правка операции' })
    const amount = within(form).getByLabelText('Сумма')
    await user.clear(amount)
    await user.type(amount, '50к{Enter}')
    await waitFor(() => expect(api.find('PATCH', '/transactions/1')).toHaveLength(1))
    expect(api.find('PATCH', '/transactions/1')[0].body).toEqual({ version: 1, amount: '50000' })
    expect(api.find('PATCH', '/transactions/1')[0].headers['Idempotency-Key']).toBeTruthy()
    expect(await screen.findByText(/Изменено: 50/)).toBeInTheDocument()
  })

  it('shows a clear message on version conflict and keeps the form', async () => {
    mockApi({
      ...base,
      'GET /transactions': page(sampleTxs),
      'PATCH /transactions/:id': () => {
        throw new HttpError(409, 'version_conflict', 'conflict')
      },
    })
    renderPage(<OperationsView />, { route: '/operations', path: '/operations' })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /латте · Highlands/ }))
    const form = await screen.findByRole('form', { name: 'Правка операции' })
    await user.type(within(form).getByLabelText('Описание'), ' утро{Enter}')
    expect(await within(form).findByRole('alert')).toHaveTextContent('изменилась на другом устройстве')
  })

  it('deletes with Delete key and undoes from the toast («Вернуть»)', async () => {
    const api = mockApi({
      ...base,
      'GET /transactions': page(sampleTxs),
      'DELETE /transactions/:id': { transaction: sampleTxs[0], action_id: 'act-del' },
      'POST /actions/:id/undo': { status: 'undone', undone_action_id: 'act-del', undo_action_id: 'act-undo', summary: 'Отменено: 45 000 ₫ · Кофе', changed: ['transactions'] },
    })
    renderPage(<OperationsView />, { route: '/operations', path: '/operations' })
    await screen.findByText('латте · Highlands')
    const user = userEvent.setup()
    await user.keyboard('{ArrowDown}{Delete}')
    await waitFor(() => expect(api.find('DELETE', '/transactions/1')).toHaveLength(1))
    expect(api.find('DELETE', '/transactions/1')[0].query.get('version')).toBe('1')
    expect(await screen.findByText(/Удалено: 45/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Вернуть' }))
    await waitFor(() => expect(api.find('POST', '/actions/act-del/undo')).toHaveLength(1))
    expect(api.find('POST', '/actions/act-del/undo')[0].headers['Idempotency-Key']).toMatch(/[0-9a-f-]{36}/)
    expect(await screen.findByText('Отменено: 45 000 ₫ · Кофе')).toBeInTheDocument()
  })

  it('switches to the virtualised list after 200 rows', async () => {
    const mk = (n: number) => Array.from({ length: n }, (_, i) => tx({ id: 1000 + i, title: `Покупка ${i}`, occurred_on: `2026-10-${String(27 - Math.floor(i / 20)).padStart(2, '0')}` }))
    mockApi({ ...base, 'GET /transactions': page(mk(150), 150) })
    const small = renderPage(<OperationsView />, { route: '/operations', path: '/operations' })
    await screen.findByText('Покупка 0')
    expect(small.container.querySelector('[data-virtual]')).toBeNull()
    small.unmount()
    mockApi({ ...base, 'GET /transactions': page(mk(250), 250) })
    const big = renderPage(<OperationsView />, { route: '/operations', path: '/operations' })
    await waitFor(() => expect(big.container.querySelector('[data-virtual]')).not.toBeNull())
    expect((await screen.findAllByText(/^Покупка \d+$/)).length).toBeGreaterThan(0)
  })

  it('picking an account in «Выбрать счёт» does not open the row edit form', async () => {
    const api = mockApi({ ...base, 'GET /transactions': page(sampleTxs), 'POST /transactions/:id/account': { transaction: { ...sampleTxs[5], status: 'posted' }, action_id: 'a1' } })
    renderPage(<OperationsView />, { route: '/operations', path: '/operations' })
    const user = userEvent.setup()
    await user.click((await screen.findAllByRole('button', { name: 'Выбрать счёт' }))[0])
    const pop = await screen.findByRole('dialog')
    await user.click(within(pop).getByRole('button', { name: /Кошелёк/ }))
    await waitFor(() => expect(api.find('POST', '/transactions/6/account')).toHaveLength(1))
    expect(screen.queryByRole('form', { name: 'Правка операции' })).toBeNull()
  })

  it('offline with nothing loaded shows «нет связи», never «Пока ни одной операции»', async () => {
    const { onlineManager } = await import('@tanstack/react-query')
    mockApi({ ...base, 'GET /transactions': page(sampleTxs) })
    onlineManager.setOnline(false)
    try {
      renderPage(<OperationsView />, { route: '/operations', path: '/operations' })
      expect(await screen.findByText('Нет связи — данные не загружены')).toBeInTheDocument()
      expect(screen.queryByText('Пока ни одной операции')).toBeNull()
      expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument()
    } finally {
      onlineManager.setOnline(true)
    }
  })

  it('day header shows signed expense and income separately; category column is always filled', async () => {
    const day = [tx({ id: 51, title: 'Еда', description: 'ужин', amount: '610000' }), tx({ id: 52, kind: 'income', title: 'Зарплата', amount: '25000000', category: { id: 21, name: 'Зарплата', kind: 'income', icon: 'Banknote', color: null, is_business: false, is_new: false } })]
    mockApi({ ...base, 'GET /transactions': page(day) })
    renderPage(<OperationsView />, { route: '/operations', path: '/operations' })
    expect(await screen.findByText(/−610\s000\s₫ · \+25\s000\s000\s₫/)).toBeInTheDocument()
    expect(screen.getByText('ужин')).toBeInTheDocument()
    expect(screen.getAllByText('Еда').length).toBeGreaterThan(0)
  })

  it('shows the empty state with reset when nothing matches', async () => {
    mockApi({ ...base, 'GET /transactions': page([]) })
    renderPage(<OperationsView />, { route: '/operations?q=zzz', path: '/operations' })
    expect(await screen.findByText('Ничего не нашлось')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Сбросить фильтры' })).toBeInTheDocument()
  })
})

describe('ManualTxForm', () => {
  it('works without crypto.randomUUID (plain-http stand): POST goes out with a UUID key', async () => {
    const orig = globalThis.crypto
    vi.stubGlobal('crypto', { getRandomValues: orig.getRandomValues.bind(orig) })
    try {
      const api = mockApi({ 'GET /settings': settings, 'POST /transactions': (c) => ({ transaction: tx({ id: 78, amount: (c.body as { amount: string }).amount }), action_id: 'act-new' }) })
      const onSaved = vi.fn()
      renderPage(<ManualTxForm categories={categories} accounts={accounts} onSaved={onSaved} />)
      const user = userEvent.setup()
      await user.type(await screen.findByLabelText('Сумма'), '21к')
      await user.click(screen.getByRole('radio', { name: /Еда/ }))
      await user.type(screen.getByLabelText('Сумма'), '{Enter}')
      await waitFor(() => expect(onSaved).toHaveBeenCalled())
      expect(api.find('POST', '/transactions')[0].headers['Idempotency-Key']).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    } finally {
      vi.stubGlobal('crypto', orig)
    }
  })

  it('a double Enter sends one POST while the first is still running', async () => {
    let release: () => void = () => {}
    const api = mockApi({
      'GET /settings': settings,
      'POST /transactions': (c) => new Promise((resolve) => { release = () => resolve({ transaction: tx({ id: 79, amount: (c.body as { amount: string }).amount }), action_id: 'act-new' }) }),
    })
    const onSaved = vi.fn()
    renderPage(<ManualTxForm categories={categories} accounts={accounts} onSaved={onSaved} />)
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Сумма'), '21к')
    await user.click(screen.getByRole('radio', { name: /Еда/ }))
    await user.type(screen.getByLabelText('Сумма'), '{Enter}{Enter}')
    await waitFor(() => expect(api.find('POST', '/transactions')).toHaveLength(1))
    release()
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    expect(api.find('POST', '/transactions')).toHaveLength(1)
  })

  it('applies «85 → 85 000 ₫», posts with Idempotency-Key and reuses it on retry', async () => {
    let attempt = 0
    const api = mockApi({
      'GET /settings': settings,
      'POST /transactions': (c) => {
        attempt++
        if (attempt === 1) throw new TypeError('Failed to fetch')
        return { transaction: tx({ id: 77, amount: (c.body as { amount: string }).amount }), action_id: 'act-new' }
      },
    })
    const onSaved = vi.fn()
    renderPage(<ManualTxForm categories={categories} accounts={accounts} onSaved={onSaved} />)
    const user = userEvent.setup()
    await screen.findByRole('form', { name: 'Новая операция' })
    await user.type(screen.getByLabelText('Сумма'), '85')
    expect(screen.getByText(/= 85 000 ₫/)).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: /Еда/ }))
    await user.click(screen.getByRole('button', { name: /Записать/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Нет ответа сервера')
    await user.click(screen.getByRole('button', { name: /Записать/ }))
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    const posts = api.find('POST', '/transactions')
    expect(posts).toHaveLength(2)
    expect(posts[0].body).toMatchObject({ kind: 'expense', amount: '85000', currency: 'VND', category_id: 1, account_id: null, account_none: false, occurred_on: '2026-10-27' })
    expect(posts[0].headers['Idempotency-Key']).toBe(posts[1].headers['Idempotency-Key'])
  })
})

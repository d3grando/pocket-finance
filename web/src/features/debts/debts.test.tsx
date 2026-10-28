import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpError, mockApi, renderPage, setMobile } from '@/features/operations/test/utils'
import { accounts, balances, page, sampleTxs, settings } from '@/features/operations/test/fixtures'
import { AccountsView } from '@/features/accounts/AccountsView'
import { SettingsView } from '@/features/settings/SettingsView'
import type { DebtsOut } from '@/lib/api/types'
import { DebtsHomeCard } from './DebtsHomeCard'
import { bal, emptyDebts, fullDebts, kolya, kolyaDetail, onlyOwedToMe, vasya } from './test/fixtures'

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-27T07:00:00Z'))
  setMobile(false)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const base = {
  'GET /settings': { ...settings, debt_reminders_enabled: false, debt_reminder_days: 30 },
  'GET /accounts': accounts,
  'GET /accounts/balances': balances,
  'GET /transactions': (c: { query: URLSearchParams }) => page(c.query.get('status') === 'needs_account' ? [] : sampleTxs.slice(0, 2)),
}

const withDebts = (d: DebtsOut, extra: Record<string, unknown> = {}) => ({ ...base, 'GET /debts': d, ...extra })

describe('Home «Долги»', () => {
  it('no open debts → no block at all', async () => {
    const api = mockApi(withDebts(emptyDebts))
    const { container } = renderPage(<DebtsHomeCard />)
    await waitFor(() => expect(api.find('GET', '/debts')).toHaveLength(1))
    expect(container.querySelector('[data-testid="home-debts"]')).toBeNull()
  })

  it('a server without debts (404 / 501) → no block, no error', async () => {
    mockApi({ ...base, 'GET /debts': () => { throw new HttpError(501, 'not_implemented') } })
    const { container } = renderPage(<DebtsHomeCard />)
    await new Promise((r) => setTimeout(r, 30))
    expect(container.textContent).toBe('')
  })

  it('one side: only «Мне должны» with the total ≈ ₫ and ≈ $', async () => {
    mockApi(withDebts(onlyOwedToMe))
    renderPage(<DebtsHomeCard />)
    const side = await screen.findByTestId('home-debts-owed_to_me')
    expect(within(side).getByText('Мне должны')).toBeInTheDocument()
    expect(within(side).getByText('≈ 300 000 ₫')).toBeInTheDocument()
    expect(within(side).getByText('≈ $11.48')).toBeInTheDocument()
    expect(within(side).getByRole('button', { name: 'Открыть долг: Коля' })).toHaveTextContent('300 000 ₫')
    expect(within(side).getByRole('button', { name: 'Открыть долг: Коля' })).toHaveTextContent('30 дн.')
    expect(screen.queryByTestId('home-debts-i_owe')).toBeNull()
  })

  it('both sides, multi-currency, up to 3 people, click opens the card of the person', async () => {
    const many = { ...fullDebts, owed_to_me: [...fullDebts.owed_to_me, { ...vasya, id: 60, name: 'Дима' }] }
    mockApi(withDebts(many))
    const { router } = renderPage(<DebtsHomeCard />)
    const owed = await screen.findByTestId('home-debts-owed_to_me')
    expect(within(owed).getAllByTestId('debt-person')).toHaveLength(3)
    expect(within(owed).getByText('и ещё 1 человек')).toBeInTheDocument()
    expect(within(owed).getByRole('button', { name: 'Открыть долг: Вася' })).toHaveTextContent('50 USDT')
    expect(within(owed).getByRole('button', { name: 'Открыть долг: Вася' })).toHaveTextContent('≈ $50.00')
    const iowe = screen.getByTestId('home-debts-i_owe')
    expect(within(iowe).getByText('≈ 1 523 000 ₫')).toBeInTheDocument()
    // a person in both lists shows only that side's currency
    expect(within(iowe).getByRole('button', { name: 'Открыть долг: Миша' })).toHaveTextContent('20 USDT')
    expect(within(iowe).getByRole('button', { name: 'Открыть долг: Миша' })).not.toHaveTextContent('₫')
    await userEvent.setup().click(within(iowe).getByRole('button', { name: 'Открыть долг: Петя' }))
    expect(router.state.location.pathname + router.state.location.search).toBe('/accounts?debt=7')
  })
})

describe('«Счета» → «Долги»', () => {
  it('two lists with balances per currency, ≈ $, age; not in the known total', async () => {
    mockApi(withDebts(fullDebts))
    renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
    const section = await screen.findByRole('region', { name: 'Долги' })
    const owed = await within(section).findByTestId('debts-owed_to_me')
    expect(within(owed).getByRole('button', { name: 'Открыть долг: Коля' })).toHaveTextContent(/30 дн\..*300 000 ₫.*≈ \$11\.48/s)
    expect(within(owed).getByRole('button', { name: 'Открыть долг: Вася' })).toHaveTextContent('@vasya')
    expect(within(section).getByTestId('debts-i_owe')).toHaveTextContent('1 000 000 ₫')
    // the known total of accounts does not include debts
    expect(screen.getByText(/≈\s\$1\s695\.20/)).toBeInTheDocument()
  })

  it('empty: a hint what to write', async () => {
    mockApi(withDebts(emptyDebts))
    renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
    expect(await screen.findByText('Долгов нет')).toBeInTheDocument()
    expect(screen.getByText(/дал в долг Коле 500к/)).toBeInTheDocument()
  })

  it('card of a person: balances, history with balance after, «весь долг» repay with an Idempotency-Key', async () => {
    const api = mockApi(withDebts(fullDebts, {
      'GET /debts/:id': kolyaDetail,
      'POST /debts/operations': { transaction: kolyaDetail.history[0].transaction, action_id: 'act-9', debt: { ...kolya, balances: [] }, balance_after: '0', excess: null, needs_account: false },
    }))
    const user = userEvent.setup()
    const { router } = renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
    await user.click(await screen.findByRole('button', { name: 'Открыть долг: Коля' }))
    expect(router.state.location.search).toBe('?debt=5')
    const card = await screen.findByTestId('debt-details')
    expect(await within(card).findByText('долг тебе 300 000 ₫')).toBeInTheDocument()
    const hist = within(card).getByTestId('debt-history')
    expect(within(hist).getByText('Возврат')).toBeInTheDocument()
    expect(within(hist).getByText('−200 000 ₫')).toBeInTheDocument()
    expect(within(hist).getByText('остаток 300 000 ₫')).toBeInTheDocument()
    expect(within(hist).getByText('+500 000 ₫')).toBeInTheDocument()
    await user.click(within(card).getByRole('button', { name: 'Записать возврат' }))
    const form = within(card).getByRole('form', { name: 'Возврат долга' })
    expect(within(form).getByRole('button', { name: /Весь долг · 300\s000\s₫/ })).toHaveAttribute('aria-pressed', 'true')
    await user.click(within(form).getByRole('button', { name: 'Записать возврат' }))
    await waitFor(() => expect(api.find('POST', '/debts/operations')).toHaveLength(1))
    const call = api.find('POST', '/debts/operations')[0]
    expect(call.body).toMatchObject({ op: 'repay_in', debt_id: 5, amount: null, currency: 'VND', account_id: null, account_none: false, occurred_on: '2026-10-27' })
    expect(call.headers['Idempotency-Key']).toBeTruthy()
  })

  it('partial repay of my debt in USDT → repay_out with the parsed amount', async () => {
    const detail = { ...kolyaDetail, debt: { ...kolya, balances: [{ ...kolya.balances[0] }, { currency: 'USDT', amount: '-20', direction: 'i_owe' as const, approx_usd: '-20', approx_vnd: null, open_since: '2026-10-26' }] } }
    const api = mockApi(withDebts(fullDebts, { 'GET /debts/:id': detail, 'POST /debts/operations': { transaction: kolyaDetail.history[0].transaction, action_id: 'a', debt: kolya, balance_after: '-5', excess: null, needs_account: false } }))
    const user = userEvent.setup()
    renderPage(<AccountsView />, { route: '/accounts?debt=5', path: '/accounts' })
    const card = await screen.findByTestId('debt-details')
    const usdt = await within(card).findByText('ты должен 20 USDT')
    await user.click(within(usdt.closest('li')!).getByRole('button', { name: 'Вернуть долг' }))
    const form = within(card).getByRole('form', { name: 'Возврат долга' })
    await user.click(within(form).getByRole('button', { name: 'Часть' }))
    await user.type(within(form).getByLabelText('Сумма'), '15')
    await user.selectOptions(within(form).getByLabelText('С какого счёта'), '3')
    await user.click(within(form).getByRole('button', { name: 'Я вернул' }))
    await waitFor(() => expect(api.find('POST', '/debts/operations')).toHaveLength(1))
    expect(api.find('POST', '/debts/operations')[0].body).toMatchObject({ op: 'repay_out', debt_id: 5, amount: '15', currency: 'USDT', account_id: 3 })
  })

  it('«Простить остаток» asks first, then forgives the whole balance', async () => {
    const api = mockApi(withDebts(fullDebts, { 'GET /debts/:id': kolyaDetail, 'POST /debts/operations': { transaction: { ...kolyaDetail.history[0].transaction, kind: 'expense' }, action_id: 'a', debt: kolya, balance_after: '0', excess: null, needs_account: false } }))
    const user = userEvent.setup()
    renderPage(<AccountsView />, { route: '/accounts?debt=5', path: '/accounts' })
    const card = await screen.findByTestId('debt-details')
    await user.click(await within(card).findByRole('button', { name: 'Простить остаток' }))
    const dialog = await screen.findByRole('dialog', { name: 'Простить долг: Коля?' })
    expect(dialog).toHaveTextContent('«Переводы людям»')
    expect(api.find('POST', '/debts/operations')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: 'Простить' }))
    await waitFor(() => expect(api.find('POST', '/debts/operations')).toHaveLength(1))
    expect(api.find('POST', '/debts/operations')[0].body).toEqual({ op: 'forgive', debt_id: 5, currency: 'VND', amount: null })
  })

  it('lists use explicit minmax(0,1fr) columns: a long name / many currencies cannot widen the page (390 px)', async () => {
    const long = { ...kolya, id: 70, name: 'Мария-Антуанетта Константиновна', balances: [bal('VND', '100000', '3.85'), bal('USDT', '125', '125'), bal('TON', '3.5', '18.9')] }
    mockApi(withDebts({ ...fullDebts, owed_to_me: [long, ...fullDebts.owed_to_me] }))
    renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
    const section = await screen.findByRole('region', { name: 'Долги' })
    const owed = await within(section).findByTestId('debts-owed_to_me')
    // jsdom has no layout: guard the grid tracks themselves (an implicit `auto` track = min-content of nowrap text)
    const grids = [owed.parentElement!, owed.parentElement!.parentElement!, owed.querySelector('ul')!]
    for (const g of grids) {
      expect(g.className).toMatch(/\bgrid\b/)
      expect(g.className).toMatch(/\bgrid-cols-1\b/)
    }
    const row = within(owed).getByRole('button', { name: 'Открыть долг: Мария-Антуанетта Константиновна' })
    expect(row).toHaveClass('min-w-0', 'w-full')
    expect(row).toHaveTextContent('100 000 ₫ · 125 USDT · 3.5 TON')
  })

  it('«Отменить» in the toast after «Простить остаток» undoes it — the card stays open', async () => {
    const api = mockApi(withDebts(fullDebts, {
      'GET /debts/:id': kolyaDetail,
      'POST /debts/operations': { transaction: { ...kolyaDetail.history[0].transaction, kind: 'expense', amount: '300000' }, action_id: 'act-f', debt: { ...kolya, balances: [] }, balance_after: '0', excess: null, needs_account: false },
      'POST /actions/:id/undo': { status: 'undone', undone_action_id: 'act-f', undo_action_id: 'u-1', summary: 'Отменено: Списан долг: Коля −300 000 ₫', changed: ['transactions', 'debts'] },
    }))
    // body has `pointer-events: none` while the modal sheet is open (jsdom does not load globals.css that
    // re-enables it for toasts — the real-browser check is e2e/debts*.spec.ts)
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage(<AccountsView />, { route: '/accounts?debt=5', path: '/accounts' })
    const card = await screen.findByTestId('debt-details')
    await user.click(await within(card).findByRole('button', { name: 'Простить остаток' }))
    await user.click(within(await screen.findByRole('dialog', { name: 'Простить долг: Коля?' })).getByRole('button', { name: 'Простить' }))
    const toast = (await screen.findByText(/Списан долг: Коля · 300\s000\s₫/)).closest('[data-sonner-toast]') as HTMLElement
    const undoBtn = within(toast).getByRole('button', { name: 'Отменить' })
    // a pointer-down on the toast is not «outside» for the sheet
    fireEvent.pointerDown(undoBtn)
    await user.click(undoBtn)
    await waitFor(() => expect(api.find('POST', '/actions/act-f/undo')).toHaveLength(1))
    expect(screen.getByTestId('debt-details')).toBeInTheDocument()
  })

  it('«Отменить последнюю»: the operation was edited since → undo the newest edit, not the 409 creation', async () => {
    const api = mockApi(withDebts(fullDebts, {
      'GET /debts/:id': kolyaDetail,
      'POST /actions/:id/undo': (c: { path: string }) => {
        if (c.path === '/actions/act-902/undo') throw new HttpError(409, 'undo_conflict', 'Запись уже изменили после этого действия — отменить нельзя')
        return { status: 'undone', undone_action_id: 'edit-2', undo_action_id: 'u-9', summary: 'Отменено изменение: 260 000 ₫ → 250 000 ₫', changed: ['transactions'] }
      },
      'GET /transactions/:id/history': [
        { id: 1, entity_type: 'transaction', entity_id: '902', operation: 'create', before: null, after: {}, action_id: 'act-902', source: 'web', created_at: '2026-10-20T10:00:00+07:00' },
        { id: 2, entity_type: 'transaction', entity_id: '902', operation: 'update', before: {}, after: {}, action_id: 'edit-1', source: 'web', created_at: '2026-10-21T10:00:00+07:00' },
        { id: 3, entity_type: 'transaction', entity_id: '902', operation: 'update', before: {}, after: {}, action_id: 'edit-2', source: 'desktop', created_at: '2026-10-22T10:00:00+07:00' },
      ],
      'GET /actions': [
        { id: 'edit-2', kind: 'update_transaction', summary: '', source: 'desktop', input_text: null, entity_refs: [{ type: 'transaction', id: 902 }], is_undoable: true, undone_at: null, created_at: '2026-10-22T10:00:00+07:00' },
        { id: 'edit-1', kind: 'update_transaction', summary: '', source: 'web', input_text: null, entity_refs: [{ type: 'transaction', id: 902 }], is_undoable: true, undone_at: null, created_at: '2026-10-21T10:00:00+07:00' },
      ],
    }))
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage(<AccountsView />, { route: '/accounts?debt=5', path: '/accounts' })
    const card = await screen.findByTestId('debt-details')
    await user.click(await within(card).findByRole('button', { name: 'Отменить последнюю операцию' }))
    await waitFor(() => expect(api.find('POST', '/actions/edit-2/undo')).toHaveLength(1))
    expect(api.find('POST', /\/actions\/.+\/undo/).map((c) => c.path)).toEqual(['/actions/act-902/undo', '/actions/edit-2/undo'])
    expect(api.find('GET', '/transactions/902/history')).toHaveLength(1)
    expect(await screen.findByText('Отменено изменение: 260 000 ₫ → 250 000 ₫')).toBeInTheDocument()
  })

  it('«Отменить последнюю»: a server with last_action_id → that action is undone right away (no history lookups)', async () => {
    const api = mockApi(withDebts(fullDebts, {
      'GET /debts/:id': { ...kolyaDetail, last_action_id: 'edit-2' },
      'POST /actions/:id/undo': { status: 'undone', undone_action_id: 'edit-2', undo_action_id: 'u-9', summary: 'Отменено изменение: 260 000 ₫ → 250 000 ₫', changed: ['transactions'] },
    }))
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    renderPage(<AccountsView />, { route: '/accounts?debt=5', path: '/accounts' })
    const card = await screen.findByTestId('debt-details')
    await user.click(await within(card).findByRole('button', { name: 'Отменить последнюю операцию' }))
    await waitFor(() => expect(api.find('POST', '/actions/edit-2/undo')).toHaveLength(1))
    expect(api.find('POST', /\/actions\/.+\/undo/).map((c) => c.path)).toEqual(['/actions/edit-2/undo'])
    expect(api.find('GET', /\/transactions\/\d+\/history/)).toHaveLength(0)
  })

  it('«Разобрать» with a debt operation does not call it an expense', async () => {
    const waiting = { ...kolyaDetail.history[0].transaction, id: 77, status: 'needs_account' as const, counter_account: null, currency: 'USDT', amount: '20' }
    mockApi({ ...withDebts(fullDebts), 'GET /transactions': (c: { query: URLSearchParams }) => page(c.query.get('status') === 'needs_account' ? [waiting] : []) })
    renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
    const hint = await screen.findByTestId('inbox-hint')
    expect(hint).toHaveTextContent('Ждут выбора счёта — в остатках пока не учтены.')
    expect(hint).not.toHaveTextContent('в расходах')
  })

  it('archive only at zero; rename sends the version', async () => {
    const api = mockApi(withDebts(fullDebts, { 'GET /debts/:id': kolyaDetail, 'PATCH /debts/:id': { ...kolya, name: 'Николай', version: 3 } }))
    const user = userEvent.setup()
    renderPage(<AccountsView />, { route: '/accounts?debt=5', path: '/accounts' })
    const card = await screen.findByTestId('debt-details')
    expect(await within(card).findByRole('button', { name: 'Архивировать' })).toBeDisabled()
    await user.click(within(card).getByRole('button', { name: 'Переименовать' }))
    const form = within(card).getByRole('form', { name: 'Переименовать' })
    await user.clear(within(form).getByLabelText('Имя'))
    await user.type(within(form).getByLabelText('Имя'), 'Николай')
    await user.click(within(form).getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(api.find('PATCH', '/debts/5')).toHaveLength(1))
    expect(api.find('PATCH', '/debts/5')[0].body).toEqual({ version: 2, name: 'Николай' })
  })

  it('«Дать в долг»: a known person by autocomplete → debt_id; a new one → person_name; «500к» → 500000', async () => {
    const result = { transaction: kolyaDetail.history[1].transaction, action_id: 'a', debt: kolya, balance_after: '800000', excess: null, needs_account: false }
    const api = mockApi(withDebts(fullDebts, { 'POST /debts/operations': result }))
    const user = userEvent.setup()
    renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
    const section = await screen.findByRole('region', { name: 'Долги' })
    await user.click(within(section).getByRole('button', { name: 'Дать в долг' }))
    let form = await screen.findByRole('form', { name: 'Дать в долг' })
    await user.type(within(form).getByLabelText('Кто'), 'ко')
    await user.click(await within(form).findByRole('button', { name: 'Коля' }))
    await user.type(within(form).getByLabelText('Сумма'), '500к')
    expect(within(form).getByText('= 500 000 ₫')).toBeInTheDocument()
    await user.click(within(form).getByRole('button', { name: 'Дать в долг' }))
    await waitFor(() => expect(api.find('POST', '/debts/operations')).toHaveLength(1))
    expect(api.find('POST', '/debts/operations')[0].body).toMatchObject({ op: 'lend', debt_id: 5, amount: '500000', currency: 'VND' })
    expect(api.find('POST', '/debts/operations')[0].body).not.toHaveProperty('person_name')

    await user.click(within(section).getByRole('button', { name: 'Занять' }))
    form = await screen.findByRole('form', { name: 'Занять' })
    await user.type(within(form).getByLabelText('Кто'), 'Петров')
    await user.type(within(form).getByLabelText('Сумма'), '50 usdt')
    await user.click(within(form).getByRole('button', { name: 'Занять' }))
    await waitFor(() => expect(api.find('POST', '/debts/operations')).toHaveLength(2))
    expect(api.find('POST', '/debts/operations')[1].body).toMatchObject({ op: 'borrow', person_name: 'Петров', amount: '50', currency: 'USDT' })
  })

  it('a form error keeps the form and says why', async () => {
    mockApi(withDebts(fullDebts, { 'POST /debts/operations': () => { throw new HttpError(422, 'validation_error', 'Крипта со счёта в ₫ — нельзя') } }))
    const user = userEvent.setup()
    renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
    const section = await screen.findByRole('region', { name: 'Долги' })
    await user.click(within(section).getByRole('button', { name: 'Дать в долг' }))
    const form = await screen.findByRole('form', { name: 'Дать в долг' })
    await user.click(within(form).getByRole('button', { name: 'Дать в долг' }))
    expect(within(form).getByRole('alert')).toHaveTextContent('Укажите, кто')
    await user.type(within(form).getByLabelText('Кто'), 'Вова')
    await user.type(within(form).getByLabelText('Сумма'), '100')
    await user.click(within(form).getByRole('button', { name: 'Дать в долг' }))
    expect(await within(form).findByText('Крипта со счёта в ₫ — нельзя')).toBeInTheDocument()
  })
})

describe('Settings → напоминать о долгах', () => {
  const route = { route: '/settings/telegram', path: '/settings/*' }
  const server = { ai_base_url: '', ai_model: '', ai_configured: true, telegram_bot_configured: true, telegram_bot_username: null, version: '1.0.0', public_url: '' }

  it('off by default; switching on and «старше N дней» are saved with the version', async () => {
    let s = { ...settings, debt_reminders_enabled: false, debt_reminder_days: 30 }
    const api = mockApi({
      'GET /settings': () => s, 'GET /settings/server': server, 'GET /recurring': [],
      'PATCH /settings': (c) => { s = { ...s, ...(c.body as object), version: s.version + 1 }; return s },
    })
    const user = userEvent.setup()
    renderPage(<SettingsView />, route)
    const sw = await screen.findByRole('switch', { name: 'Напоминать о долгах' })
    expect(sw).toHaveAttribute('aria-checked', 'false')
    expect(screen.queryByLabelText('старше')).toBeNull()
    await user.click(sw)
    await waitFor(() => expect(api.find('PATCH', '/settings')[0]?.body).toEqual({ debt_reminders_enabled: true, version: 3 }))
    const days = await screen.findByLabelText('старше')
    await user.clear(days)
    await user.type(days, '400')
    expect(screen.getByText('От 1 до 365 дней')).toBeInTheDocument()
    const form = screen.getByRole('form', { name: 'Старше N дней' })
    expect(within(form).getByRole('button', { name: 'Сохранить' })).toBeDisabled()
    await user.clear(days)
    await user.type(days, '45')
    await user.click(within(form).getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(api.find('PATCH', '/settings')[1]?.body).toEqual({ debt_reminder_days: 45, version: 4 }))
  })

  it('a server without debt settings → no row', async () => {
    mockApi({ 'GET /settings': settings, 'GET /settings/server': server, 'GET /recurring': [] })
    renderPage(<SettingsView />, route)
    expect(await screen.findByRole('switch', { name: 'Ежедневный отчёт' })).toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: 'Напоминать о долгах' })).toBeNull()
  })
})

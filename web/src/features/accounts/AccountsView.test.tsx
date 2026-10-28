import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { mockApi, renderPage } from '@/features/operations/test/utils'
import { accounts, balances, page, sampleTxs, settings } from '@/features/operations/test/fixtures'
import { AccountsView } from './AccountsView'
import { rateLabel } from './forms'
import { totalsText, unknownAccountsCount } from './AccountCard'
import type { AccountOut } from '@/lib/api'

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-27T07:00:00Z'))
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const routes = {
  'GET /settings': settings,
  'GET /accounts': accounts,
  'GET /accounts/balances': balances,
  'GET /transactions': (c: { query: URLSearchParams }) => page(c.query.get('status') === 'needs_account' ? [sampleTxs[5]] : sampleTxs.slice(0, 2)),
}

describe('AccountsView', () => {
  it('never shows an unknown balance as zero; wallets list coins with ≈ $', async () => {
    mockApi(routes)
    renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
    const cash = (await screen.findByRole('button', { name: 'Открыть счёт Наличные' })).closest('article')!
    expect(within(cash).getByText('Остаток не задан')).toBeInTheDocument()
    expect(within(cash).queryByText(/0\s₫/)).toBeNull()
    expect(within(cash).getByRole('button', { name: 'Задать остаток' })).toBeInTheDocument()
    const kb = screen.getByRole('button', { name: 'Открыть счёт Кошелёк' }).closest('article')!
    expect(within(kb).getByText(/312\.40\sUSDT/)).toBeInTheDocument()
    expect(within(kb).getByText(/3\sTON/)).toBeInTheDocument()
    expect(within(kb).getByText(/≈\s\$328\.60/)).toBeInTheDocument()
    expect(within(kb).getByText(/монета добавится при первом упоминании/)).toBeInTheDocument()
    expect(screen.getByText(/≈\s\$1\s695\.20/)).toBeInTheDocument()
    expect(screen.getByText(/1 счёт без остатка не учтён$/)).toBeInTheDocument()
    expect(screen.getAllByText(/по умолчанию для ₫/).length).toBe(1)
  })

  it('«Разобрать»: chooses the account for a needs_account operation', async () => {
    const api = mockApi({ ...routes, 'POST /transactions/:id/account': { transaction: { ...sampleTxs[5], status: 'posted' }, action_id: 'a1' } })
    renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
    const group = await screen.findByRole('group', { name: 'С какого счёта?' })
    // fitting accounts first (wallets for USDT), then every other account — like the assistant card
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['Кошелёк', 'Карта', 'Наличные', 'Карта RUB', 'Без счёта'])
    await userEvent.setup().click(within(group).getByRole('button', { name: 'Кошелёк' }))
    await waitFor(() => expect(api.find('POST', '/transactions/6/account')).toHaveLength(1))
    expect(api.find('POST', '/transactions/6/account')[0].body).toEqual({ account_id: 3, version: 1 })
  })

  it('transfer form posts once per body with an Idempotency-Key', async () => {
    const api = mockApi({ ...routes, 'POST /transfers': { transaction: sampleTxs[4], action_id: 'a2' } })
    renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Перевод' }))
    const form = await screen.findByRole('form', { name: 'Перевод между счетами' })
    await user.selectOptions(within(form).getByLabelText('Откуда'), '3')
    await user.selectOptions(within(form).getByLabelText('Куда'), '1')
    await user.type(within(form).getByLabelText('Сумма'), '3')
    await user.selectOptions(within(form).getByLabelText('Валюта'), 'TON')
    await user.click(within(form).getByRole('button', { name: 'Перевести' }))
    await waitFor(() => expect(api.find('POST', '/transfers')).toHaveLength(1))
    const call = api.find('POST', '/transfers')[0]
    expect(call.body).toMatchObject({ from_account_id: 3, to_account_id: 1, amount: '3', currency: 'TON', occurred_on: '2026-10-27' })
    expect(call.headers['Idempotency-Key']).toBeTruthy()
  })

  it('account panel: sets the opening balance', async () => {
    const api = mockApi({ ...routes, 'PUT /accounts/:id/opening': accounts[1] })
    renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
    const user = userEvent.setup()
    const cash = (await screen.findByRole('button', { name: 'Открыть счёт Наличные' })).closest('article')!
    await user.click(within(cash).getByRole('button', { name: 'Задать остаток' }))
    const form = await screen.findByRole('form', { name: 'Начальный остаток VND' })
    await user.type(within(form).getByLabelText('Начальный остаток, VND'), '1.2м')
    await user.click(within(form).getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(api.find('PUT', '/accounts/2/opening')).toHaveLength(1))
    expect(api.find('PUT', '/accounts/2/opening')[0].body).toEqual({ asset: 'VND', amount: '1200000' })
  })

  it('counts accounts (not asset rows) without a balance, with the right verb form', () => {
    const wallet = (id: number, name: string): AccountOut => ({ ...accounts[2], id, name, assets: [] })
    const list = [...accounts, wallet(5, 'Копилка'), wallet(6, 'Биржа')]
    expect(unknownAccountsCount(list)).toBe(3)
    expect(totalsText({ total_usd: '10', total_vnd: null, accounts: list }).note).toBe('3 счёта без остатка не учтены')
    expect(totalsText({ total_usd: '10', total_vnd: null, accounts: [accounts[1]] }).note).toBe('1 счёт без остатка не учтён')
    expect(totalsText({ total_usd: '10', total_vnd: null, accounts: [accounts[0]] }).note).toBeNull()
  })

  it('shows «нет связи», not «Счетов нет», when offline with nothing loaded', async () => {
    const { onlineManager } = await import('@tanstack/react-query')
    mockApi(routes)
    onlineManager.setOnline(false)
    try {
      renderPage(<AccountsView />, { route: '/accounts', path: '/accounts' })
      expect((await screen.findAllByText('Нет связи — данные не загружены')).length).toBeGreaterThan(0)
      expect(screen.queryByText('Счетов нет')).toBeNull()
      expect(screen.queryByText('Всё разобрано')).toBeNull()
    } finally {
      onlineManager.setOnline(true)
    }
  })

  it('exchange rate label is computed from both amounts', () => {
    expect(rateLabel('100', 'USDT', '99.8', 'USDT')).toBe('1 USDT = 0.998 USDT')
    expect(rateLabel('100', 'USD', '2614000', 'VND')).toBe('1 USD = 26 140 VND')
  })
})

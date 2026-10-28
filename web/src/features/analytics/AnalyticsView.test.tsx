import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { mockApi, renderPage, type Call } from '@/features/operations/test/utils'
import { settings } from '@/features/operations/test/fixtures'
import { AnalyticsView } from './AnalyticsView'

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-27T07:00:00Z'))
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const range = (c: Call) => ({ date_from: c.query.get('date_from')!, date_to: c.query.get('date_to')!, label: '' })
const cs = (id: number, name: string, amount: string, share: string) => ({ category_id: id, name, icon: null, color: null, is_business: false, is_new: false, amount, share, count: 3 })

export const statsRoutes = {
  'GET /settings': settings,
  'GET /stats/compare': (c: Call) => ({ currency: c.query.get('currency'), current: { range: range(c), expense: '476.3', income: '700.1', net: '223.8' }, previous: { range: range(c), expense: '425.1', income: '730', net: '304.9' }, expense_change_ratio: '0.12', income_change_ratio: '-0.04', by_category: [] }),
  'GET /stats/daily-average': (c: Call) => ({ range: range(c), currency: c.query.get('currency'), days: 27, total: '476.3', average: '17.64' }),
  'GET /stats/business': (c: Call) => ({ range: range(c), currency: c.query.get('currency'), business_income: '1100', business_expense: '320', business_net: '780', personal_income: '47.6', personal_expense: '156.3', expense_by_category: [], income_by_category: [] }),
  'GET /stats/series': (c: Call) => ({ range: range(c), granularity: c.query.get('granularity'), currency: c.query.get('currency'), points: [{ date: '2026-10-01', label: '1 окт', expense: '12.4', income: '0', net: '-12.4' }, { date: '2026-10-02', label: '2 окт', expense: '30', income: '100', net: '70' }] }),
  'GET /stats/by-category': (c: Call) => ({ range: range(c), kind: c.query.get('kind'), currency: c.query.get('currency'), total: '476.3', items: c.query.get('date_from') === '2026-10-01' ? [cs(1, 'Еда', '180.2', '0.378'), cs(4, 'Такси/транспорт', '68', '0.143')] : [cs(1, 'Еда', '140', '0.4'), cs(4, 'Такси/транспорт', '81', '0.2')] }),
  'GET /stats/top': (c: Call) => ({ range: range(c), currency: c.query.get('currency'), items: [{ transaction_id: 99, occurred_on: '2026-10-12', title: 'Серверы/инфра · vast.ai', category: 'Серверы/инфра', amount: '815', currency: 'USD', original_amount: '21300000', original_currency: 'VND' }] }),
}

describe('AnalyticsView', () => {
  it('shows KPIs and charts from backend stats in $ by default', async () => {
    const api = mockApi(statsRoutes)
    renderPage(<AnalyticsView />, { route: '/analytics', path: '/analytics' })
    expect(await screen.findByText('−$476.30')).toBeInTheDocument()
    expect(screen.getByText('+$700.10')).toBeInTheDocument()
    expect(screen.getByText('+12% к сент.')).toHaveClass('text-expense')
    expect(screen.getByText('−4% к сент.')).toHaveClass('text-expense')
    expect(screen.getByText('−$17.64')).toBeInTheDocument()
    expect(screen.getAllByText('+$780.00').length).toBe(2)
    expect(screen.getByText('Октябрь 2026')).toBeInTheDocument()
    expect(await screen.findByText('Серверы/инфра · vast.ai')).toBeInTheDocument()
    const cmp = api.find('GET', '/stats/compare')[0].query
    // running month vs the same elapsed days of the previous one (backend previous_range, same as Home)
    expect(Object.fromEntries(cmp)).toMatchObject({ preset: 'custom', date_from: '2026-10-01', date_to: '2026-10-31', currency: 'USD', prev_date_from: '2026-09-01', prev_date_to: '2026-09-27' })
    expect(api.find('GET', '/stats/series').find((c) => c.query.get('granularity') === 'day')!.query.get('date_from')).toBe('2026-10-01')
    const monthly = api.find('GET', '/stats/series').find((c) => c.query.get('granularity') === 'month')!.query
    expect([monthly.get('date_from'), monthly.get('date_to')]).toEqual(['2025-11-01', '2026-10-31'])
  })

  it('₫/$ switch refetches in VND; period arrows move by month', async () => {
    const api = mockApi(statsRoutes)
    const { router } = renderPage(<AnalyticsView />, { route: '/analytics', path: '/analytics' })
    await screen.findByText('−$476.30')
    const user = userEvent.setup()
    await user.click(screen.getByRole('radio', { name: '₫' }))
    await waitFor(() => expect(api.find('GET', '/stats/compare').some((c) => c.query.get('currency') === 'VND')).toBe(true))
    expect(router.state.location.search).toContain('cur=VND')
    await user.click(screen.getByRole('button', { name: 'Предыдущий период' }))
    await waitFor(() => expect(screen.getByText('Сентябрь 2026')).toBeInTheDocument())
    expect(router.state.location.search).toContain('period=2026-09')
  })

  it('every chart has a table view; comparison pairs show Δ', async () => {
    mockApi(statsRoutes)
    renderPage(<AnalyticsView />, { route: '/analytics', path: '/analytics' })
    const card = (await screen.findByRole('heading', { name: 'Расходы по дням' })).closest('section')!
    await userEvent.setup().click(within(card).getByRole('button', { name: /Таблица/ }))
    expect(within(card).getByRole('table')).toBeInTheDocument()
    expect(within(card).getByText('$12.40')).toBeInTheDocument()
    const cmp = screen.getByRole('heading', { name: /Сравнение: 1–27 окт vs 1–27 сен/ }).closest('section')!
    await waitFor(() => expect(within(cmp).getByText('+29%')).toHaveClass('text-expense'))
    expect(within(cmp).getByText('−16%')).toHaveClass('text-income')
  })

  it('all_time asks the backend for preset=all_time and uses its range (no 2000-01-01, no 9797 days)', async () => {
    const api = mockApi({
      ...statsRoutes,
      'GET /stats/daily-average': (c: Call) => (c.query.get('preset') === 'all_time' ? { range: { date_from: '2026-08-15', date_to: '2026-10-27', label: 'За всё время' }, currency: 'USD', days: 74, total: '1116.7', average: '15.09' } : statsRoutes['GET /stats/daily-average'](c)),
    })
    const { router } = renderPage(<AnalyticsView />, { route: '/analytics?period=all_time', path: '/analytics' })
    expect(await screen.findByText('−$15.09')).toBeInTheDocument()
    expect(screen.getByText(/74 дн\./)).toBeInTheDocument()
    await waitFor(() => expect(api.find('GET', '/stats/series').some((c) => c.query.get('granularity') === 'day' && c.query.get('preset') === 'all_time')).toBe(true))
    for (const c of api.calls.filter((x) => x.path.startsWith('/stats/') && x.query.get('granularity') !== 'month')) {
      expect(c.query.get('date_from') ?? '').not.toBe('2000-01-01')
    }
    const cmp = api.find('GET', '/stats/compare')[0].query
    expect(cmp.get('preset')).toBe('all_time')
    expect(cmp.get('prev_date_from')).toBeNull()
    expect(screen.queryByText(/к сент/)).toBeNull()
    expect(screen.getAllByText('За всё время').length).toBeGreaterThan(0)
    expect(router.state.location.search).toBe('?period=all_time')
  })

  it('offline with nothing loaded: «Нет связи», not «Нет данных за период»', async () => {
    const { onlineManager } = await import('@tanstack/react-query')
    mockApi(statsRoutes)
    onlineManager.setOnline(false)
    try {
      renderPage(<AnalyticsView />, { route: '/analytics', path: '/analytics' })
      expect(await screen.findByText('Нет связи')).toBeInTheDocument()
      expect(screen.queryByText('Нет данных за период')).toBeNull()
      expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument()
    } finally {
      onlineManager.setOnline(true)
    }
  })

  it('opens operations with the filter when a category is clicked', async () => {
    mockApi(statsRoutes)
    const { router } = renderPage(<AnalyticsView />, { route: '/analytics', path: '/analytics' })
    const legend = await screen.findByRole('list', { name: 'Легенда' })
    await userEvent.setup().click(within(legend).getByRole('button', { name: /Еда/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/operations'))
    expect(router.state.location.search).toContain('cat=1')
    expect(router.state.location.search).toContain('from=2026-10-01')
  })
})

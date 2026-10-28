// Contract-shaped fixtures (docs/API.md) shared by vitest and Playwright smoke tests.
import type { AccountOut, BalancesOut, CategoryOut, SettingsOut, TransactionOut, TransactionPage } from '@/lib/api'

export const settings: SettingsOut = {
  timezone: 'Asia/Ho_Chi_Minh',
  default_currency: 'VND',
  analytics_currency: 'USD',
  summary_currency: 'VND',
  small_amount_thousands: true,
  theme: 'system',
  desktop_hotkey: 'Ctrl+Alt+Space',
  telegram_user_id: 123456789,
  report_monthly: true,
  report_daily: false,
  report_weekly: false,
  report_time: '10:00:00',
  onboarding_done: true,
  version: 3,
}

const cat = (id: number, name: string, kind: 'expense' | 'income', icon: string, is_business = false, extra: Partial<CategoryOut> = {}): CategoryOut => ({
  id, name, kind, is_business, icon, color: null, aliases: [], is_system: true, is_new: false, is_archived: false, sort_order: id, version: 1, tx_count: 10 - (id % 10), ...extra,
})

export const categories: CategoryOut[] = [
  cat(1, 'Еда', 'expense', 'UtensilsCrossed', false, { aliases: ['еда', 'обед'] }),
  cat(2, 'Продукты', 'expense', 'ShoppingBasket'),
  cat(3, 'Кофе', 'expense', 'Coffee'),
  cat(4, 'Такси/транспорт', 'expense', 'CarTaxiFront'),
  cat(5, 'Подписки', 'expense', 'RefreshCcw'),
  cat(14, 'Серверы/инфра', 'expense', 'Server', true),
  cat(15, 'Софт и AI', 'expense', 'Bot', true),
  cat(20, 'Клиенты', 'income', 'BriefcaseBusiness', true),
  cat(21, 'Зарплата', 'income', 'Banknote'),
  cat(30, 'Вейпы', 'expense', 'Tag', false, { is_system: false, is_new: true, tx_count: 2 }),
]

const asset = (a: string, balance: string | null, approx_usd: string | null = null, approx_vnd: string | null = null) => ({ asset: a, opening_balance: balance === null ? null : balance, opening_at: balance === null ? null : '2026-10-01T00:00:00+07:00', balance, approx_usd, approx_vnd })

export const accounts: AccountOut[] = [
  { id: 1, name: 'Карта', kind: 'bank', currency: 'VND', is_multi_asset: false, aliases: ['карта'], default_for_currencies: ['VND'], color: null, icon: 'Landmark', sort_order: 1, is_archived: false, version: 1, assets: [asset('VND', '34120000', '1305.3', '34120000')] },
  { id: 2, name: 'Наличные', kind: 'cash', currency: 'VND', is_multi_asset: false, aliases: ['налом'], default_for_currencies: [], color: null, icon: 'Wallet', sort_order: 2, is_archived: false, version: 1, assets: [asset('VND', null)] },
  { id: 3, name: 'Кошелёк', kind: 'crypto_wallet', currency: null, is_multi_asset: true, aliases: ['Кошелёк', 'кошелёк'], default_for_currencies: [], color: null, icon: 'Bot', sort_order: 3, is_archived: false, version: 2, assets: [asset('USDT', '312.4', '312.4', '8166136'), asset('TON', '3', '16.2', '423468')] },
  { id: 4, name: 'Карта RUB', kind: 'card', currency: 'RUB', is_multi_asset: false, aliases: [], default_for_currencies: ['RUB'], color: null, icon: 'CreditCard', sort_order: 4, is_archived: false, version: 1, assets: [asset('RUB', '4990', '61.3', '1602000')] },
]

export const balances: BalancesOut = { accounts, total_vnd: '45311604', total_usd: '1695.2', unknown_count: 1 }

let seq = 100
export function tx(p: Partial<TransactionOut> & { kind?: TransactionOut['kind'] }): TransactionOut {
  const id = p.id ?? ++seq
  const category = p.category === undefined ? { id: 1, name: 'Еда', kind: 'expense' as const, icon: 'UtensilsCrossed', color: null, is_business: false, is_new: false } : p.category
  return {
    id,
    kind: 'expense',
    status: 'posted',
    occurred_at: '2026-10-27T14:32:05+07:00',
    occurred_on: '2026-10-27',
    amount: '45000',
    currency: 'VND',
    account: { id: 1, name: 'Карта', icon: 'Landmark' },
    account_amount: '45000',
    account_asset: 'VND',
    account_rate: '1',
    is_estimated: false,
    counter_account: null,
    counter_amount: null,
    counter_asset: null,
    category,
    is_business: category?.is_business ?? false,
    merchant: null,
    counterparty: null,
    description: null,
    amount_usd: '1.72',
    amount_vnd: p.amount_vnd ?? p.amount ?? '45000',
    usd_rate: '0.0000382',
    rate_date: '2026-10-27',
    rate_source: 'vietcombank',
    refund_of_id: null,
    recurring_id: null,
    source: 'web',
    raw_text: null,
    created_action_id: null,
    version: 1,
    created_at: '2026-10-27T14:32:05+07:00',
    updated_at: '2026-10-27T14:32:05+07:00',
    title: category?.name ?? 'Операция',
    ...p,
  }
}

export const sampleTxs: TransactionOut[] = [
  tx({ id: 1, title: 'Кофе', description: 'латте', merchant: { id: 9, name: 'Highlands' }, category: { id: 3, name: 'Кофе', kind: 'expense', icon: 'Coffee', color: null, is_business: false, is_new: false } }),
  tx({ id: 2, title: 'Grab', amount: '120000', occurred_at: '2026-10-27T12:10:00+07:00', category: { id: 4, name: 'Такси/транспорт', kind: 'expense', icon: 'CarTaxiFront', color: null, is_business: false, is_new: false } }),
  tx({ id: 3, title: 'Серверы/инфра · vast.ai', kind: 'expense', amount: '180', currency: 'USD', amount_usd: '180', amount_vnd: '4705200', account_amount: '4705200', account_asset: 'VND', is_estimated: true, occurred_on: '2026-10-26', occurred_at: '2026-10-26T22:40:00+07:00', category: { id: 14, name: 'Серверы/инфра', kind: 'expense', icon: 'Server', color: null, is_business: true, is_new: false } }),
  tx({ id: 4, title: 'Клиенты · @client', kind: 'income', amount: '300', currency: 'USDT', amount_usd: '300', amount_vnd: '7842000', account: { id: 3, name: 'Кошелёк', icon: 'Bot' }, account_amount: '300', account_asset: 'USDT', occurred_on: '2026-10-26', occurred_at: '2026-10-26T09:40:00+07:00', category: { id: 20, name: 'Клиенты', kind: 'income', icon: 'BriefcaseBusiness', color: null, is_business: true, is_new: false } }),
  tx({ id: 5, title: 'Перевод', kind: 'transfer', amount: '3', currency: 'TON', amount_usd: '16.2', amount_vnd: '423468', account: { id: 3, name: 'Кошелёк', icon: 'Bot' }, counter_account: { id: 5, name: 'Копилка', icon: 'Bot' }, counter_amount: '3', counter_asset: 'TON', category: null, occurred_on: '2026-10-25', occurred_at: '2026-10-25T11:05:00+07:00' }),
  tx({ id: 6, title: 'Подписки', kind: 'expense', status: 'needs_account', amount: '15', currency: 'USDT', amount_usd: '15', amount_vnd: '392100', account: null, account_amount: null, account_asset: null, account_rate: null, occurred_on: '2026-10-25', occurred_at: '2026-10-25T10:00:00+07:00', raw_text: 'потратил 15 USDT на подписку', category: { id: 5, name: 'Подписки', kind: 'expense', icon: 'RefreshCcw', color: null, is_business: false, is_new: false } }),
]

export function page(items: TransactionOut[], total = items.length, offset = 0): TransactionPage {
  return { items, total, limit: 100, offset, totals: { expense_vnd: '5262300', income_vnd: '7842000', expense_usd: '201.3', income_usd: '300', count: total } }
}

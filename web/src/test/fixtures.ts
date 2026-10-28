// Test fixtures shaped exactly like API.md examples.
import type { AssistantResponse, Card, TransactionOut } from '@/lib/api/types'

export function tx(over: Partial<TransactionOut> = {}): TransactionOut {
  return {
    id: 123, kind: 'expense', status: 'posted', occurred_at: '2026-10-27T14:32:05+07:00', occurred_on: '2026-10-27',
    amount: '250000', currency: 'VND', account: { id: 1, name: 'Карта', icon: 'Landmark' },
    account_amount: '250000', account_asset: 'VND', account_rate: '1', is_estimated: false,
    counter_account: null, counter_amount: null, counter_asset: null,
    category: { id: 1, name: 'Еда', kind: 'expense', icon: 'UtensilsCrossed', color: null, is_business: false, is_new: false },
    is_business: false, merchant: null, counterparty: null, description: null,
    amount_usd: '9.56', amount_vnd: '250000', usd_rate: '0.0000382', rate_date: '2026-10-27', rate_source: 'vietcombank',
    refund_of_id: null, recurring_id: null, source: 'web', raw_text: 'еда 250к', created_action_id: 'a-1', version: 1,
    created_at: '2026-10-27T07:32:05Z', updated_at: '2026-10-27T07:32:05Z', title: 'Еда',
    ...over,
  }
}

export function createdCard(t: TransactionOut = tx(), actionId = 'a-1'): Card {
  return {
    kind: 'created', title: '250 000 ₫ · Еда', subtitle: 'Карта · сегодня, 14:32', action_id: actionId, transaction: t,
    category_is_new: false, approx: null, note: null,
    buttons: [
      { id: 'undo', label: 'Отменить', style: 'ghost', hotkey: 'Ctrl+Z', action: { kind: 'undo', action_id: actionId } },
      { id: 'edit', label: 'Изменить', style: 'ghost', hotkey: 'Ctrl+E', action: { kind: 'edit', transaction_id: t.id } },
    ],
  }
}

export function response(cards: Card[], over: Partial<AssistantResponse> = {}): AssistantResponse {
  return { request_id: 1, route: 'parser', cards, action_ids: ['a-1'], changed: ['transactions', 'stats'], ai_available: true, ...over }
}

export function needsAccountCard(): Card {
  const t = tx({ id: 125, status: 'needs_account', amount: '15', currency: 'USDT', account: null, title: 'Подписки' })
  return {
    kind: 'needs_account', title: '15 USDT · Подписки', subtitle: '≈ $15.00', question: 'С какого счёта?', action_id: 'a-2', transaction: t,
    options: [{ account_id: 3, label: 'Кошелёк', hint: 'есть USDT', hotkey: '1' }, { account_id: null, label: 'Без счёта', hint: null, hotkey: '0' }],
    buttons: [
      { id: 'acc-3', label: 'Кошелёк', style: 'outline', hotkey: '1', action: { kind: 'choose_account', transaction_id: 125, account_id: 3 } },
      { id: 'acc-4', label: 'Копилка', style: 'outline', hotkey: '2', action: { kind: 'choose_account', transaction_id: 125, account_id: 4 } },
      { id: 'acc-none', label: 'Без счёта', style: 'ghost', hotkey: '0', action: { kind: 'choose_account', transaction_id: 125, account_id: null } },
      { id: 'later', label: 'Позже', style: 'ghost', hotkey: null, action: { kind: 'later' } },
      { id: 'undo', label: 'Отменить', style: 'ghost', hotkey: null, action: { kind: 'undo', action_id: 'a-2' } },
    ],
  }
}

// Assistant cards for debts (DEBTS.md §8, §10): the additive `debt` / `debts` fields render a line /
// lists; cards from an old server (no such fields) render exactly as before.
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/tooltip'
import { connection } from '@/lib/connection'
import type { Card } from '@/lib/api/types'
import { createdCard, response } from '@/test/fixtures'
import { AiCommandBar } from '@/features/assistant/AiCommandBar'
import { AssistantProvider } from '@/features/assistant/AssistantProvider'
import { CardStack } from '@/features/assistant/CardStack'
import { TxRow } from '@/features/operations/components/TxRow'
import { debtTx, fullDebts } from './test/fixtures'

type Call = { url: string; method: string; body: unknown }
let calls: Call[] = []
let reply: Card[] = []
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    const c = { url: String(url), method: init.method ?? 'GET', body: init.body ? JSON.parse(String(init.body)) : undefined }
    calls.push(c)
    if (c.method === 'GET' && /\/(categories|accounts)(\?|$)/.test(c.url)) return json([])
    if (c.method === 'GET' && /\/settings(\?|$)/.test(c.url)) return json({ timezone: 'Asia/Ho_Chi_Minh', small_amount_thousands: true })
    if (c.url.endsWith('/assistant/messages')) return json(response(reply))
    return json({ error: { code: 'not_found', message: 'x', details: {}, request_id: null } }, 404)
  }))
  connection.markOk()
})
afterEach(() => vi.unstubAllGlobals())

function setup(onOpen = vi.fn(), compact = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <AssistantProvider onOpen={onOpen}>
          <AiCommandBar />
          <CardStack compact={compact} />
        </AssistantProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  )
  return { user: userEvent.setup(), input: screen.getByTestId('ai-input') as HTMLTextAreaElement, onOpen }
}

async function say(user: ReturnType<typeof userEvent.setup>, input: HTMLTextAreaElement, text: string) {
  await user.type(input, text)
  await user.keyboard('{Enter}')
  return screen.findByTestId('result-card')
}

const lendCard = (): Card => ({
  ...createdCard(debtTx({ id: 300, op: 'lend' }), 'a-300'),
  title: '500 000 ₫ · Дал в долг: Коля',
  subtitle: 'Карта · Коля: долг тебе 500 000 ₫',
  debt: { debt_id: 5, name: 'Коля', op: 'lend', currency: 'VND', balance_after: '500000', excess: null },
} as Card)

describe('assistant cards with debts', () => {
  it('created + debt: title/subtitle as the server sent them, the balance said once; the name in the title opens the card', async () => {
    reply = [lendCard()]
    const { user, input, onOpen } = setup()
    const card = await say(user, input, 'дал в долг Коле 500к')
    expect(within(card).getByTestId('card-title')).toHaveTextContent('500 000 ₫ · Дал в долг: Коля')
    expect(card).toHaveTextContent('Карта · Коля: долг тебе 500 000 ₫')
    // the subtitle already says it — no second «Коля: долг тебе 500 000 ₫» line
    expect(within(card).getAllByText(/Коля: долг тебе 500\s000\s₫/)).toHaveLength(1)
    expect(within(card).queryByTestId('card-debt')).toBeNull()
    expect(within(card).getByRole('button', { name: /Отменить/ })).toBeInTheDocument()
    await user.click(within(within(card).getByTestId('card-title')).getByRole('button', { name: 'Коля' }))
    expect(onOpen).toHaveBeenCalledWith('/accounts?debt=5')
  })

  it('a subtitle without the balance (older server) keeps the debt line; the name is linked once', async () => {
    reply = [{ ...lendCard(), subtitle: 'Карта' } as Card]
    const { user, input } = setup()
    const card = await say(user, input, 'дал в долг Коле 500к')
    expect(within(card).getByTestId('card-debt')).toHaveTextContent('Коля: долг тебе 500 000 ₫')
    expect(within(card).getAllByRole('button', { name: 'Коля' })).toHaveLength(1)
  })

  it('updated + debt: «Изменено: …» with the balance — neither the change nor the debt line repeated', async () => {
    reply = [{
      kind: 'updated', title: '150 000 ₫ · Дал в долг: Коля', subtitle: 'Изменено: 100 000 ₫ → 150 000 ₫ · Коля: долг тебе 450 000 ₫', action_id: 'a-310',
      transaction: debtTx({ id: 310, op: 'lend', amount: '150000' }), changes: ['100 000 ₫ → 150 000 ₫'],
      debt: { debt_id: 5, name: 'Коля', op: 'lend', currency: 'VND', balance_after: '450000', excess: null },
      buttons: [{ id: 'undo', label: 'Отменить', style: 'ghost', hotkey: 'Ctrl+Z', action: { kind: 'undo', action_id: 'a-310' } }],
    } as Card]
    const { user, input } = setup()
    const card = await say(user, input, 'нет, 150к')
    expect(within(card).getAllByText(/100\s000\s₫ → 150\s000\s₫/)).toHaveLength(1)
    expect(within(card).getAllByText(/Коля: долг тебе 450\s000\s₫/)).toHaveLength(1)
    expect(within(card).queryByTestId('card-debt')).toBeNull()
  })

  it('overpayment: the excess is highlighted and «Излишек — мне» is an ordinary send_text button', async () => {
    reply = [{
      ...createdCard(debtTx({ id: 301, op: 'repay_in', amount: '400000' }), 'a-301'),
      title: '400 000 ₫ · Возврат долга: Коля', subtitle: 'Карта · долг закрыт', note: 'Излишек 100 000 ₫ — теперь ты должен: Коля',
      debt: { debt_id: 5, name: 'Коля', op: 'repay_in', currency: 'VND', balance_after: '-100000', excess: '100000' },
      buttons: [
        { id: 'excess', label: 'Излишек — мне', style: 'outline', hotkey: null, action: { kind: 'send_text', text: 'списать мой долг: Коля 100к' } },
        { id: 'undo', label: 'Отменить', style: 'ghost', hotkey: 'Ctrl+Z', action: { kind: 'undo', action_id: 'a-301' } },
      ],
    } as Card]
    const { user, input } = setup()
    const card = await say(user, input, 'Коля вернул 400к')
    // «Карта · долг закрыт» already says the balance: only the excess is highlighted
    expect(within(card).queryByTestId('card-debt-line')).toBeNull()
    expect(within(card).getByTestId('card-debt-excess')).toHaveTextContent('Излишек 100 000 ₫ — теперь ты должен: Коля')
    // the server note says the same — shown once (highlighted), not twice
    expect(within(card).getAllByText(/Излишек 100\s000\s₫/)).toHaveLength(1)
    // «Излишек — мне» on a row of its own, «Отменить» / «Изменить» together below it
    const extra = within(card).getByTestId('card-extra-actions')
    expect(within(extra).getAllByRole('button').map((b) => b.textContent)).toEqual(['Излишек — мне'])
    expect(within(extra).queryByRole('button', { name: /Отменить/ })).toBeNull()
    reply = [createdCard()]
    await user.click(within(card).getByRole('button', { name: 'Излишек — мне' }))
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/assistant/messages')).at(-1)?.body).toMatchObject({ text: 'списать мой долг: Коля 100к' }))
  })

  it('an old-style card (no `debt`) renders as before — no debt line', async () => {
    reply = [createdCard()]
    const { user, input } = setup()
    const card = await say(user, input, 'еда 250к')
    expect(within(card).getByTestId('card-title')).toHaveTextContent('250 000 ₫ · Еда')
    expect(within(card).queryByTestId('card-debt')).toBeNull()
  })

  it('needs_account for a debt inflow keeps the server question and shows the debt line', async () => {
    const t = debtTx({ id: 302, op: 'repay_in', amount: '50', currency: 'USDT', status: 'needs_account', counter_account: null })
    reply = [{
      kind: 'needs_account', title: '50 USDT · Возврат долга: Вася', subtitle: 'осталось 0 USDT · ≈ $50.00', question: 'На какой счёт?', action_id: 'a-302', transaction: t,
      options: [{ account_id: 3, label: 'Кошелёк', hint: null, hotkey: '1' }],
      debt: { debt_id: 6, name: 'Вася', op: 'repay_in', currency: 'USDT', balance_after: '0', excess: null },
      buttons: [
        { id: 'acc-3', label: 'Кошелёк', style: 'outline', hotkey: '1', action: { kind: 'choose_account', transaction_id: 302, account_id: 3 } },
        { id: 'later', label: 'Позже', style: 'ghost', hotkey: null, action: { kind: 'later' } },
      ],
    }]
    const { user, input } = setup()
    const card = await say(user, input, 'вася вернул 50 usdt')
    expect(card).toHaveTextContent('На какой счёт?')
    expect(within(card).getByTestId('card-debt')).toHaveTextContent('Вася · долг закрыт')
  })

  it('answer «кто мне должен?» with `debts`: both lists; compact (Quick) as well; old answer → lines', async () => {
    const answer: Card = {
      kind: 'answer', answer_type: 'balance', title: 'Долги', subtitle: null, headline: 'Мне должны ≈ 1 807 000 ₫', headline_approx: '≈ $69', text: 'Я должен ≈ 1 523 000 ₫',
      lines: [{ label: 'Коля', value: 'долг тебе 300 000 ₫', amount: null, currency: null, share: null, category_id: null, transaction_id: null, icon: null }],
      transactions: [], total_count: null, filter: null, debts: fullDebts,
      buttons: [{ id: 'open', label: 'Открыть долги →', style: 'ghost', hotkey: null, action: { kind: 'open', route: '/accounts?debt=all' } }],
    }
    reply = [answer]
    const { user, input, onOpen } = setup(vi.fn(), true)
    const card = await say(user, input, 'кто мне должен')
    const lists = within(card).getByTestId('card-debts')
    expect(lists).toHaveTextContent('Мне должны')
    expect(lists).toHaveTextContent('Я должен')
    expect(within(lists).getAllByTestId('debt-person')).toHaveLength(5)
    await user.click(within(lists).getByRole('button', { name: 'Открыть долг: Петя' }))
    expect(onOpen).toHaveBeenCalledWith('/accounts?debt=7')
    await user.click(within(card).getByRole('button', { name: /Открыть долги/ }))
    expect(onOpen).toHaveBeenCalledWith('/accounts?debt=all')
  })

  it('answer without `debts` (old server) shows the lines summary', async () => {
    reply = [{
      kind: 'answer', answer_type: 'balance', title: 'Долги', subtitle: null, headline: 'Мне должны ≈ 300 000 ₫', headline_approx: null, text: null,
      lines: [{ label: 'Коля', value: 'долг тебе 300 000 ₫', amount: null, currency: null, share: null, category_id: null, transaction_id: null, icon: null }],
      transactions: [], total_count: null, filter: null, buttons: [],
    }]
    const { user, input } = setup()
    const card = await say(user, input, 'кто мне должен')
    expect(within(card).queryByTestId('card-debts')).toBeNull()
    expect(card).toHaveTextContent('Коля долг тебе 300 000 ₫')
  })
})

describe('«Операции» row of a debt operation', () => {
  it('HandCoins title «Дал в долг: Коля», the name links to the card, the real account only', async () => {
    const t = debtTx({ id: 400, op: 'lend' })
    const onOpen = vi.fn()
    const router = createMemoryRouter([{ path: '*', element: (
      <TxRow tx={t} tz="Asia/Ho_Chi_Minh" accounts={[]} actions={{ onOpen, onDelete: vi.fn(), onDuplicate: vi.fn() }} offline={false} mobile={false} />
    ) }], { initialEntries: ['/operations'] })
    render(<RouterProvider router={router} />)
    const link = screen.getByRole('link', { name: 'Коля' })
    expect(link.closest('[data-tx-id]')).toHaveTextContent('Дал в долг: Коля')
    expect(link.closest('[data-tx-id]')).toHaveTextContent('Карта')
    expect(link.closest('[data-tx-id]')).not.toHaveTextContent('Долг: Коля →')
    await userEvent.setup().click(link)
    expect(router.state.location.search).toBe('?debt=5')
    expect(onOpen).not.toHaveBeenCalled() // the row's edit form does not open
  })
})

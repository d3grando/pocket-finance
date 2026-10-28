// Integration: AI line + provider + cards against a mocked fetch. Checks the money-safety rules:
// Idempotency-Key on every send, same key on retry, «сохранено» only after a 2xx, text kept on errors.
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/tooltip'
import { createdCard, needsAccountCard, response, tx } from '@/test/fixtures'
import { AiCommandBar } from '../AiCommandBar'
import { AssistantProvider } from '../AssistantProvider'
import { CardStack } from '../CardStack'

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown }
let calls: Call[] = []
let handler: (c: Call) => Promise<Response> | Response

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  calls = []
  sessionStorage.clear()
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    const c: Call = {
      url: String(url), method: init.method ?? 'GET', headers: (init.headers ?? {}) as Record<string, string>,
      body: init.body ? JSON.parse(String(init.body)) : undefined,
    }
    calls.push(c)
    return handler(c)
  }))
})
afterEach(() => vi.unstubAllGlobals())

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <AssistantProvider>
          <AiCommandBar />
          <CardStack />
        </AssistantProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  )
  const input = screen.getByTestId('ai-input') as HTMLTextAreaElement
  return { user, input }
}

const sends = () => calls.filter((c) => c.url.endsWith('/assistant/messages'))

describe('AiCommandBar', () => {
  it('Enter sends with an Idempotency-Key; card and cleared field only after 2xx', async () => {
    let release!: () => void
    handler = () => new Promise((r) => { release = () => r(json(response([createdCard()]))) })
    const { user, input } = setup()
    await user.type(input, 'еда 250к{Enter}')
    expect(sends()).toHaveLength(1)
    expect(sends()[0].method).toBe('POST')
    expect(sends()[0].headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
    expect(sends()[0].body).toEqual({ text: 'еда 250к', context: null })
    // still pending: nothing claims to be saved, text stays
    expect(screen.queryByTestId('result-card')).toBeNull()
    expect(input.value).toBe('еда 250к')
    // a second Enter with the same text while in flight is ignored
    await user.type(input, '{Enter}')
    expect(sends()).toHaveLength(1)
    await act(async () => release())
    expect((await screen.findAllByText('250 000 ₫ · Еда')).length).toBeGreaterThan(0)
    await waitFor(() => expect(input.value).toBe(''))
  })

  it('Shift+Enter inserts a newline instead of sending', async () => {
    handler = () => json(response([createdCard()]))
    const { user, input } = setup()
    await user.type(input, 'еда 120к{Shift>}{Enter}{/Shift}такси 45к')
    expect(sends()).toHaveLength(0)
    expect(input.value).toBe('еда 120к\nтакси 45к')
  })

  it('offline: keeps the text, says «не сохранено», [Повторить] resends with the SAME key', async () => {
    let fail = true
    const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    handler = () => {
      if (fail) throw new TypeError('Failed to fetch')
      return json(response([createdCard()]))
    }
    const { user, input } = setup()
    await user.type(input, 'кофе 85к{Enter}')
    expect((await screen.findAllByText('Не сохранено — нет связи')).length).toBeGreaterThan(0)
    online.mockRestore()
    expect(input.value).toBe('кофе 85к')
    const key = sends()[0].headers['Idempotency-Key']
    fail = false
    await user.click(screen.getByTestId('btn-retry'))
    await screen.findAllByText('250 000 ₫ · Еда')
    expect(sends()).toHaveLength(2)
    expect(sends()[1].headers['Idempotency-Key']).toBe(key)
    await waitFor(() => expect(screen.queryAllByTestId('result-card').filter((n) => n.textContent?.includes('нет связи'))).toHaveLength(0))
    await waitFor(() => expect(input.value).toBe(''))
  })

  it('online but no answer: never claims «не сохранено» (the server may have saved it); retry = same key', async () => {
    let fail = true
    handler = () => {
      if (fail) throw new TypeError('Failed to fetch')
      return json(response([createdCard()]))
    }
    const { user, input } = setup()
    await user.type(input, 'кофе 85к{Enter}')
    expect((await screen.findAllByText('Нет ответа сервера — не знаю, сохранилось ли')).length).toBeGreaterThan(0)
    expect(screen.queryByText(/Не сохранено/)).toBeNull()
    expect(input.value).toBe('кофе 85к')
    fail = false
    await user.click(screen.getByTestId('btn-retry'))
    await screen.findAllByText('250 000 ₫ · Еда')
    expect(sends()[1].headers['Idempotency-Key']).toBe(sends()[0].headers['Idempotency-Key'])
    await waitFor(() => expect(screen.queryByText('Нет ответа сервера — не знаю, сохранилось ли')).toBeNull())
  })

  it('5xx: «Ничего не записано», Enter again reuses the key', async () => {
    let status = 500
    handler = () => (status === 500 ? json({ error: { code: 'internal', message: 'boom', details: {}, request_id: 'r' } }, 500) : json(response([createdCard()])))
    const { user, input } = setup()
    await user.type(input, 'кофе 85к{Enter}')
    expect((await screen.findAllByText(/Ничего не записано/)).length).toBeGreaterThan(0)
    status = 200
    await user.type(input, '{Enter}')
    await screen.findAllByText('250 000 ₫ · Еда')
    expect(sends()[1].headers['Idempotency-Key']).toBe(sends()[0].headers['Idempotency-Key'])
  })

  it('«Отменить» posts the undo with its own key and shows «Отменено · Вернуть»', async () => {
    handler = (c) => c.url.includes('/undo')
      ? json({ status: 'undone', undone_action_id: 'a-1', undo_action_id: 'u-1', summary: 'Отменено: 250 000 ₫ · Еда', changed: ['transactions'] })
      : json(response([createdCard()]))
    const { user, input } = setup()
    await user.type(input, 'еда 250к{Enter}')
    await screen.findAllByText('250 000 ₫ · Еда')
    await user.click(screen.getByTestId('btn-undo'))
    expect((await screen.findAllByText('Отменено: 250 000 ₫ · Еда')).length).toBeGreaterThan(0)
    const undo = calls.find((c) => c.url.endsWith('/actions/a-1/undo'))!
    expect(undo.method).toBe('POST')
    expect(undo.headers['Idempotency-Key']).toBeTruthy()
    expect(screen.getByTestId('btn-redo')).toBeInTheDocument()
  })

  it('Ctrl+Z in the empty field undoes the latest card', async () => {
    handler = (c) => c.url.includes('/undo')
      ? json({ status: 'undone', undone_action_id: 'a-1', undo_action_id: 'u-1', summary: 'Отменено: 250 000 ₫ · Еда', changed: [] })
      : json(response([createdCard()]))
    const { user, input } = setup()
    await user.type(input, 'еда 250к{Enter}')
    await screen.findAllByText('250 000 ₫ · Еда')
    await waitFor(() => expect(input.value).toBe(''))
    await user.keyboard('{Control>}z{/Control}')
    await screen.findAllByText('Отменено: 250 000 ₫ · Еда')
  })

  it('needs_account: digit picks the wallet, card becomes variant (a)', async () => {
    handler = (c) => c.url.endsWith('/transactions/125/account')
      ? json({ transaction: tx({ id: 125, amount: '15', currency: 'USDT', account: { id: 4, name: 'Копилка', icon: null }, category: { id: 7, name: 'Подписки', kind: 'expense', icon: null, color: null, is_business: false, is_new: false } }), action_id: 'a-3' })
      : json(response([needsAccountCard()]))
    const { user, input } = setup()
    await user.type(input, 'потратил 15 USDT на подписку{Enter}')
    await screen.findAllByText('С какого счёта?')
    await waitFor(() => expect(input.value).toBe(''))
    await user.keyboard('2')
    await screen.findAllByText('15 USDT · Подписки')
    const assign = calls.find((c) => c.url.endsWith('/transactions/125/account'))!
    expect(assign.body).toEqual({ account_id: 4, version: 1 })
    expect(input.value).toBe('')
  })

  it('unclear keeps the input for editing', async () => {
    handler = () => json(response([{ kind: 'unclear', title: 'Не понял, что записать', subtitle: null, reason: 'missing_amount', question: '«перевел вчера» — не хватает суммы.', keep_input: true, buttons: [] }], { action_ids: [], changed: [] }))
    const { user, input } = setup()
    await user.type(input, 'перевел вчера{Enter}')
    await screen.findAllByText('Не понял, что записать')
    expect(input.value).toBe('перевел вчера')
  })

  it('preview: Enter on focused «Применить» confirms with an Idempotency-Key', async () => {
    const preview = {
      kind: 'preview' as const, title: 'Изменю 2 операции', subtitle: null, preview_token: 'tok-1', operation: 'bulk_update' as const,
      change_summary: 'категория: Прочее → Кофе', items: [{ transaction: tx({ id: 1 }), before: 'Прочее', after: 'Кофе' }, { transaction: tx({ id: 2 }), before: 'Прочее', after: 'Кофе' }],
      total_count: 2, total: 'Итого 500 000 ₫', expires_at: '2026-10-27T08:00:00Z', buttons: [],
    }
    handler = (c) => c.url.includes('/confirm')
      ? json(response([{ kind: 'applied', title: 'Изменено 2 операции', subtitle: null, action_id: 'b-1', count: 2, buttons: [{ id: 'undo', label: 'Отменить', style: 'ghost', hotkey: 'Ctrl+Z', action: { kind: 'undo', action_id: 'b-1' } }] }]))
      : json(response([preview], { action_ids: [], changed: [] }))
    const { user, input } = setup()
    await user.type(input, 'все траты в highlands — кофе{Enter}')
    await screen.findAllByText('Изменю 2 операции')
    await waitFor(() => expect(screen.getByTestId('btn-confirm-preview')).toHaveFocus())
    await user.keyboard('{Enter}')
    await screen.findAllByText('Изменено 2 операции')
    const confirm = calls.find((c) => c.url.endsWith('/assistant/previews/tok-1/confirm'))!
    expect(confirm.headers['Idempotency-Key']).toBeTruthy()
  })
})

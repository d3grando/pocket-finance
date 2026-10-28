// Regression tests for the assistant (shell):
// undo after edit, edit after a change on another device, retry of server-answered cards,
// 409 in progress, auto-retry after the connection returns, typing during a slow send.
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/tooltip'
import { connection } from '@/lib/connection'
import { createdCard, response, tx } from '@/test/fixtures'
import { AiCommandBar } from '../AiCommandBar'
import { AssistantProvider } from '../AssistantProvider'
import { RETRY_IN_PROGRESS, typeOver } from '../logic'
import { CardStack } from '../CardStack'

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown }
let calls: Call[] = []
let handler: (c: Call) => Promise<Response> | Response

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const err = (status: number, code: string, message = 'x', details: Record<string, unknown> = {}) =>
  json({ error: { code, message, details, request_id: 'r' } }, status)

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
    // lookups of the inline edit form
    if (c.method === 'GET' && /\/(categories|accounts)(\?|$)/.test(c.url)) return json([])
    if (c.method === 'GET' && /\/settings(\?|$)/.test(c.url)) return json({ timezone: 'Asia/Ho_Chi_Minh', small_amount_thousands: true })
    return handler(c)
  }))
  connection.markOk()
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
const edited = tx({ amount: '300000', version: 2, category: { id: 2, name: 'Продукты', kind: 'expense', icon: null, color: null, is_business: false, is_new: false } })

describe('«Отменить» after «Изменить»', () => {
  it('removes the operation with its current version instead of a failing undo chain; «Вернуть» restores it', async () => {
    handler = (c) => {
      if (c.url.endsWith('/transactions/123') && c.method === 'PATCH') return json({ transaction: edited, action_id: 'e-1' })
      if (c.url.includes('/transactions/123?version=2') && c.method === 'DELETE') {
        return json({ transaction: { ...edited, version: 3 }, action_id: 'd-1' })
      }
      if (c.url.endsWith('/actions/d-1/undo')) {
        return json({ status: 'undone', undone_action_id: 'd-1', undo_action_id: 'u-9', summary: 'Отменено: удаление 300 000 ₫ · Продукты', changed: ['transactions'] })
      }
      if (c.url.includes('/undo')) return err(409, 'undo_conflict', 'Запись уже изменили после этого действия — отменить нельзя')
      return json(response([createdCard()]))
    }
    const { user, input } = setup()
    await user.type(input, 'еда 250к{Enter}')
    await screen.findAllByText('250 000 ₫ · Еда')
    await user.click(screen.getByTestId('btn-edit'))
    const amount = screen.getByTestId('edit-amount') as HTMLInputElement
    await user.clear(amount)
    await user.type(amount, '300000')
    await user.click(screen.getByTestId('edit-save'))
    await screen.findAllByText('300 000 ₫ · Продукты')
    await user.click(screen.getByTestId('btn-undo'))
    expect((await screen.findAllByText('Отменено: 300 000 ₫ · Продукты')).length).toBeGreaterThan(0)
    // no undo of the creation/edit chain (that is what produced 409), a DELETE with version 2 instead
    expect(calls.filter((c) => c.url.includes('/actions/'))).toHaveLength(0)
    const del = calls.find((c) => c.method === 'DELETE')!
    expect(del.url).toContain('/transactions/123?version=2')
    expect(del.headers['Idempotency-Key']).toBeTruthy()
    await user.click(screen.getByTestId('btn-redo'))
    await screen.findAllByText(/^Возвращено:/)
    expect(calls.some((c) => c.url.endsWith('/actions/d-1/undo'))).toBe(true)
  })

  it('a conflict on «Отменить» redraws the card from the server, the next press removes it', async () => {
    const elsewhere = tx({ amount: '251000', version: 2 })
    handler = (c) => {
      if (c.url.endsWith('/actions/a-1/undo')) return err(409, 'undo_conflict', 'Запись уже изменили после этого действия — отменить нельзя', { current_version: 2 })
      if (c.url.endsWith('/transactions/123') && c.method === 'GET') return json(elsewhere)
      if (c.method === 'DELETE') return json({ transaction: { ...elsewhere, version: 3 }, action_id: 'd-2' })
      return json(response([createdCard()]))
    }
    const { user, input } = setup()
    await user.type(input, 'еда 250к{Enter}')
    await screen.findAllByText('250 000 ₫ · Еда')
    await user.click(screen.getByTestId('btn-undo'))
    await screen.findAllByText('251 000 ₫ · Еда')
    expect((await screen.findAllByText(/Запись изменили на другом устройстве/)).length).toBeGreaterThan(0)
    await user.click(screen.getByTestId('btn-undo'))
    await screen.findAllByText('Отменено: 251 000 ₫ · Еда')
    expect(calls.find((c) => c.method === 'DELETE')!.url).toContain('version=2')
  })
})

describe('«Изменить» after a change on another device', () => {
  it('409 version_conflict re-reads the operation, shows it and reopens the form with the current values', async () => {
    const elsewhere = tx({ amount: '251000', version: 2 })
    let patches = 0
    handler = (c) => {
      if (c.method === 'PATCH') {
        patches++
        const body = c.body as { version: number }
        return body.version === 1 ? err(409, 'version_conflict', 'Запись уже изменили, обновите данные', { current_version: 2 })
          : json({ transaction: tx({ amount: '300000', version: 3 }), action_id: 'e-2' })
      }
      if (c.url.endsWith('/transactions/123') && c.method === 'GET') return json(elsewhere)
      return json(response([createdCard()]))
    }
    const { user, input } = setup()
    await user.type(input, 'еда 250к{Enter}')
    await screen.findAllByText('250 000 ₫ · Еда')
    await user.click(screen.getByTestId('btn-edit'))
    let amount = screen.getByTestId('edit-amount') as HTMLInputElement
    await user.clear(amount)
    await user.type(amount, '300000{Enter}')
    await screen.findAllByText(/Запись изменили на другом устройстве — показал актуальную/)
    await screen.findAllByText('251 000 ₫ · Еда')
    amount = screen.getByTestId('edit-amount') as HTMLInputElement
    expect(amount.value).toBe('251000')
    await user.clear(amount)
    await user.type(amount, '300000{Enter}')
    await screen.findAllByText('300 000 ₫ · Еда')
    expect(patches).toBe(2)
    expect((calls.filter((c) => c.method === 'PATCH')[1].body as { version: number }).version).toBe(2)
  })
})

describe('[Повторить]', () => {
  it('on a server «AI недоступен» card sends a NEW key (the old one only replays the stored error)', async () => {
    let n = 0
    handler = () => {
      n++
      if (n === 1) {
        return json(response([{
          kind: 'error', code: 'ai_unavailable', title: 'AI сейчас недоступен', subtitle: null, text: 'Простые записи работают', keep_input: true,
          buttons: [{ id: 'retry', label: 'Повторить', style: 'outline', hotkey: 'Enter', action: { kind: 'retry' } }],
        }], { action_ids: [], changed: [], ai_available: false }))
      }
      return json(response([createdCard()]))
    }
    const { user, input } = setup()
    await user.type(input, 'вчера в ресторане 600к{Enter}')
    await screen.findAllByText('AI сейчас недоступен')
    await user.click(screen.getByTestId('btn-retry'))
    await screen.findAllByText('250 000 ₫ · Еда')
    expect(sends()).toHaveLength(2)
    expect(sends()[1].headers['Idempotency-Key']).not.toBe(sends()[0].headers['Idempotency-Key'])
    // the error card is replaced by the result
    await waitFor(() => expect(screen.queryByText('AI сейчас недоступен')).toBeNull())
  })
})

describe('409 idempotency_in_progress (w4)', () => {
  const saved = [...RETRY_IN_PROGRESS]
  afterEach(() => { RETRY_IN_PROGRESS.splice(0, RETRY_IN_PROGRESS.length, ...saved) })

  it('keeps polling with the same key and never says «Ничего не записано» while the first attempt runs', async () => {
    RETRY_IN_PROGRESS.splice(0, RETRY_IN_PROGRESS.length, 5, 5)
    let n = 0
    handler = () => (++n <= 3 ? err(409, 'idempotency_in_progress', 'Этот запрос ещё выполняется, подождите') : json(response([createdCard()])))
    const { user, input } = setup()
    await user.type(input, 'кофе 45к{Enter}')
    expect((await screen.findAllByText('Ещё обрабатывается…')).length).toBeGreaterThan(0)
    expect(screen.queryByText(/Ничего не записано/)).toBeNull()
    const keys = new Set(sends().map((c) => c.headers['Idempotency-Key']))
    expect(keys.size).toBe(1)
    // [Повторить] → the result replaces every card of this attempt
    await user.click(screen.getByTestId('btn-retry'))
    await screen.findAllByText('250 000 ₫ · Еда')
    await waitFor(() => expect(screen.queryByText('Ещё обрабатывается…')).toBeNull())
    expect(new Set(sends().map((c) => c.headers['Idempotency-Key'])).size).toBe(1)
  })
})

describe('auto-retry when the server answers again (w8, critic-tz)', () => {
  it('re-sends a «нет связи» card with the same key after offline → reconnecting → online', async () => {
    let fail = true
    const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    handler = (c) => {
      if (c.url.endsWith('/assistant/messages') && fail) throw new TypeError('Failed to fetch')
      return json(response([createdCard()]))
    }
    const { user, input } = setup()
    await user.type(input, 'кофе 21к{Enter}')
    await screen.findAllByText('Не сохранено — нет связи')
    // the browser went offline, came back; the first REST answer arrives before SSE (status
    // «reconnecting», never a direct offline → online jump)
    act(() => connection.setBrowserOnline(false))
    online.mockReturnValue(true)
    act(() => connection.setBrowserOnline(true))
    act(() => connection.markFail())
    fail = false
    act(() => connection.markOk())
    await screen.findAllByText('250 000 ₫ · Еда')
    expect(sends()).toHaveLength(2)
    expect(sends()[1].headers['Idempotency-Key']).toBe(sends()[0].headers['Idempotency-Key'])
    await waitFor(() => expect(screen.queryByText('Не сохранено — нет связи')).toBeNull())
    online.mockRestore()
  })
})

describe('typing while a slow request is in flight (p9)', () => {
  it('the new phrase replaces the sent text instead of gluing onto it, and survives the answer', async () => {
    let release!: () => void
    handler = () => new Promise((r) => { release = () => r(json(response([createdCard()]))) })
    const { user, input } = setup()
    await user.type(input, 'hôm qua ăn phở hết 60k{Enter}')
    await user.type(input, 'кофе 9к')
    expect(input.value).toBe('кофе 9к')
    await act(async () => release())
    await screen.findAllByText('250 000 ₫ · Еда')
    expect(input.value).toBe('кофе 9к')
    expect(sends()).toHaveLength(1)
  })

  it('typeOver keeps edits inside the sent text and whitespace-only additions', () => {
    expect(typeOver('кофе 13к', 'кофе 13кеда 14к', 'кофе 13к')).toBe('еда 14к')
    expect(typeOver('кофе 13к', 'екофе 13к', 'кофе 13к')).toBe('е')
    expect(typeOver('кофе 13к', 'кофе 13', 'кофе 13к')).toBe('кофе 13')
    expect(typeOver('кофе 13к', 'кофе 13к\n', 'кофе 13к')).toBe('кофе 13к\n')
    expect(typeOver('кофе 13к', 'кофе 13кх', null)).toBe('кофе 13кх')
  })
})

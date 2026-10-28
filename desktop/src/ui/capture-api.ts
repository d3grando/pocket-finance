// API access for the lite capture page: Bearer device token from the bridge, Idempotency-Key on
// writes, 401 → token is dead → main shows the login screen. SSE over fetch (API.md §12).

export class NetworkError extends Error {}
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message)
  }
}

let baseUrl = ''

export async function initApi(): Promise<void> {
  const cfg = await window.pocket.config.get()
  baseUrl = cfg.apiBaseUrl
  window.pocket.config.onChange((c) => {
    baseUrl = c.apiBaseUrl
  })
}

export function newKey(): string {
  return crypto.randomUUID()
}

async function headers(extra: Record<string, string> = {}): Promise<Record<string, string>> {
  const token = await window.pocket.auth.getToken()
  const h: Record<string, string> = { Accept: 'application/json', ...extra }
  if (token) h.Authorization = `Bearer ${token}`
  return h
}

export async function call<T>(method: string, path: string, body?: unknown, idempotencyKey?: string): Promise<T> {
  const h = await headers(body !== undefined ? { 'Content-Type': 'application/json' } : {})
  if (idempotencyKey) h['Idempotency-Key'] = idempotencyKey
  let res: Response
  try {
    res = await fetch(`${baseUrl}${path}`, {
      method,
      headers: h,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    })
  } catch {
    throw new NetworkError('Нет связи с сервером')
  }
  const data = (await res.json().catch(() => null)) as T & { error?: { code?: string; message?: string } }
  if (res.status === 401) {
    await window.pocket.auth.clearToken()
    throw new ApiError(401, 'unauthorized', 'Нужно войти заново')
  }
  if (!res.ok) {
    const e = data?.error
    throw new ApiError(res.status, e?.code ?? 'internal', e?.message ?? `Ошибка сервера (${res.status})`)
  }
  return data as T
}

/** Subscribe to /events/stream; calls onChange for `change` events. Reconnects with backoff 1→30 s. */
export function startEvents(onChange: (entities: string[]) => void, onReconnect: () => void): () => void {
  let stopped = false
  let ctrl: AbortController | null = null
  let delay = 1000
  const loop = async () => {
    while (!stopped) {
      ctrl = new AbortController()
      try {
        const res = await fetch(`${baseUrl}/events/stream`, {
          headers: await headers({ Accept: 'text/event-stream' }),
          signal: ctrl.signal,
        })
        if (res.status === 401) {
          await window.pocket.auth.clearToken()
          return
        }
        if (!res.ok || !res.body) throw new Error(`sse ${res.status}`)
        delay = 1000
        onReconnect()
        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
        let buf = ''
        for (;;) {
          const { value, done } = await reader.read()
          if (done) break
          buf += value
          let i: number
          while ((i = buf.indexOf('\n\n')) >= 0) {
            const raw = buf.slice(0, i)
            buf = buf.slice(i + 2)
            let ev = 'message'
            let data = ''
            for (const line of raw.split('\n')) {
              if (line.startsWith('event:')) ev = line.slice(6).trim()
              else if (line.startsWith('data:')) data += line.slice(5).trim()
            }
            if (ev === 'change') {
              try {
                const d = JSON.parse(data) as { entities?: string[] }
                onChange(Array.isArray(d.entities) ? d.entities : [])
              } catch {
                onChange([])
              }
            }
          }
        }
      } catch {
        /* reconnect below */
      }
      if (stopped) return
      await new Promise((r) => setTimeout(r, delay))
      delay = Math.min(delay * 2, 30_000)
    }
  }
  void loop()
  return () => {
    stopped = true
    ctrl?.abort()
  }
}

// ---- API shapes used by the page (subset of docs/API.md) ----
export interface Button {
  id: string
  label: string
  style?: string
  hotkey?: string | null
  action: {
    kind: string
    action_id?: string
    transaction_id?: number
    account_id?: number | null
    category_id?: number
    preview_token?: string
    text?: string
    route?: string
  }
}
export interface Tx {
  id: number
  kind: string
  status: string
  occurred_at: string
  amount: string
  currency: string
  version?: number
  title?: string | null
  account?: { id: number; name: string } | null
  category?: { id: number; name: string; is_new?: boolean } | null
}
export interface Card {
  kind: string
  title: string
  subtitle?: string | null
  buttons?: Button[]
  approx?: string | null
  note?: string | null
  action_id?: string
  transaction?: Tx
  question?: string
  text?: string
  keep_input?: boolean
  headline?: string
  lines?: { label: string; value?: string }[]
  change_summary?: string
  total?: string
}
export interface AssistantResponse {
  cards: Card[]
  action_ids?: string[]
  ai_available?: boolean
}
export interface UndoResult {
  status: string
  undone_action_id: string
  undo_action_id: string
  summary: string
}
export interface MutationResult {
  transaction: Tx
  action_id: string
}
export interface Kpi {
  value: string | null
  currency: string
}
export interface SummaryOut {
  spent_today: Kpi
  spent_month: Kpi
  income_month: Kpi
  needs_account_count: number
}
export interface TxPage {
  items: Tx[]
}

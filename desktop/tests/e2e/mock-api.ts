// Minimal mock of the Pocket API (docs/API.md) for the Electron smoke tests: device login,
// health, me, stats summary, transactions, assistant messages (with Idempotency-Key replay),
// undo, SSE. CORS like the real backend for the app://pocket origin. Records every request.
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { randomUUID } from 'node:crypto'

export const PASSWORD = 'correct horse battery'
export const TOKEN = 'pfd_' + 'T0k3n'.repeat(9)

export interface Recorded {
  method: string
  path: string
  headers: http.IncomingHttpHeaders
  body: unknown
}

interface TxRow {
  id: number
  kind: string
  status: string
  occurred_at: string
  occurred_on: string
  amount: string
  currency: string
  category: { id: number; name: string }
  account: { id: number; name: string }
  title: string
  version: number
  deleted: boolean
  action_id: string
}

export class MockApi {
  readonly requests: Recorded[] = []
  private server: http.Server
  private txs: TxRow[] = []
  private nextId = 100
  private idem = new Map<string, unknown>()
  private actions = new Map<string, number>() // action_id → tx id
  private undoResults = new Map<string, unknown>()
  revoked = false
  online = true
  url = ''

  constructor() {
    this.server = http.createServer((req, res) => void this.handle(req, res))
  }

  async start(): Promise<string> {
    await new Promise<void>((r) => this.server.listen(0, '127.0.0.1', () => r()))
    const { port } = this.server.address() as AddressInfo
    this.url = `http://127.0.0.1:${port}`
    return this.url
  }

  async stop(): Promise<void> {
    this.server.closeAllConnections()
    await new Promise<void>((r) => this.server.close(() => r()))
  }

  find(method: string, path: string): Recorded[] {
    return this.requests.filter((r) => r.method === method && r.path.startsWith(path))
  }

  private send(res: http.ServerResponse, status: number, body: unknown, extra: Record<string, string> = {}) {
    res.writeHead(status, { 'Content-Type': 'application/json', ...this.cors(), ...extra })
    res.end(body === undefined ? '' : JSON.stringify(body))
  }

  private cors(): Record<string, string> {
    return {
      'Access-Control-Allow-Origin': 'app://pocket',
      'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': 'authorization, content-type, idempotency-key, x-csrf-token, accept',
      'Access-Control-Allow-Methods': 'GET, POST, PATCH, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Private-Network': 'true',
      Vary: 'Origin',
    }
  }

  private err(res: http.ServerResponse, status: number, code: string, message: string) {
    this.send(res, status, { error: { code, message, details: {}, request_id: 'mock' } })
  }

  private authed(req: http.IncomingMessage): boolean {
    return !this.revoked && req.headers.authorization === `Bearer ${TOKEN}`
  }

  private txOut(t: TxRow) {
    return { ...t, deleted: undefined }
  }

  private async handle(req: http.IncomingMessage, res: http.ServerResponse) {
    const url = new URL(req.url ?? '/', 'http://x')
    const path = url.pathname.replace(/^\/api\/v1/, '')
    let raw = ''
    for await (const chunk of req) raw += chunk
    let body: unknown = undefined
    try {
      body = raw ? JSON.parse(raw) : undefined
    } catch {
      body = raw
    }
    if (req.method !== 'OPTIONS') this.requests.push({ method: req.method ?? 'GET', path: path + url.search, headers: req.headers, body })
    if (req.method === 'OPTIONS') {
      res.writeHead(204, this.cors())
      res.end()
      return
    }
    if (!this.online) {
      req.socket.destroy()
      return
    }

    if (path === '/health') return this.send(res, 200, { status: 'ok', db: true, version: '0.1.0', server_time: new Date().toISOString() })
    if (path === '/auth/device-login' && req.method === 'POST') {
      const b = body as { password?: string; username?: string; device_name?: string; platform?: string }
      if (b?.password !== PASSWORD) return this.err(res, 401, 'unauthorized', 'Неверный пароль')
      this.revoked = false
      return this.send(res, 201, { token: TOKEN, device_id: randomUUID() })
    }
    if (!this.authed(req)) return this.err(res, 401, 'unauthorized', 'Нужно войти')

    if (path === '/auth/me') return this.send(res, 200, { user_id: 1, username: 'owner', auth: 'device', source: 'desktop', onboarding_done: true, has_passkeys: false })
    if (path === '/auth/logout' && req.method === 'POST') {
      this.revoked = true
      return this.send(res, 200, { ok: true })
    }
    if (path === '/events/stream') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', ...this.cors() })
      res.write(`event: hello\ndata: {"server_time":"${new Date().toISOString()}","version":"0.1.0"}\n\n`)
      const t = setInterval(() => res.write('event: ping\ndata: {}\n\n'), 1000)
      req.on('close', () => clearInterval(t))
      return
    }
    const live = this.txs.filter((t) => !t.deleted)
    if (path === '/stats/summary') {
      const spent = live.filter((t) => t.kind === 'expense').reduce((s, t) => s + BigInt(t.amount), 0n)
      const kpi = (v: string) => ({ value: v, currency: 'VND', approx_usd: null, change_ratio: null, sparkline: [] })
      return this.send(res, 200, {
        currency: 'VND',
        spent_today: kpi(String(spent)),
        spent_month: kpi(String(12_000_000n + spent)),
        income_month: kpi('30000000'),
        net_month: kpi('0'),
        needs_account_count: 0,
        missing_rates_count: 0,
      })
    }
    if (path === '/transactions' && req.method === 'GET') {
      const items = [...live].reverse().slice(0, Number(url.searchParams.get('limit') ?? 50)).map((t) => this.txOut(t))
      return this.send(res, 200, { items, total: live.length, limit: 50, offset: 0, totals: {} })
    }
    if (path === '/assistant/messages' && req.method === 'POST') {
      const key = String(req.headers['idempotency-key'] ?? '')
      if (!key) return this.err(res, 428, 'idempotency_key_required', 'Нужен Idempotency-Key')
      const saved = this.idem.get(key)
      if (saved) return this.send(res, 200, saved)
      const text = String((body as { text?: string })?.text ?? '')
      const m = /^(\S+)\s+(\d+)\s*к$/i.exec(text.trim())
      let out: unknown
      if (!m) {
        out = { request_id: 1, route: 'parser', ai_available: true, action_ids: [], changed: [], cards: [{ kind: 'unclear', title: 'Не понял, что записать', subtitle: null, reason: 'missing_amount', question: 'Не хватает суммы', keep_input: true, buttons: [] }] }
      } else {
        const name = m[1].toLowerCase() === 'еда' ? 'Еда' : m[1]
        const amount = String(BigInt(m[2]) * 1000n)
        const action_id = randomUUID()
        const now = new Date()
        const tx: TxRow = {
          id: this.nextId++,
          kind: 'expense',
          status: 'posted',
          occurred_at: now.toISOString(),
          occurred_on: now.toISOString().slice(0, 10),
          amount,
          currency: 'VND',
          category: { id: 1, name },
          account: { id: 1, name: 'Карта' },
          title: name,
          version: 1,
          deleted: false,
          action_id,
        }
        this.txs.push(tx)
        this.actions.set(action_id, tx.id)
        const fmt = amount.replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
        out = {
          request_id: 2,
          route: 'parser',
          ai_available: true,
          action_ids: [action_id],
          changed: ['transactions', 'stats'],
          cards: [
            {
              kind: 'created',
              title: `${fmt} ₫ · ${name}`,
              subtitle: 'Карта · сейчас',
              action_id,
              transaction: this.txOut(tx),
              category_is_new: false,
              buttons: [
                { id: 'undo', label: 'Отменить', style: 'ghost', hotkey: 'Ctrl+Z', action: { kind: 'undo', action_id } },
                { id: 'edit', label: 'Изменить', style: 'ghost', hotkey: 'Ctrl+E', action: { kind: 'edit', transaction_id: tx.id } },
              ],
            },
          ],
        }
      }
      this.idem.set(key, out)
      return this.send(res, 200, out)
    }
    const undo = /^\/actions\/([^/]+)\/undo$/.exec(path)
    if (undo && req.method === 'POST') {
      const key = String(req.headers['idempotency-key'] ?? '')
      if (!key) return this.err(res, 428, 'idempotency_key_required', 'Нужен Idempotency-Key')
      const prev = this.undoResults.get(undo[1])
      if (prev) return this.send(res, 200, { ...(prev as object), status: 'already_undone' })
      const txId = this.actions.get(undo[1])
      const tx = this.txs.find((t) => t.id === txId)
      if (!tx) return this.err(res, 404, 'not_found', 'Действие не найдено')
      tx.deleted = !tx.deleted
      const undoActionId = randomUUID()
      this.actions.set(undoActionId, tx.id)
      const r = { status: 'undone', undone_action_id: undo[1], undo_action_id: undoActionId, summary: `Отменено: ${tx.title}`, changed: ['transactions'] }
      this.undoResults.set(undo[1], r)
      return this.send(res, 200, r)
    }
    return this.err(res, 404, 'not_found', `mock: ${req.method} ${path}`)
  }
}

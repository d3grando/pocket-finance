// Test helpers for the pages: jsdom polyfills, a fetch mock that records calls, router + query providers.
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { render } from '@testing-library/react'
import { vi } from 'vitest'

// ---------------------------------------------------------------- jsdom polyfills
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const g = globalThis as unknown as Record<string, unknown>
g.ResizeObserver ??= RO
g.IntersectionObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
}
if (typeof window !== 'undefined') {
  window.matchMedia ??= ((q: string) => ({ matches: false, media: q, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false })) as unknown as typeof window.matchMedia
  Element.prototype.scrollIntoView ??= function () {}
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.releasePointerCapture ??= () => {}
  Element.prototype.setPointerCapture ??= () => {}
  window.HTMLElement.prototype.scrollTo ??= function () {}
  window.scrollTo = (() => {}) as typeof window.scrollTo
}

/** Make the viewport «mobile» (max-width: 767px) for useIsMobile. */
export function setMobile(mobile: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: mobile && q.includes('max-width: 767px'),
    media: q,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

// ---------------------------------------------------------------- fetch mock
export interface Call {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
  headers: Record<string, string>
}

export type Handler = (call: Call) => unknown | Promise<unknown>
export type Routes = Record<string, Handler | object>

export class HttpError {
  constructor(
    public status: number,
    public code: string,
    public message = 'ошибка',
  ) {}
}

/** Mock `fetch` for `/api/v1/...`. Route keys: "GET /transactions", "PATCH /transactions/:id". */
export function mockApi(routes: Routes) {
  const calls: Call[] = []
  const match = (method: string, path: string): Handler | object | undefined => {
    for (const [key, h] of Object.entries(routes)) {
      const [m, p] = key.split(' ')
      if (m !== method) continue
      const re = new RegExp('^' + p.replace(/:[a-z_]+/g, '[^/]+') + '$')
      if (re.test(path)) return h
    }
    return undefined
  }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), 'http://localhost')
    const path = url.pathname.replace(/^\/api\/v1/, '')
    const method = (init.method ?? 'GET').toUpperCase()
    const headers = Object.fromEntries(Object.entries((init.headers ?? {}) as Record<string, string>))
    const call: Call = { method, path, query: url.searchParams, body: init.body ? JSON.parse(String(init.body)) : undefined, headers }
    calls.push(call)
    const h = match(method, path)
    if (h === undefined) return new Response(JSON.stringify({ error: { code: 'not_found', message: `no mock for ${method} ${path}`, details: {}, request_id: null } }), { status: 404, headers: { 'Content-Type': 'application/json' } })
    try {
      const data = typeof h === 'function' ? await (h as Handler)(call) : h
      if (data instanceof Response) return data
      return new Response(JSON.stringify(data ?? { ok: true }), { status: method === 'POST' ? 201 : 200, headers: { 'Content-Type': 'application/json' } })
    } catch (e) {
      if (e instanceof HttpError) return new Response(JSON.stringify({ error: { code: e.code, message: e.message, details: {}, request_id: 'r1' } }), { status: e.status, headers: { 'Content-Type': 'application/json' } })
      if (e instanceof TypeError) throw e
      throw e
    }
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock, find: (method: string, path: string | RegExp) => calls.filter((c) => c.method === method && (typeof path === 'string' ? c.path === path : path.test(c.path))) }
}

// ---------------------------------------------------------------- render
export function renderPage(element: ReactNode, { route = '/', path = '*' }: { route?: string; path?: string } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: Infinity }, mutations: { retry: 0 } } })
  const router = createMemoryRouter(
    [
      { path, element },
      { path: '*', element: <div data-testid="elsewhere">elsewhere</div> },
    ],
    { initialEntries: [route] },
  )
  const r = render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { ...r, qc, router }
}

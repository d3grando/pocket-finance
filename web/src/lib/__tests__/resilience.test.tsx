// Regression tests (resilience / PWA): proxy 503 is «no connection», offline requests fail
// at once instead of pausing (no hidden write queue), the app shell never stays blank without a network,
// renames refetch the operation lists.
import { render, screen, waitFor } from '@testing-library/react'
import { MutationObserver, QueryClientProvider, onlineManager, useQuery } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AuthGate } from '@/features/auth/AuthGate'
import { NetworkError, isGatewayDown, onApiEvent, request } from '../api/client'
import { useMe } from '../api/hooks'
import { entityKeys, qk } from '../api/keys'
import { createQueryClient } from '../api/queryClient'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => { vi.unstubAllGlobals(); onlineManager.setOnline(true) })

describe('proxy answers are not «на связи» (w2_api_down)', () => {
  it('502/504 and a 503 without an app code are the proxy; app 503 codes are the API', () => {
    expect(isGatewayDown(502)).toBe(true)
    expect(isGatewayDown(504, 'whatever')).toBe(true)
    expect(isGatewayDown(503, 'service_unavailable')).toBe(true)
    expect(isGatewayDown(503, null)).toBe(true)
    expect(isGatewayDown(503, 'ai_unavailable')).toBe(false)
    expect(isGatewayDown(503, 'rates_unavailable')).toBe(false)
    expect(isGatewayDown(500, 'internal')).toBe(false)
    expect(isGatewayDown(200)).toBe(false)
  })

  it('request() reports Caddy 503 JSON as unreachable and an AI 503 as reachable', async () => {
    const events: string[] = []
    const off = onApiEvent((e) => events.push(e.type))
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (String(url).includes('caddy')
      ? json({ error: { code: 'service_unavailable', message: 'Сервер временно недоступен', details: null, request_id: null } }, 503)
      : json({ error: { code: 'ai_unavailable', message: 'AI сейчас недоступен', details: {}, request_id: 'r' } }, 503))))
    await expect(request('GET', '/caddy')).rejects.toMatchObject({ status: 503 })
    await expect(request('GET', '/ai')).rejects.toMatchObject({ code: 'ai_unavailable' })
    off()
    expect(events).toEqual(['unreachable', 'reachable'])
  })
})

describe('offline: no paused queries / hidden mutation queue (offline2 C)', () => {
  it('a mutation without a network runs at once and fails, it is not paused to run later by itself', async () => {
    onlineManager.setOnline(false)
    const qc = createQueryClient()
    const fn = vi.fn(async () => { throw new NetworkError() })
    const m = new MutationObserver(qc, { mutationFn: fn })
    await expect(m.mutate()).rejects.toBeInstanceOf(NetworkError)
    expect(fn).toHaveBeenCalledTimes(1)
    expect(m.getCurrentResult().isPaused).toBe(false)
    onlineManager.setOnline(true)
    await new Promise((r) => setTimeout(r, 20))
    expect(fn).toHaveBeenCalledTimes(1) // nothing re-ran on reconnect
  })

  it('a query without a network fails instead of staying «pending» forever', async () => {
    onlineManager.setOnline(false)
    const qc = createQueryClient()
    await expect(qc.fetchQuery({ queryKey: ['x'], queryFn: async () => { throw new NetworkError() } })).rejects.toBeInstanceOf(NetworkError)
  })
})

describe('renames refetch the operations (t9 rename)', () => {
  it('categories / accounts / merchants changes invalidate transaction lists and the open operation', () => {
    for (const e of ['categories', 'accounts', 'merchants'] as const) {
      const firsts = entityKeys[e].map((k) => k[0])
      expect(firsts).toEqual(expect.arrayContaining(['transactions', 'transaction']))
    }
  })
})

describe('<AuthGate> cold start without a network (w1b, pwa A)', () => {
  let meCalls = 0
  beforeEach(() => {
    meCalls = 0
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/me')) meCalls++
      throw new TypeError('Failed to fetch')
    }))
  })

  function Shell() {
    // the real shell has its own /auth/me observer — its mount must not restart the check in a loop
    const me = useMe()
    useQuery({ queryKey: qk.settings, queryFn: async () => { throw new NetworkError() }, enabled: !!me.data })
    return <div data-testid="shell">shell</div>
  }

  it('renders the shell after the failed check (never a blank page) and does not loop on /auth/me', async () => {
    const qc = createQueryClient()
    const router = createMemoryRouter([{ path: '/', element: <AuthGate><Shell /></AuthGate> }, { path: '/login', element: <p>login</p> }])
    render(
      <QueryClientProvider client={qc}>
        <TooltipProvider><RouterProvider router={router} /></TooltipProvider>
      </QueryClientProvider>,
    )
    expect(await screen.findByTestId('shell')).toBeInTheDocument()
    const after = meCalls
    await new Promise((r) => setTimeout(r, 300))
    expect(screen.getByTestId('shell')).toBeInTheDocument()
    expect(meCalls).toBe(after)
    expect(meCalls).toBeLessThanOrEqual(1) // network errors are not retried (finite)
    await waitFor(() => expect(screen.queryByText('login')).toBeNull())
  })
})

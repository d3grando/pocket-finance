// app://pocket — serves the packaged frontend (web build + desktop pages) from the app package.
// A standard + secure scheme: real origin "app://pocket" (CORS at the API: DESKTOP_ORIGIN),
// fetch/streams allowed, no file:// anywhere.
import fs from 'node:fs'
import { protocol } from 'electron'
import { APP_HOST, APP_SCHEME } from '../shared/ipc'
import { contentSecurityPolicy, mimeFor, resolveAppPath, type ResolveRoots } from './assets-resolver'

/** Must run before app 'ready'. */
export function registerSchemePrivileges(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: APP_SCHEME,
      privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true, codeCache: true },
    },
  ])
}

function isFile(file: string): boolean {
  try {
    return fs.statSync(file).isFile()
  } catch {
    return false
  }
}

function notFound(): Response {
  return new Response('Not found', { status: 404, headers: { 'Content-Type': 'text/plain; charset=utf-8' } })
}

export function handleAppProtocol(roots: () => ResolveRoots, apiOrigin: () => string): void {
  protocol.handle(APP_SCHEME, async (req) => {
    let url: URL
    try {
      url = new URL(req.url)
    } catch {
      return notFound()
    }
    if (url.host !== APP_HOST) return notFound()
    if (req.method !== 'GET' && req.method !== 'HEAD') return new Response('Method not allowed', { status: 405 })
    const r = resolveAppPath(url.pathname, roots(), isFile)
    if (!r) return notFound()
    let body: Buffer
    try {
      body = await fs.promises.readFile(r.file)
    } catch {
      return notFound()
    }
    const type = mimeFor(r.file)
    const headers: Record<string, string> = {
      'Content-Type': type,
      'X-Content-Type-Options': 'nosniff',
      'Cache-Control': 'no-cache',
    }
    if (type.startsWith('text/html')) {
      headers['Content-Security-Policy'] = contentSecurityPolicy(apiOrigin())
      headers['Referrer-Policy'] = 'no-referrer'
      headers['X-Frame-Options'] = 'DENY'
    }
    return new Response(req.method === 'HEAD' ? null : new Uint8Array(body), { status: 200, headers })
  })
}

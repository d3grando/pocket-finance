// app://pocket file resolution (pure, unit-tested; the protocol handler is in protocol.ts).
//   /__pocket/<file>  → desktop-native pages (login, settings, lite capture) from out/ui
//   /<file>           → packaged web build (desktop/renderer, built by `pnpm --dir ../web build:desktop`)
//   /<route>          → SPA fallback to the web index.html (react-router routes: /quick, /settings/…)
// Path traversal is impossible: the decoded path is normalized and must stay inside its root.
import path from 'node:path'
import { UI_PREFIX } from '../shared/ipc'

export interface ResolveRoots {
  webRoot: string
  uiRoot: string
}

export type Resolved = { file: string; root: 'web' | 'ui'; fallback: boolean } | null

export type ExistsFn = (file: string) => boolean

function safeJoin(root: string, rel: string): string | null {
  const full = path.resolve(root, rel)
  const r = path.relative(root, full)
  if (r === '' || r.startsWith('..') || path.isAbsolute(r)) return null
  return full
}

export function resolveAppPath(pathname: string, roots: ResolveRoots, exists: ExistsFn): Resolved {
  let decoded: string
  try {
    decoded = decodeURIComponent(pathname)
  } catch {
    return null
  }
  if (decoded.includes('\0') || decoded.includes('\\')) return null
  // any dot-dot segment is refused outright (browsers already collapse them in real URLs)
  if (decoded.split('/').includes('..')) return null
  const norm = path.posix.normalize(`/${decoded}`)

  if (norm.startsWith(UI_PREFIX)) {
    const rel = norm.slice(UI_PREFIX.length)
    if (!rel) return null
    const file = safeJoin(roots.uiRoot, rel)
    if (file && exists(file)) return { file, root: 'ui', fallback: false }
    // nested asset requests from the ui pages
    const i = rel.lastIndexOf('assets/')
    if (i > 0) {
      const f2 = safeJoin(roots.uiRoot, rel.slice(i))
      if (f2 && exists(f2)) return { file: f2, root: 'ui', fallback: false }
    }
    return null
  }

  const rel = norm === '/' ? 'index.html' : norm.slice(1)
  const file = safeJoin(roots.webRoot, rel)
  if (file && exists(file)) return { file, root: 'web', fallback: false }

  // Vite `base: './'` makes asset URLs relative: a deep route like /settings/devices asks for
  // /settings/assets/x.js — map it to /assets/x.js.
  const ai = rel.lastIndexOf('assets/')
  if (ai > 0) {
    const f2 = safeJoin(roots.webRoot, rel.slice(ai))
    if (f2 && exists(f2)) return { file: f2, root: 'web', fallback: false }
  }

  // SPA fallback only for "route-like" paths (no file extension)
  const last = rel.split('/').pop() ?? ''
  if (!last.includes('.')) {
    const index = safeJoin(roots.webRoot, 'index.html')
    if (index && exists(index)) return { file: index, root: 'web', fallback: true }
  }
  return null
}

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.webmanifest': 'application/manifest+json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm',
}

export function mimeFor(file: string): string {
  return MIME[path.extname(file).toLowerCase()] ?? 'application/octet-stream'
}

/**
 * CSP for every HTML page served from app://pocket: scripts only from the package, network
 * only to the configured API origin. No inline scripts, no eval, no plugins, no frames.
 */
export function contentSecurityPolicy(apiOrigin: string): string {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self' ${apiOrigin}`,
    "media-src 'none'",
    "object-src 'none'",
    "frame-src 'none'",
    "worker-src 'self' blob:",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join('; ')
}

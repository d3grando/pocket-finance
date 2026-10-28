import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { contentSecurityPolicy, mimeFor, resolveAppPath } from '../../src/main/assets-resolver'
import { allowedExternalUrl, isInternalUrl, normalizeServerUrl, sanitizeRoute } from '../../src/main/urls'

describe('normalizeServerUrl', () => {
  it('accepts host / https URL / API URL and derives /api/v1', () => {
    expect(normalizeServerUrl('pocket.example.com')).toEqual({ ok: true, serverUrl: 'https://pocket.example.com', apiBaseUrl: 'https://pocket.example.com/api/v1' })
    expect(normalizeServerUrl(' https://pocket.example.com/ ')).toMatchObject({ ok: true, serverUrl: 'https://pocket.example.com' })
    expect(normalizeServerUrl('https://pocket.example.com/api/v1')).toMatchObject({ ok: true, apiBaseUrl: 'https://pocket.example.com/api/v1' })
    expect(normalizeServerUrl('https://example.com:8443/pocket')).toMatchObject({ ok: true, apiBaseUrl: 'https://example.com:8443/pocket/api/v1' })
  })
  it('requires HTTPS (plain http only for loopback dev/tests)', () => {
    expect(normalizeServerUrl('http://pocket.example.com').ok).toBe(false)
    expect(normalizeServerUrl('http://127.0.0.1:8000')).toMatchObject({ ok: true, apiBaseUrl: 'http://127.0.0.1:8000/api/v1' })
    expect(normalizeServerUrl('ftp://x.y').ok).toBe(false)
    expect(normalizeServerUrl('javascript:alert(1)').ok).toBe(false)
    expect(normalizeServerUrl('https://user:pw@pocket.example.com').ok).toBe(false)
    expect(normalizeServerUrl('https://pocket.example.com/?a=1').ok).toBe(false)
    expect(normalizeServerUrl('').ok).toBe(false)
  })
})

describe('navigation policy', () => {
  it('internal pages are app://pocket only', () => {
    expect(isInternalUrl('app://pocket/quick')).toBe(true)
    expect(isInternalUrl('app://pocket/__pocket/login.html')).toBe(true)
    expect(isInternalUrl('app://evil/quick')).toBe(false)
    expect(isInternalUrl('https://pocket.example.com/')).toBe(false)
    expect(isInternalUrl('file:///C:/Windows/system32')).toBe(false)
  })
  it('only the configured server and t.me/<bot> links go to the system browser (normalized href)', () => {
    const server = 'https://pocket.example.com'
    const ext = (u: unknown) => allowedExternalUrl(u, server)
    expect(ext('https://pocket.example.com/analytics?period=this_month')).toBe('https://pocket.example.com/analytics?period=this_month')
    expect(ext('HTTPS://POCKET.EXAMPLE.COM/a b')).toBe('https://pocket.example.com/a%20b') // href, not the raw string
    expect(ext('https://t.me/pocket_test_bot')).toBe('https://t.me/pocket_test_bot')
    // a page that got the device token cannot carry it out
    expect(ext('https://evil.example.net/?t=pfd_secret')).toBeNull()
    expect(ext('https://t.me/evil_bot?start=pfd_secret')).toBeNull()
    expect(ext('https://t.me/s/channel/pfd_secret')).toBeNull()
    expect(ext('https://pocket.example.com.evil.net/')).toBeNull()
    expect(ext('https://pocket.example.com:8443/')).toBeNull() // another port = another origin
    expect(ext('http://pocket.example.com/')).toBeNull()
    expect(ext('file:///C:/x.exe')).toBeNull()
    expect(ext('ms-settings:privacy')).toBeNull()
    expect(ext('javascript:alert(1)')).toBeNull()
    expect(ext('https://a:b@pocket.example.com/')).toBeNull()
    expect(ext(42)).toBeNull()
    expect(ext('https://pocket.example.com/' + 'x'.repeat(2100))).toBeNull()
    // http only for a loopback server (dev/tests); no server configured → only t.me
    expect(allowedExternalUrl('http://127.0.0.1:8000/x', 'http://127.0.0.1:8000')).toBe('http://127.0.0.1:8000/x')
    expect(allowedExternalUrl('http://10.0.0.5/x', 'http://10.0.0.5')).toBeNull()
    expect(allowedExternalUrl('https://pocket.example.com/', '')).toBeNull()
    expect(allowedExternalUrl('https://t.me/pocket_test_bot', '')).toBe('https://t.me/pocket_test_bot')
  })
  it('full panel routes stay inside the app', () => {
    expect(sanitizeRoute(undefined)).toBe('/')
    expect(sanitizeRoute('/analytics?period=this_month')).toBe('/analytics?period=this_month')
    expect(sanitizeRoute('/operations?status=needs_account')).toBe('/operations?status=needs_account')
    expect(sanitizeRoute('/../../etc/passwd')).toBe('/etc/passwd')
    expect(sanitizeRoute('//evil.com/x')).toBeNull()
    expect(sanitizeRoute('https://evil.com')).toBeNull()
    expect(sanitizeRoute('/\\evil.com')).toBeNull()
    expect(sanitizeRoute(42)).toBeNull()
  })
})

describe('resolveAppPath (app:// protocol)', () => {
  const roots = { webRoot: path.resolve('/pkg/renderer'), uiRoot: path.resolve('/pkg/out/ui') }
  const files = new Set([
    path.join(roots.webRoot, 'index.html'),
    path.join(roots.webRoot, 'assets', 'index-abc.js'),
    path.join(roots.webRoot, 'icons', 'icon-192.png'),
    path.join(roots.uiRoot, 'login.html'),
    path.join(roots.uiRoot, 'assets', 'login-x.js'),
  ])
  const exists = (f: string) => files.has(f)
  const r = (p: string) => resolveAppPath(p, roots, exists)

  it('serves web files and the SPA fallback for routes', () => {
    expect(r('/')).toMatchObject({ file: path.join(roots.webRoot, 'index.html'), fallback: false })
    expect(r('/quick')).toMatchObject({ file: path.join(roots.webRoot, 'index.html'), fallback: true })
    expect(r('/settings/devices')).toMatchObject({ fallback: true })
    expect(r('/assets/index-abc.js')?.file).toBe(path.join(roots.webRoot, 'assets', 'index-abc.js'))
    // relative asset URL requested from a nested route
    expect(r('/settings/assets/index-abc.js')?.file).toBe(path.join(roots.webRoot, 'assets', 'index-abc.js'))
    expect(r('/missing.js')).toBeNull()
  })
  it('serves the desktop pages under /__pocket/', () => {
    expect(r('/__pocket/login.html')).toMatchObject({ root: 'ui', file: path.join(roots.uiRoot, 'login.html') })
    expect(r('/__pocket/assets/login-x.js')?.root).toBe('ui')
    expect(r('/__pocket/nope.html')).toBeNull()
    expect(r('/__pocket/')).toBeNull()
  })
  it('blocks traversal and odd encodings', () => {
    expect(r('/../../etc/passwd')).toBeNull()
    expect(r('/%2e%2e/%2e%2e/etc/passwd')).toBeNull()
    expect(r('/__pocket/../../main/index.js')).toBeNull()
    expect(r('/..%5c..%5cwindows%5cwin.ini')).toBeNull()
    expect(r('/%00')).toBeNull()
    expect(r('/%E0%A4%A')).toBeNull()
  })
  it('mime types and CSP', () => {
    expect(mimeFor('a.js')).toContain('javascript')
    expect(mimeFor('a.woff2')).toBe('font/woff2')
    expect(mimeFor('a.unknown')).toBe('application/octet-stream')
    const csp = contentSecurityPolicy('https://pocket.example.com')
    expect(csp).toContain("script-src 'self'")
    expect(csp).toContain("connect-src 'self' https://pocket.example.com")
    expect(csp).not.toContain('unsafe-eval')
    expect(csp).toContain("object-src 'none'")
  })
})

// What the shell's desktop mode does: configureApi({ baseUrl: config.apiBaseUrl, getToken: pocket.auth.getToken }).
const $ = (id) => document.getElementById(id)
$('path').textContent = location.pathname + location.search
$('kind').textContent = window.pocket ? window.pocket.windowKind : 'no-bridge'
async function main() {
  const cfg = await window.pocket.config.get()
  $('api').textContent = cfg.apiBaseUrl
  const token = await window.pocket.auth.getToken()
  const res = await fetch(cfg.apiBaseUrl + '/auth/me', { headers: { Authorization: 'Bearer ' + token, Accept: 'application/json' } })
  if (res.status === 401) {
    await window.pocket.auth.clearToken()
    return
  }
  const me = await res.json()
  $('me').textContent = me.username
}
$('open-full').addEventListener('click', () => window.pocket.window.openFull('/operations?status=needs_account'))

// Esc the way the real /quick page handles it: a Radix dropdown (⋯) closes on Esc with
// preventDefault (document capture listener); the AI field clears its text first and hides the
// window when empty (AiCommandBar onEscapeEmpty). Esc anywhere else is left to main.
const menu = $('menu')
$('menu-btn').addEventListener('click', () => {
  menu.hidden = !menu.hidden
  if (!menu.hidden) $('menu-settings').focus()
})
document.addEventListener(
  'keydown',
  (e) => {
    if (e.key !== 'Escape' || menu.hidden) return
    e.preventDefault()
    menu.hidden = true
    $('menu-btn').focus()
  },
  true,
)
$('input').addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return
  e.preventDefault()
  e.stopPropagation()
  if (e.target.value) e.target.value = ''
  else window.pocket.window.hide()
})
window.pocket.window.onShown(() => $('input').focus())
main().catch((e) => {
  $('me').textContent = 'error: ' + e.message
})

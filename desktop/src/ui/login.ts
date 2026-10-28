// Login screen inside the popup (DESIGN §10.10 state 8): password once → device token in safeStorage.
// A fresh install without a built-in server (no PF_DEFAULT_SERVER_URL at build time) first asks for
// the server address, then for the password.
import './styles.css'
import './login.css'
import type { DesktopConfig } from '../shared/ipc'
import { normalizeServerUrl } from '../main/urls'
import { $, bindConnectionDot, hostOf } from './common'

const form = $<HTMLFormElement>('#form')
const pw = $<HTMLInputElement>('#password')
const submit = $<HTMLButtonElement>('#submit')
const error = $('#error')
const serverForm = $<HTMLFormElement>('#server-form')
const serverInput = $<HTMLInputElement>('#server-url')
const serverSubmit = $<HTMLButtonElement>('#server-submit')
const serverError = $('#server-error')
let hasServer = false

function render(c: DesktopConfig): void {
  const was = hasServer
  hasServer = !!c.serverUrl
  const host = hasServer ? hostOf(c.serverUrl) : ''
  $('#server-host').textContent = host
  $('#server-host2').textContent = host
  serverForm.classList.toggle('hidden', hasServer)
  form.classList.toggle('hidden', !hasServer)
  $('#password-note').classList.toggle('hidden', !hasServer)
  $('#server-line').classList.toggle('hidden', !hasServer)
  if (hasServer !== was || !document.activeElement || document.activeElement === document.body) focusField()
}

function focusField(): void {
  if (hasServer) pw.focus()
  else serverInput.focus()
}

async function init(): Promise<void> {
  render(await window.pocket.config.get())
  window.pocket.config.onChange(render)
  bindConnectionDot($('#dot'))
  focusField()
}

serverForm.addEventListener('submit', async (e) => {
  e.preventDefault()
  if (serverSubmit.disabled) return
  const n = normalizeServerUrl(serverInput.value)
  if (!n.ok) {
    serverError.textContent = n.message
    serverInput.focus()
    return
  }
  serverSubmit.disabled = true
  serverError.textContent = ''
  try {
    const r = await window.pocket.config.setServerUrl(n.serverUrl)
    if (r.ok) render(r.config)
    else serverError.textContent = r.message
  } catch {
    serverError.textContent = 'Не удалось сохранить адрес. Попробуйте ещё раз.'
  } finally {
    serverSubmit.disabled = false
  }
})

form.addEventListener('submit', async (e) => {
  e.preventDefault()
  if (submit.disabled) return
  if (!pw.value) {
    error.textContent = 'Введите пароль'
    pw.focus()
    return
  }
  submit.disabled = true
  error.textContent = ''
  try {
    const r = await window.pocket.auth.login(pw.value)
    if (!r.ok) {
      error.textContent = r.message
      pw.select()
      pw.focus()
    } else {
      pw.value = ''
    }
  } catch {
    error.textContent = 'Не удалось войти. Попробуйте ещё раз.'
  } finally {
    submit.disabled = false
  }
})

$('#eye').addEventListener('click', () => {
  pw.type = pw.type === 'password' ? 'text' : 'password'
  pw.focus()
})
$('#close').addEventListener('click', () => void window.pocket.window.hide())
$('#change-server').addEventListener('click', () => void window.pocket.window.openSettings())
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') void window.pocket.window.hide()
})
window.pocket.window.onShown(() => focusField())

void init()

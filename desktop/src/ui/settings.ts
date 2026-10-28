// Desktop settings window: hotkey (with «taken» check), autostart, server address, device logout.
import './styles.css'
import './settings.css'
import { acceleratorFromEvent, formatAccelerator } from '../shared/accelerator'
import { defaultHotkey, type DesktopConfig, type HotkeyResult } from '../shared/ipc'
import { normalizeServerUrl } from '../main/urls'
import { $, h } from './common'

const hotkeyBox = $<HTMLButtonElement>('#hotkey')
const hotkeyMsg = $('#hotkey-msg')
const suggestBox = $('#hotkey-suggest')
const autostart = $<HTMLInputElement>('#autostart')
const server = $<HTMLInputElement>('#server')
const serverMsg = $('#server-msg')
let cfg: DesktopConfig
let recording = false

function setMsg(el: HTMLElement, text: string, kind: 'ok' | 'error' | '' = ''): void {
  el.textContent = text
  el.className = `caption ${kind}`.trim()
}

function render(c: DesktopConfig): void {
  cfg = c
  if (!recording) hotkeyBox.textContent = formatAccelerator(c.hotkey, c.platform)
  autostart.checked = c.autostart
  if (document.activeElement !== server) server.value = c.serverUrl
  $('#device-state').textContent = c.signedIn
    ? 'Вход выполнен. Токен устройства хранится в защищённом хранилище системы.'
    : 'Вход не выполнен — откройте Pocket и введите пароль.'
  $<HTMLButtonElement>('#logout').disabled = !c.signedIn
  $('#version').textContent = `Pocket ${c.version} · ${c.renderer === 'web' ? 'полный интерфейс' : 'облегчённый интерфейс'}`
  if (c.hotkeyStatus === 'taken' && !recording && !hotkeyMsg.textContent) {
    setMsg(hotkeyMsg, `${formatAccelerator(c.hotkey, c.platform)} занято другой программой. Выберите другое сочетание.`, 'error')
    void showSuggestions()
  }
}

async function showSuggestions(list?: string[]): Promise<void> {
  const items = list ?? (await window.pocket.hotkey.suggest())
  suggestBox.replaceChildren(
    ...(items.length ? [h('span', { class: 'caption', text: 'Свободны:' })] : []),
    ...items.map((acc) => {
      const b = h('button', { class: 'chip num', text: formatAccelerator(acc, cfg.platform) })
      b.addEventListener('click', () => void applyHotkey(acc))
      return b
    }),
  )
}

async function applyHotkey(acc: string): Promise<void> {
  const r: HotkeyResult = await window.pocket.hotkey.set(acc)
  if (r.ok) {
    setMsg(hotkeyMsg, `Сохранено: ${formatAccelerator(r.accelerator, cfg.platform)}`, 'ok')
    suggestBox.replaceChildren()
    hotkeyBox.textContent = formatAccelerator(r.accelerator, cfg.platform)
  } else {
    setMsg(hotkeyMsg, r.message, 'error')
    void showSuggestions(r.suggestions)
  }
}

async function startRecording(): Promise<void> {
  if (recording) return
  recording = true
  await window.pocket.hotkey.setRecording(true)
  hotkeyBox.classList.add('recording')
  hotkeyBox.textContent = 'Нажмите сочетание…'
  setMsg(hotkeyMsg, 'Esc — отмена')
  hotkeyBox.focus()
}

async function stopRecording(): Promise<void> {
  if (!recording) return
  recording = false
  hotkeyBox.classList.remove('recording')
  await window.pocket.hotkey.setRecording(false)
  hotkeyBox.textContent = formatAccelerator(cfg.hotkey, cfg.platform)
}

hotkeyBox.addEventListener('click', () => void startRecording())
$('#hotkey-change').addEventListener('click', () => void startRecording())
$('#hotkey-reset').addEventListener('click', () => void applyHotkey(defaultHotkey(cfg.platform)))
hotkeyBox.addEventListener('blur', () => void stopRecording())
hotkeyBox.addEventListener('keydown', async (e) => {
  if (!recording) return
  e.preventDefault()
  e.stopPropagation()
  if (e.key === 'Escape' && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey) {
    await stopRecording()
    setMsg(hotkeyMsg, '')
    return
  }
  const r = acceleratorFromEvent(e)
  if (r.kind === 'pending') return
  if (r.kind === 'invalid') {
    setMsg(hotkeyMsg, r.message, 'error')
    return
  }
  hotkeyBox.textContent = formatAccelerator(r.accelerator, cfg.platform)
  const res = await window.pocket.hotkey.set(r.accelerator) // checked while suspended (free?)
  recording = false
  hotkeyBox.classList.remove('recording')
  await window.pocket.hotkey.setRecording(false) // re-registers the (new or old) hotkey
  if (res.ok) {
    setMsg(hotkeyMsg, `Сохранено: ${formatAccelerator(res.accelerator, cfg.platform)}`, 'ok')
    suggestBox.replaceChildren()
  } else {
    hotkeyBox.textContent = formatAccelerator(cfg.hotkey, cfg.platform)
    setMsg(hotkeyMsg, res.message, 'error')
    void showSuggestions(res.suggestions)
  }
})

autostart.addEventListener('change', async () => {
  const actual = await window.pocket.autostart.set(autostart.checked)
  autostart.checked = actual
})

$<HTMLFormElement>('#server-form').addEventListener('submit', async (e) => {
  e.preventDefault()
  const n = normalizeServerUrl(server.value)
  if (!n.ok) {
    setMsg(serverMsg, n.message, 'error')
    return
  }
  // the confirmation is a native dialog in main (a page cannot fake it)
  const r = await window.pocket.config.setServerUrl(n.serverUrl)
  if (r.ok) {
    setMsg(serverMsg, 'Сохранено', 'ok')
    server.value = r.config.serverUrl
  } else setMsg(serverMsg, r.message, 'error')
})

$('#logout').addEventListener('click', async () => {
  if (!confirm('Выйти на этом устройстве? Токен будет отозван, для входа понадобится пароль.')) return
  await window.pocket.auth.logout()
})
$('#all-settings').addEventListener('click', () => void window.pocket.window.openFull('/settings'))
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !recording && document.activeElement !== server) void window.pocket.window.hide()
})

void window.pocket.config.get().then(render)
window.pocket.config.onChange(render)

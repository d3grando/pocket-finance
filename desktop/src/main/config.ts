// Local desktop settings: <userData>/config.json. No secrets here (the token is in token-store).
import fs from 'node:fs'
import path from 'node:path'
import { normalizeAccelerator } from '../shared/accelerator'
import { DEFAULT_SERVER_URL, defaultHotkey } from '../shared/ipc'
import { normalizeServerUrl } from './urls'

export interface StoredConfig {
  serverUrl: string
  hotkey: string
  autostart: boolean
  /** Autostart is enabled by default once, on the first run of the installed app. */
  autostartInitialized: boolean
}

export const DEFAULTS: StoredConfig = {
  serverUrl: DEFAULT_SERVER_URL,
  hotkey: defaultHotkey(process.platform),
  autostart: true,
  autostartInitialized: false,
}

/** Merge a parsed JSON object over defaults, dropping anything invalid. */
export function sanitizeConfig(raw: unknown): StoredConfig {
  const out: StoredConfig = { ...DEFAULTS }
  if (!raw || typeof raw !== 'object') return out
  const r = raw as Record<string, unknown>
  if (typeof r.serverUrl === 'string') {
    const s = normalizeServerUrl(r.serverUrl)
    if (s.ok) out.serverUrl = s.serverUrl
  }
  if (typeof r.hotkey === 'string') {
    const h = normalizeAccelerator(r.hotkey)
    if (h) out.hotkey = h
  }
  if (typeof r.autostart === 'boolean') out.autostart = r.autostart
  if (typeof r.autostartInitialized === 'boolean') out.autostartInitialized = r.autostartInitialized
  return out
}

export class ConfigStore {
  private data: StoredConfig

  constructor(private readonly file: string) {
    this.data = ConfigStore.read(file)
  }

  static read(file: string): StoredConfig {
    try {
      return sanitizeConfig(JSON.parse(fs.readFileSync(file, 'utf8')))
    } catch {
      return { ...DEFAULTS }
    }
  }

  get(): StoredConfig {
    return { ...this.data }
  }

  update(patch: Partial<StoredConfig>): StoredConfig {
    this.data = sanitizeConfig({ ...this.data, ...patch })
    this.save()
    return this.get()
  }

  private save(): void {
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true })
      const tmp = `${this.file}.tmp`
      fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2), 'utf8')
      fs.renameSync(tmp, this.file)
    } catch {
      /* settings are best effort; the app keeps working with in-memory values */
    }
  }
}

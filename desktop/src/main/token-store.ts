// Device token storage («Токен хранится в защищённом хранилище Windows»).
// Encrypted with Electron safeStorage (DPAPI on Windows, Keychain on macOS, libsecret/kwallet
// on Linux) and written to <userData>/device-token.bin. The plaintext token never touches disk:
// if encryption is unavailable the token is kept in memory only (asks for the password after
// a restart) — never stored in plain text.
import fs from 'node:fs'
import path from 'node:path'

export interface CryptoPort {
  isEncryptionAvailable(): boolean
  encryptString(plain: string): Buffer
  decryptString(encrypted: Buffer): string
}

export const TOKEN_RE = /^pfd_[A-Za-z0-9_-]{16,256}$/

export function isValidDeviceToken(token: unknown): token is string {
  return typeof token === 'string' && TOKEN_RE.test(token)
}

export class TokenStore {
  private cache: string | null | undefined = undefined
  private _persisted = false

  constructor(
    private readonly file: string,
    private readonly crypto: CryptoPort,
  ) {}

  /** True when the current token is stored encrypted on disk (survives restarts). */
  get persisted(): boolean {
    return this._persisted
  }

  get(): string | null {
    if (this.cache !== undefined) return this.cache
    this.cache = null
    try {
      if (!fs.existsSync(this.file)) return null
      if (!this.crypto.isEncryptionAvailable()) return null
      const plain = this.crypto.decryptString(fs.readFileSync(this.file))
      if (isValidDeviceToken(plain)) {
        this.cache = plain
        this._persisted = true
      } else {
        this.removeFile()
      }
    } catch {
      // unreadable / encrypted for another Windows user → forget it, ask for the password again
      this.removeFile()
    }
    return this.cache
  }

  set(token: string): { ok: boolean; persisted: boolean } {
    if (!isValidDeviceToken(token)) return { ok: false, persisted: false }
    this.cache = token
    this._persisted = false
    try {
      if (this.crypto.isEncryptionAvailable()) {
        const enc = this.crypto.encryptString(token)
        fs.mkdirSync(path.dirname(this.file), { recursive: true })
        const tmp = `${this.file}.tmp`
        fs.writeFileSync(tmp, enc, { mode: 0o600 })
        fs.renameSync(tmp, this.file)
        this._persisted = true
      }
    } catch {
      this._persisted = false
    }
    return { ok: true, persisted: this._persisted }
  }

  clear(): void {
    this.cache = null
    this._persisted = false
    this.removeFile()
  }

  private removeFile(): void {
    try {
      fs.rmSync(this.file, { force: true })
    } catch {
      /* ignore */
    }
  }
}

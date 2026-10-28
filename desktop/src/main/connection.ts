// Connection indicator source for the tray icon and the pages: GET /health every 30 s, right
// away when the popup is shown and after the server address changes.
import type { ConnectionState } from '../shared/ipc'

export const CHECK_INTERVAL_MS = 30_000

export class ConnectionMonitor {
  private _state: ConnectionState = 'unknown'
  private timer: ReturnType<typeof setInterval> | null = null
  private inflight: Promise<ConnectionState> | null = null
  private generation = 0

  constructor(
    private readonly probe: () => Promise<boolean>,
    private readonly onChange: (s: ConnectionState) => void,
    private readonly intervalMs = CHECK_INTERVAL_MS,
  ) {}

  get state(): ConnectionState {
    return this._state
  }

  start(): void {
    this.stop()
    void this.check()
    this.timer = setInterval(() => void this.check(), this.intervalMs)
    this.timer.unref?.()
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
  }

  /** Server address changed: forget the old state and probe again. */
  reset(): void {
    this.generation++
    this.inflight = null
    this.set('unknown')
    void this.check()
  }

  check(): Promise<ConnectionState> {
    if (this.inflight) return this.inflight
    const gen = this.generation
    this.inflight = this.probe()
      .catch(() => false)
      .then((ok) => {
        if (gen === this.generation) {
          this.inflight = null
          this.set(ok ? 'online' : 'offline')
        }
        return this._state
      })
    return this.inflight
  }

  private set(s: ConnectionState): void {
    if (s === this._state) return
    this._state = s
    this.onChange(s)
  }
}

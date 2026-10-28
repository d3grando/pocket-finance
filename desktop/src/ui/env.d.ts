/// <reference types="vite/client" />
import type { PocketBridge } from '../shared/ipc'

declare global {
  interface Window {
    pocket: PocketBridge
  }
}
export {}

// Vite dev server for the operations Playwright smoke (port 5202): web/vite.config.ts on a fixed port,
// without the API proxy (the smoke mocks the API).
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, mergeConfig } from 'vite'
import base from '../../../../vite.config.ts'

const here = path.dirname(fileURLToPath(import.meta.url))
const webRoot = path.resolve(here, '../../../..')

export default defineConfig((env) => {
  const b = typeof base === 'function' ? base(env) : base
  return mergeConfig(b, {
    root: webRoot,
    server: { host: '127.0.0.1', port: 5202, strictPort: true, proxy: {} },
  })
})

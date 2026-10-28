// Render PWA icons from the SVG logo (DESIGN §13.5) with Playwright Chromium, run:
//   node scripts/gen-icons.mjs
// Writes public/icons/{icon,maskable}-{192,512}.png, public/apple-touch-icon.png (180, opaque),
// public/favicon.ico (16/32/48 PNG-in-ICO) and public/favicon.svg.
import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const brand = (f) => readFileSync(resolve(root, 'assets/brand', f), 'utf8')
const out = (f) => resolve(root, 'public', f)

const jobs = [
  ['pocket-icon.svg', 192, 'icons/icon-192.png', true],
  ['pocket-icon.svg', 512, 'icons/icon-512.png', true],
  ['pocket-icon-maskable.svg', 192, 'icons/maskable-192.png', false],
  ['pocket-icon-maskable.svg', 512, 'icons/maskable-512.png', false],
  ['pocket-icon-maskable.svg', 180, 'apple-touch-icon.png', false],
  ['pocket-icon.svg', 16, 'tmp-16.png', true],
  ['pocket-icon.svg', 32, 'tmp-32.png', true],
  ['pocket-icon.svg', 48, 'tmp-48.png', true],
]

mkdirSync(out('icons'), { recursive: true })
const browser = await chromium.launch()
const page = await browser.newPage({ deviceScaleFactor: 1 })
const pngs = {}
for (const [svg, size, file, transparent] of jobs) {
  await page.setViewportSize({ width: size, height: size })
  const markup = brand(svg).replace(/width="\d+" height="\d+"/, `width="${size}" height="${size}"`)
  await page.setContent(`<!doctype html><html><body style="margin:0;background:transparent">${markup}</body></html>`)
  const buf = await page.screenshot({ omitBackground: transparent, clip: { x: 0, y: 0, width: size, height: size } })
  if (file.startsWith('tmp-')) pngs[size] = buf
  else writeFileSync(out(file), buf)
}
await browser.close()

// ICO container with embedded PNGs (supported by all current browsers and Windows Vista+)
const sizes = [16, 32, 48]
const header = Buffer.alloc(6 + 16 * sizes.length)
header.writeUInt16LE(0, 0); header.writeUInt16LE(1, 2); header.writeUInt16LE(sizes.length, 4)
let offset = header.length
sizes.forEach((s, i) => {
  const e = 6 + i * 16
  header.writeUInt8(s, e); header.writeUInt8(s, e + 1); header.writeUInt8(0, e + 2); header.writeUInt8(0, e + 3)
  header.writeUInt16LE(1, e + 4); header.writeUInt16LE(32, e + 6)
  header.writeUInt32LE(pngs[s].length, e + 8); header.writeUInt32LE(offset, e + 12)
  offset += pngs[s].length
})
writeFileSync(out('favicon.ico'), Buffer.concat([header, ...sizes.map((s) => pngs[s])]))
copyFileSync(resolve(root, 'assets/brand/pocket-icon.svg'), out('favicon.svg'))
console.log('icons written')

// Render the brand SVGs (DESIGN.md §13) into the app/tray icons. Run: `pnpm icons`.
// Uses Playwright's Chromium to rasterize (no ImageMagick/rsvg needed on the build host) and packs .ico
// files itself: BMP (32-bit BGRA + AND mask) entries for < 256 px, PNG entry for 256 px —
// the most compatible layout for Windows shell, tray and NSIS.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const brand = path.join(root, 'assets', 'brand')
const svg = (name) => fs.readFileSync(path.join(brand, name), 'utf8')

const browser = await chromium.launch()
const page = await browser.newPage()

/** → { png: Buffer, rgba: Buffer } for a square raster of `size` px */
async function render(svgText, size) {
  const r = await page.evaluate(
    async ({ svgText, size }) => {
      const img = new Image()
      img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgText)))
      await img.decode()
      const c = document.createElement('canvas')
      c.width = size
      c.height = size
      const ctx = c.getContext('2d')
      ctx.imageSmoothingQuality = 'high'
      ctx.drawImage(img, 0, 0, size, size)
      const data = ctx.getImageData(0, 0, size, size).data
      let bin = ''
      for (let i = 0; i < data.length; i++) bin += String.fromCharCode(data[i])
      return { png: c.toDataURL('image/png').split(',')[1], rgba: btoa(bin) }
    },
    { svgText, size },
  )
  return { png: Buffer.from(r.png, 'base64'), rgba: Buffer.from(r.rgba, 'base64') }
}

function bmpEntry(rgba, size) {
  const header = Buffer.alloc(40)
  header.writeUInt32LE(40, 0)
  header.writeInt32LE(size, 4)
  header.writeInt32LE(size * 2, 8) // XOR + AND masks
  header.writeUInt16LE(1, 12)
  header.writeUInt16LE(32, 14)
  header.writeUInt32LE(0, 16)
  const xor = Buffer.alloc(size * size * 4)
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const src = ((size - 1 - y) * size + x) * 4 // bottom-up
      const dst = (y * size + x) * 4
      xor[dst] = rgba[src + 2]
      xor[dst + 1] = rgba[src + 1]
      xor[dst + 2] = rgba[src]
      xor[dst + 3] = rgba[src + 3]
    }
  }
  const rowBytes = Math.ceil(size / 32) * 4
  const and = Buffer.alloc(rowBytes * size)
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const a = rgba[((size - 1 - y) * size + x) * 4 + 3]
      if (a === 0) and[y * rowBytes + (x >> 3)] |= 0x80 >> (x & 7)
    }
  }
  header.writeUInt32LE(xor.length + and.length, 20)
  return Buffer.concat([header, xor, and])
}

async function ico(svgText, sizes) {
  const images = []
  for (const s of sizes) {
    const { png, rgba } = await render(svgText, s)
    images.push({ size: s, data: s >= 256 ? png : bmpEntry(rgba, s) })
  }
  const head = Buffer.alloc(6 + 16 * images.length)
  head.writeUInt16LE(0, 0)
  head.writeUInt16LE(1, 2)
  head.writeUInt16LE(images.length, 4)
  let offset = head.length
  images.forEach((img, i) => {
    const o = 6 + i * 16
    head.writeUInt8(img.size >= 256 ? 0 : img.size, o)
    head.writeUInt8(img.size >= 256 ? 0 : img.size, o + 1)
    head.writeUInt8(0, o + 2)
    head.writeUInt8(0, o + 3)
    head.writeUInt16LE(1, o + 4)
    head.writeUInt16LE(32, o + 6)
    head.writeUInt32LE(img.data.length, o + 8)
    head.writeUInt32LE(offset, o + 12)
    offset += img.data.length
  })
  return Buffer.concat([head, ...images.map((i) => i.data)])
}

const out = (rel, buf) => {
  const f = path.join(root, rel)
  fs.mkdirSync(path.dirname(f), { recursive: true })
  fs.writeFileSync(f, buf)
  console.log(`${rel}  ${buf.length} B`)
}

const main = svg('pocket-icon.svg')
const offline = svg('pocket-icon-offline.svg')
const template = svg('pocket-tray-template.svg')

out('build/icon.ico', await ico(main, [16, 24, 32, 48, 64, 128, 256]))
out('build/icon.png', (await render(main, 512)).png)
out('assets/icon.png', (await render(main, 256)).png)
out('assets/tray.ico', await ico(main, [16, 20, 24, 32, 40, 48]))
out('assets/tray-offline.ico', await ico(offline, [16, 20, 24, 32, 40, 48]))
out('assets/tray.png', (await render(main, 16)).png)
out('assets/tray@2x.png', (await render(main, 32)).png)
out('assets/tray-offline.png', (await render(offline, 16)).png)
out('assets/tray-offline@2x.png', (await render(offline, 32)).png)
out('assets/trayTemplate.png', (await render(template, 16)).png)
out('assets/trayTemplate@2x.png', (await render(template, 32)).png)

await browser.close()

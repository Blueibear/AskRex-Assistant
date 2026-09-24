import { existsSync, readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import { inflateSync } from 'node:zlib'
import { describe, expect, it } from 'vitest'

const repoRoot = resolve(import.meta.dirname, '..', '..')
const guiRoot = resolve(repoRoot, 'gui')
const expectedWindowsIconSizes = [16, 24, 32, 48, 64, 128, 256]

type IconProvenance = {
  product: string
  artwork: string
  canonicalArtwork: string
  canonicalArtworkSha256: string
  windowsIcon: string
  windowsIconSha256: string
}

function sha256(path: string): string {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function renderedIcoPixel(icon: Buffer, size: number, x: number, y: number): number[] {
  const entryCount = icon.readUInt16LE(4)
  const entryOffset = Array.from({ length: entryCount }, (_, index) => 6 + index * 16).find(
    (offset) => (icon[offset] === 0 ? 256 : icon[offset]) === size
  )
  expect(entryOffset).toBeDefined()

  const imageOffset = icon.readUInt32LE(entryOffset! + 12)
  const imageLength = icon.readUInt32LE(entryOffset! + 8)
  const image = icon.subarray(imageOffset, imageOffset + imageLength)
  expect(image.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))

  let offset = 8
  const dataChunks: Buffer[] = []
  while (offset < image.length) {
    const length = image.readUInt32BE(offset)
    const type = image.toString('ascii', offset + 4, offset + 8)
    if (type === 'IDAT') dataChunks.push(image.subarray(offset + 8, offset + 8 + length))
    offset += length + 12
  }

  const raw = inflateSync(Buffer.concat(dataChunks))
  const bytesPerRow = size * 4
  const rows: Buffer[] = []
  let rawOffset = 0
  let previous = Buffer.alloc(bytesPerRow)
  for (let rowIndex = 0; rowIndex < size; rowIndex += 1) {
    const filter = raw[rawOffset]
    rawOffset += 1
    const row = Buffer.from(raw.subarray(rawOffset, rawOffset + bytesPerRow))
    rawOffset += bytesPerRow
    for (let index = 0; index < row.length; index += 1) {
      const left = index >= 4 ? row[index - 4] : 0
      const above = previous[index]
      const upperLeft = index >= 4 ? previous[index - 4] : 0
      if (filter === 1) row[index] = (row[index] + left) & 0xff
      if (filter === 2) row[index] = (row[index] + above) & 0xff
      if (filter === 3) row[index] = (row[index] + Math.floor((left + above) / 2)) & 0xff
      if (filter === 4) {
        const prediction = left + above - upperLeft
        const nearest = [left, above, upperLeft].reduce((best, candidate) =>
          Math.abs(prediction - candidate) < Math.abs(prediction - best) ? candidate : best
        )
        row[index] = (row[index] + nearest) & 0xff
      }
    }
    rows.push(row)
    previous = row
  }

  return Array.from(rows[y].subarray(x * 4, x * 4 + 4))
}

describe('Windows application icon packaging', () => {
  it('uses the documented AskRex artwork ICO for the Windows executable and installer', () => {
    const packageJson = JSON.parse(
      readFileSync(resolve(guiRoot, 'package.json'), 'utf8')
    ) as { build: { win: { icon: string } } }

    expect(packageJson.build.win.icon).toBe('src/assets/icon.ico')
    const executableIcon = resolve(guiRoot, packageJson.build.win.icon)
    expect(existsSync(executableIcon)).toBe(true)

    const provenance = JSON.parse(
      readFileSync(resolve(guiRoot, 'src/assets/icon-provenance.json'), 'utf8')
    ) as IconProvenance
    const iconBytes = readFileSync(executableIcon)
    const entryCount = iconBytes.readUInt16LE(4)
    const sizes = Array.from({ length: entryCount }, (_, index) => {
      const offset = 6 + index * 16
      const width = iconBytes[offset] === 0 ? 256 : iconBytes[offset]
      const height = iconBytes[offset + 1] === 0 ? 256 : iconBytes[offset + 1]
      expect(width).toBe(height)
      return width
    })

    expect(provenance.product).toBe('AskRex')
    expect(provenance.artwork).toContain('raptor')
    expect(provenance.windowsIcon).toBe(packageJson.build.win.icon.replace('src/assets/', ''))
    expect(sha256(executableIcon)).toBe(provenance.windowsIconSha256)
    expect(sizes).toEqual(expectedWindowsIconSizes)
  })

  it('ships the same high-resolution icon for the installed window', () => {
    const executableIcon = resolve(guiRoot, 'src/assets/icon.ico')
    const installedWindowIcon = resolve(repoRoot, 'assets/brand/icon.ico')
    const windowSource = readFileSync(resolve(guiRoot, 'src/main/window.ts'), 'utf8')

    expect(existsSync(installedWindowIcon)).toBe(true)
    const provenance = JSON.parse(
      readFileSync(resolve(guiRoot, 'src/assets/icon-provenance.json'), 'utf8')
    ) as IconProvenance

    expect(sha256(installedWindowIcon)).toBe(provenance.windowsIconSha256)
    expect(sha256(executableIcon)).toBe(provenance.windowsIconSha256)
    expect(sha256(resolve(guiRoot, 'src/assets', provenance.canonicalArtwork))).toBe(
      provenance.canonicalArtworkSha256
    )
    expect(windowSource).toContain("join(process.resourcesPath, 'assets', 'brand', 'icon.ico')")
  })

  it('renders the AskRex raptor artwork in the largest Windows icon frame', () => {
    const icon = readFileSync(resolve(guiRoot, 'src/assets/icon.ico'))

    // These points are deliberately taken from the white raptor head, blue R, and navy field
    // in the approved canonical artwork. They make an accidental generic/blank ICO detectable.
    expect(renderedIcoPixel(icon, 256, 128, 128)).toEqual([254, 254, 254, 255])
    expect(renderedIcoPixel(icon, 256, 80, 190)).toEqual([78, 153, 192, 255])
    expect(renderedIcoPixel(icon, 256, 200, 200)).toEqual([22, 33, 76, 255])
  })

  it('uses the installed app identity for taskbar and Start-menu grouping', () => {
    const packageJson = JSON.parse(
      readFileSync(resolve(guiRoot, 'package.json'), 'utf8')
    ) as { build: { appId: string } }
    const mainSource = readFileSync(resolve(guiRoot, 'src/main/index.ts'), 'utf8')

    expect(packageJson.build.appId).toBe('com.askrex.app')
    expect(mainSource).toContain("electronApp.setAppUserModelId('com.askrex.app')")
  })
})

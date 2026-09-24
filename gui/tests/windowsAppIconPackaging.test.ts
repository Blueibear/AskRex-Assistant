import { existsSync, readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
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

  it('uses the installed app identity for taskbar and Start-menu grouping', () => {
    const packageJson = JSON.parse(
      readFileSync(resolve(guiRoot, 'package.json'), 'utf8')
    ) as { build: { appId: string } }
    const mainSource = readFileSync(resolve(guiRoot, 'src/main/index.ts'), 'utf8')

    expect(packageJson.build.appId).toBe('com.askrex.app')
    expect(mainSource).toContain("electronApp.setAppUserModelId('com.askrex.app')")
  })
})

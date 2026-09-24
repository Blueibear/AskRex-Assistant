import { existsSync, readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const repoRoot = resolve(import.meta.dirname, '..', '..')
const guiRoot = resolve(repoRoot, 'gui')

describe('Windows application icon packaging', () => {
  it('uses the shipped high-resolution AskRex ICO for the Windows executable and installer', () => {
    const packageJson = JSON.parse(
      readFileSync(resolve(guiRoot, 'package.json'), 'utf8')
    ) as { build: { win: { icon: string } } }

    expect(packageJson.build.win.icon).toBe('src/assets/icon.ico')
    const executableIcon = resolve(guiRoot, packageJson.build.win.icon)
    expect(existsSync(executableIcon)).toBe(true)

    const iconBytes = readFileSync(executableIcon)
    const entryCount = iconBytes.readUInt16LE(4)
    const sizes = Array.from({ length: entryCount }, (_, index) => {
      const offset = 6 + index * 16
      return {
        width: iconBytes[offset] === 0 ? 256 : iconBytes[offset],
        height: iconBytes[offset + 1] === 0 ? 256 : iconBytes[offset + 1]
      }
    })

    expect(sizes).toContainEqual({ width: 256, height: 256 })
  })

  it('ships the same high-resolution icon for the installed window', () => {
    const executableIcon = resolve(guiRoot, 'src/assets/icon.ico')
    const installedWindowIcon = resolve(repoRoot, 'assets/brand/icon.ico')
    const windowSource = readFileSync(resolve(guiRoot, 'src/main/window.ts'), 'utf8')

    expect(existsSync(installedWindowIcon)).toBe(true)
    expect(createHash('sha256').update(readFileSync(installedWindowIcon)).digest('hex')).toBe(
      createHash('sha256').update(readFileSync(executableIcon)).digest('hex')
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

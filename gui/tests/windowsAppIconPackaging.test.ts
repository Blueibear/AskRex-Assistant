import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const repoRoot = resolve(import.meta.dirname, '..', '..')
const guiRoot = resolve(repoRoot, 'gui')

describe('Windows application icon packaging', () => {
  it('uses the shipped AskRex ICO for the Windows executable and installer', () => {
    const packageJson = JSON.parse(
      readFileSync(resolve(guiRoot, 'package.json'), 'utf8')
    ) as { build: { win: { icon: string } } }

    expect(packageJson.build.win.icon).toBe('src/assets/icon.ico')
    expect(existsSync(resolve(guiRoot, packageJson.build.win.icon))).toBe(true)
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

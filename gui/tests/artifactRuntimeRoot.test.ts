import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const repoRoot = resolve(import.meta.dirname, '..', '..')

describe('installed artifact runtime isolation', () => {
  it('binds Electron userData to the smoke runtime root only in artifact mode', () => {
    const source = readFileSync(resolve(repoRoot, 'gui/src/main/index.ts'), 'utf8')
    expect(source).toContain("process.env['ASKREX_ARTIFACT_SMOKE_RUNTIME_ROOT']")
    expect(source).toContain("process.env['ASKREX_ARTIFACT_SMOKE'] === '1'")
    expect(source).toContain("app.setPath('userData', artifactSmokeRuntimeRoot)")
  })

  it('bootstraps setup and Electron against the same isolated runtime root', () => {
    const source = readFileSync(resolve(repoRoot, 'scripts/test_installed_electron_artifact.ps1'), 'utf8')
    for (const expected of [
      "$appData = Join-Path $testRoot 'AppData'",
      "$runtimeRoot = Join-Path $appData 'rex-gui'",
      '$env:APPDATA = $appData',
      '$env:ASKREX_RUNTIME_DIR = $runtimeRoot',
      'create_user(',
      'bootstrap_admin_if_first_user(',
      '$env:ASKREX_ARTIFACT_SMOKE_RUNTIME_ROOT = $runtimeRoot',
      'Remove-Item Env:ASKREX_ARTIFACT_SMOKE_RUNTIME_ROOT',
      'Remove-Item Env:ASKREX_RUNTIME_DIR'
    ]) {
      expect(source).toContain(expected)
    }
  })
})

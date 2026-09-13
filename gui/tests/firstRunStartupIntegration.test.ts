import { readFileSync } from 'fs'
import { fileURLToPath } from 'url'
import { describe, expect, it } from 'vitest'

const mainSource = readFileSync(
  fileURLToPath(new URL('../src/main/index.ts', import.meta.url)),
  'utf8'
)
const ipcSource = readFileSync(
  fileURLToPath(new URL('../src/main/ipc.ts', import.meta.url)),
  'utf8'
)
const setupSource = readFileSync(
  fileURLToPath(new URL('../src/main/handlers/setup.ts', import.meta.url)),
  'utf8'
)
const setupPreviewSource = readFileSync(
  fileURLToPath(new URL('../src/main/handlers/setupPreview.ts', import.meta.url)),
  'utf8'
)
const appSource = readFileSync(
  fileURLToPath(new URL('../src/renderer/src/App.tsx', import.meta.url)),
  'utf8'
)

describe('US-125 packaged first-run bootstrap', () => {
  it('checks setup state before requiring an authenticated Electron identity', () => {
    expect(mainSource).toContain('readSetupStatus')
    expect(mainSource).toContain('planElectronStartup')
    expect(mainSource.indexOf('readSetupStatus')).toBeLessThan(
      mainSource.indexOf('resolveElectronSessionIdentity')
    )
    expect(mainSource).toContain("startupPlan.mode === 'setup'")
  })

  it('registers setup IPC independently and authenticated IPC only after identity exists', () => {
    expect(ipcSource).toContain('registerAuthenticatedIpcHandlers')
    expect(ipcSource).not.toContain('registerSetupHandlers()')
    expect(mainSource).toContain('registerSetupHandlers')
    expect(mainSource).toContain('registerAuthenticatedIpcHandlers')
  })

  it('keeps read-only voice inventory, preview, and selected wake readiness usable before identity exists', () => {
    expect(setupPreviewSource).toContain('registerSetupPreviewHandlers')
    expect(setupPreviewSource).toContain('unregisterSetupPreviewHandlers')
    for (const channel of [
      "'rex:listVoices'",
      "'rex:previewVoice'",
      "'rex:listWakeWords'",
      "'rex:previewWakeWordSample'",
      "'rex:getSetupWakeWordStatus'"
    ]) {
      expect(setupPreviewSource).toContain(channel)
    }
    expect(mainSource).toContain('registerSetupPreviewHandlers')
    expect(mainSource).toContain('unregisterSetupPreviewHandlers')
  })

  it('bootstraps the authenticated runtime immediately after setup completes', () => {
    expect(setupSource).toContain('onSetupCompleted')
    expect(setupSource).toContain('await onSetupCompleted()')
    expect(mainSource).toContain('bootstrapAuthenticatedRuntime')
  })

  it('reconciles persisted disabled background voice on authenticated startup', () => {
    expect(mainSource).toContain('applyBackgroundVoicePreference')
    expect(mainSource).toContain('applyBackgroundVoicePreference(sessionIdentity, false)')
    expect(mainSource).toContain('background_runtime_disable_reconcile_failed')
  })

  it('does not probe authenticated backend status while the setup decision is unresolved', () => {
    expect(appSource).toContain('if (needsSetup !== false) return')
    expect(appSource).toContain('.getStatus()')
  })
})

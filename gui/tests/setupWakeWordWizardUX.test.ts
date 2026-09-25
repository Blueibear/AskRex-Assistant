import { readFileSync } from 'fs'
import { fileURLToPath } from 'url'
import { describe, expect, it } from 'vitest'

function source(relativePath: string): string {
  return readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8')
}

const pageSource = source('../src/pages/SetupWizardPage.tsx')
const preloadSource = source('../src/preload/index.ts')
const ipcSource = source('../src/types/ipc.ts')

describe('US-125 setup wake-word UX', () => {
  it('loads the canonical wake-word inventory', () => {
    expect(pageSource).toContain('window.rex.listWakeWords()')
    expect(pageSource).toContain('wakeWords')
    expect(pageSource).toContain('wake_words')
  })

  it('uses a named wake-word selector instead of a raw id field', () => {
    expect(pageSource).toContain('Choose a wake word')
    expect(pageSource).toContain('wakeWords.map((wakeWord)')
    expect(pageSource).toContain('wakeWord.name')
    expect(pageSource).not.toContain('placeholder="hey_rex"')
  })

  it('previews a real wake-word sample without claiming detection', () => {
    expect(pageSource).toContain('window.rex.previewWakeWordSample(data.wakeWordId)')
    expect(pageSource).toContain("selectedWakeWord?.has_sample === true")
    expect(pageSource).toContain('No preview recording is available for this wake word.')
    expect(pageSource).toContain('Wake-word sample played')
    expect(pageSource).toContain('Actual wake detection is still required')
  })

  it('does not silently replace the default Hey Rex selection', () => {
    expect(pageSource).toContain("wakeWord.id === 'hey_rex'")
    expect(pageSource).toContain('The default Hey Rex wake-word asset is not installed.')
    expect(pageSource).toContain("return { ...previous, wakeWordId: '' }")
    expect(pageSource).not.toContain("wakeWordId: available[0]?.id ?? ''")
  })

  it('checks canonical readiness for the currently selected setup wake word', () => {
    expect(pageSource).toContain('window.rex.getSetupWakeWordStatus(data.wakeWordId)')
    expect(pageSource).toContain('[data.wakeWordId, wakeWordsLoading]')
    expect(preloadSource).toContain(
      'getSetupWakeWordStatus: (wakeWordId: string): Promise<WakeWordStatus>'
    )
    expect(preloadSource).toContain(
      "ipcRenderer.invoke('rex:getSetupWakeWordStatus', wakeWordId)"
    )
    expect(ipcSource).toContain(
      'getSetupWakeWordStatus: (wakeWordId: string) => Promise<WakeWordStatus>'
    )
    expect(pageSource).toContain('Wake-word asset ready')
    expect(pageSource).toContain('wakeWordStatusError')
    expect(pageSource).toContain('Voice not yet verified')
  })
})

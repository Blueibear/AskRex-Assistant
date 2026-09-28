import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(resolve(__dirname, '../src/main/handlers/setupPreview.ts'), 'utf8')

describe('US-125 pre-auth setup preview boundary', () => {
  it('does not let an unauthenticated renderer override wake-word asset paths', () => {
    expect(source).not.toContain("'rex:getWakeWordStatus'")
    expect(source).not.toContain('const source = values ??')
    expect(source).toContain("'rex:getSetupWakeWordStatus'")
  })

  it('checks readiness only for wake-word IDs AskRex enumerated for setup', () => {
    expect(source).toContain('const setupWakeWordInventory = new Map<string, WakeWordInfo>()')
    expect(source).toContain('setupWakeWordInventory.set(')
    expect(source).toContain('const selectedWakeWord = setupWakeWordInventory.get(wakeWordId)')
    expect(source).toContain(
      'Choose a wake word from the available setup list before checking it.'
    )
  })

  it('only previews voice IDs that AskRex enumerated for setup', () => {
    expect(source).toContain('const setupVoiceInventory = new Map<string, Set<string>>()')
    expect(source).toContain('setupVoiceInventory.set(')
    expect(source).toContain('allowedVoiceIds?.has(voiceId)')
    expect(source).toContain('Choose a voice from the available setup list before previewing it.')
  })

  it('does not invoke the sample bridge for wake words without a declared sample', () => {
    expect(source).toContain("ipcMain.handle('rex:previewWakeWordSample'")
    expect(source).toContain('const selectedWakeWord = setupWakeWordInventory.get(wakeWordId)')
    expect(source).toContain('selectedWakeWord.has_sample !== true')
    expect(source).toContain('No wake-word sample is available for this choice.')
  })
})

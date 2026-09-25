import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'


describe('wake-word diagnostic IPC', () => {
  it('wires typed runtime status and attempt evidence through Electron', () => {
    const handler = readFileSync(join(__dirname, '../src/main/handlers/voice.ts'), 'utf8')
    const preload = readFileSync(join(__dirname, '../src/preload/index.ts'), 'utf8')
    const types = readFileSync(join(__dirname, '../src/types/ipc.ts'), 'utf8')
    const page = readFileSync(join(__dirname, '../src/pages/VoicePage.tsx'), 'utf8')

    expect(handler).toContain("'rex:wakeWordRuntimeStatus'")
    expect(handler).toContain("'rex:wakeWordAttemptEvidence'")
    expect(preload).toContain("ipcRenderer.on('rex:wakeWordRuntimeStatus'")
    expect(preload).toContain("ipcRenderer.on('rex:wakeWordAttemptEvidence'")
    expect(types).toContain('export interface WakeWordRuntimeStatus')
    expect(types).toContain('export interface WakeWordAttemptEvidence')
    // Snapshots survive VoicePage unmount/remount via the trusted main process.
    expect(handler).toContain("'rex:getWakeWordRuntimeSnapshots'")
    expect(handler).toContain('latestWakeWordRuntimeStatus')
    expect(handler).toContain('clearWakeWordDiagnosticSnapshots()')
    expect(preload).toContain("ipcRenderer.invoke('rex:getWakeWordRuntimeSnapshots')")
    expect(types).toContain('getWakeWordRuntimeSnapshots')
    expect(page).toContain('getWakeWordRuntimeSnapshots()')
    expect(page).toContain('snapshotRuntimeRevision')
    expect(page).toContain('shouldRestoreWakeWordRuntimeSnapshot')
    expect(page).toContain('Say:')
    expect(page).toContain('Wake-word diagnostics')
    expect(page).toContain('reliable immediate path')
  })
})

describe('wake-word failure state', () => {
  it('keeps a typed visible error state when wake startup fails', () => {
    const toggle = readFileSync(join(__dirname, '../src/components/voice/VoiceToggle.tsx'), 'utf8')
    const page = readFileSync(join(__dirname, '../src/pages/VoicePage.tsx'), 'utf8')

    expect(toggle).toContain("| 'error'")
    expect(toggle).toContain("error: 'Voice failed'")
    expect(page).toContain("setVoiceState('error')")
  })
})

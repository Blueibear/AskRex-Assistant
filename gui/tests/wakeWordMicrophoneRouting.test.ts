import { describe, expect, it } from 'vitest'
import { readFileSync } from 'fs'
import { join } from 'path'

describe('wake-word microphone routing', () => {
  it('propagates the selected microphone from the Voice page to the Python bridge', () => {
    const page = readFileSync(join(__dirname, '../src/pages/VoicePage.tsx'), 'utf8')
    const preload = readFileSync(join(__dirname, '../src/preload/index.ts'), 'utf8')
    const handler = readFileSync(join(__dirname, '../src/main/handlers/voice.ts'), 'utf8')
    const bridge = readFileSync(join(__dirname, '../../bridge/rex_voice_bridge.py'), 'utf8')

    expect(page).toContain('microphoneLabel')
    expect(page).toContain('selectedMicId')
    expect(preload).toContain(".invoke('rex:startVoice', options ?? {})")
    expect(handler).toContain("bridgeArgs.push('--microphone-label', microphoneLabel)")
    expect(handler).toContain("? ['-I', scriptPath, '--user', session.userId]")
    expect(bridge).toContain('_configured_input_device_index(active_settings)')
    expect(bridge).toContain('resolve_input_device_index_by_name(microphone_label)')
    expect(bridge).toContain('device_index=microphone_device_index')
  })
})

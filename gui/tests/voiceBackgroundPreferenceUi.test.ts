import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

const section = readFileSync(
  new URL('../src/pages/settings/voice/VoiceSettingsSection.tsx', import.meta.url),
  'utf8',
)
const controller = readFileSync(
  new URL('../src/pages/settings/voice/useVoiceSettingsController.ts', import.meta.url),
  'utf8',
)
const audioSelectors = readFileSync(
  new URL('../src/pages/settings/voice/RuntimeAudioDeviceSelectors.tsx', import.meta.url),
  'utf8',
)

describe('background voice preference UI', () => {
  it('keeps auto-start consent in Voice settings with explicit privacy copy', () => {
    expect(section).toContain('Start wake-word listening automatically when I sign in')
    expect(section).toContain("handleFieldChange('backgroundVoiceEnabled'")
    expect(section).toContain('You can pause listening at any time from the tray')
  })

  it('loads and saves backgroundVoiceEnabled through the canonical voice settings form', () => {
    expect(controller).toContain('backgroundVoiceEnabled: false')
    expect(controller).toContain('backgroundVoiceEnabled: settings.backgroundVoiceEnabled === true')
  })

  it('retains an explicit browser capture microphone for wake-word training and enrollment', () => {
    expect(section).toContain('Browser capture microphone')
    expect(section).toContain("handleFieldChange('microphoneDeviceId'")
    expect(section).toContain('System default (browser capture)')
    expect(controller).toContain('audio: form.microphoneDeviceId')
  })

  it('uses the packaged Python audio inventory and canonical device indices for recovery', () => {
    expect(controller).toContain('window.rex.getSetupAudioDevices()')
    expect(controller).toContain('microphoneDeviceIndex: null')
    expect(controller).toContain('speakerDeviceIndex: null')
    expect(section).toContain('RuntimeAudioDeviceSelectors')
    expect(audioSelectors).toContain("'microphoneDeviceIndex'")
    expect(audioSelectors).toContain("'speakerDeviceIndex'")
    expect(audioSelectors).toContain('max_input_channels')
    expect(audioSelectors).toContain('max_output_channels')
  })
})

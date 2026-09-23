import { readFileSync } from 'fs'
import { fileURLToPath } from 'url'
import { describe, expect, it } from 'vitest'

const pageSource = readFileSync(
  fileURLToPath(new URL('../src/pages/SetupWizardPage.tsx', import.meta.url)),
  'utf8'
)

describe('US-125 setup audio wizard UX', () => {
  it('loads canonical PortAudio inventory through the typed setup bridge', () => {
    expect(pageSource).toContain('window.rex.getSetupAudioDevices()')
    expect(pageSource).toContain('max_input_channels > 0')
    expect(pageSource).toContain('max_output_channels > 0')
  })

  it('uses named device selectors instead of numeric PortAudio index entry', () => {
    expect(pageSource).toContain('Choose a microphone')
    expect(pageSource).toContain('Choose a speaker')
    expect(pageSource).toContain('device.name')
    expect(pageSource).not.toContain('type="number"')
  })

  it('uses the grouped physical microphone inventory while leaving speaker compatibility intact', () => {
    expect(pageSource).toMatch(/result\.microphones\s*\?\?\s*result\.devices\.filter/)
    expect(pageSource).toContain('const speakerDevices = audioDevices.filter')
    expect(pageSource).toMatch(/setMicrophoneDevices\(\s*result\.microphones/)
  })

  it('explains that Windows host-API duplicates are labeled without changing the runtime choice', () => {
    expect(pageSource).toContain(
      'Entries sharing a name are labeled with their Windows audio API so each stays a distinct'
    )
    expect(pageSource).toContain('keeps the tested voice-runtime device.')
  })

  it('starts first-run verification from the persisted canonical microphone index, not a display-name remap', () => {
    expect(pageSource).not.toContain('microphoneLabel ? { microphoneLabel } : undefined')
  })

  it('runs non-persisting microphone and speaker functional tests', () => {
    expect(pageSource).toContain("window.rex.testSetupAudioDevice('microphone', data.microphoneDeviceIndex)")
    expect(pageSource).toContain("window.rex.testSetupAudioDevice('speaker', data.speakerDeviceIndex)")
    expect(pageSource).toContain('Test microphone')
    expect(pageSource).toContain('Test speaker')
  })

  it('shows stage-specific success and failure without claiming full voice verification', () => {
    expect(pageSource).toContain('Microphone test passed')
    expect(pageSource).toContain('Speaker test passed')
    expect(pageSource).toContain('microphoneTestError')
    expect(pageSource).toContain('speakerTestError')
    expect(pageSource).toContain('Voice not yet verified')
  })
})

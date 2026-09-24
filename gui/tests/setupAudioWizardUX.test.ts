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

  it('uses the grouped physical microphone and speaker inventory instead of a raw name-only list', () => {
    expect(pageSource).toMatch(/result\.microphones\s*\?\?\s*result\.devices\.filter/)
    expect(pageSource).toMatch(/result\.speakers\s*\?\?\s*result\.devices\.filter/)
    expect(pageSource).toMatch(/setMicrophoneDevices\(\s*result\.microphones/)
    expect(pageSource).toMatch(/setSpeakerDevices\(\s*result\.speakers/)
  })

  it('explains that MME shortened-name hints remain distinct runtime choices', () => {
    const duplicateLabelOccurrences = pageSource.match(
      /Windows MME names can show a full-name hint when their shortened name has one possible/g
    )
    expect(duplicateLabelOccurrences).toHaveLength(1)
    expect(pageSource).toContain('individually selectable microphone')
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

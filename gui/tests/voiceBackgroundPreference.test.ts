import { beforeEach, describe, expect, it, vi } from 'vitest'

const { mockReadRexConfig } = vi.hoisted(() => ({
  mockReadRexConfig: vi.fn(),
}))

vi.mock('../src/main/configStore', () => ({
  readRexConfig: mockReadRexConfig,
  getConfigDir: () => 'C:\\AskRex\\config',
}))

import { buildVoiceSettings } from '../src/main/voiceSettings'

describe('background voice preference authority', () => {
  beforeEach(() => {
    mockReadRexConfig.mockReset()
  })

  it('reads setup background voice consent from canonical runtime config', () => {
    mockReadRexConfig.mockReturnValue({
      runtime: { background_voice_enabled: true },
    })

    expect(buildVoiceSettings({}).backgroundVoiceEnabled).toBe(true)
  })

  it('treats missing or non-boolean runtime consent as disabled', () => {
    mockReadRexConfig.mockReturnValue({ runtime: { background_voice_enabled: 'yes' } })
    expect(buildVoiceSettings({}).backgroundVoiceEnabled).toBe(false)
  })

  it('reads canonical Python audio device indices for replacement-device recovery', () => {
    mockReadRexConfig.mockReturnValue({
      audio: { input_device_index: 4, output_device_index: 7 },
    })

    const voice = buildVoiceSettings({})
    expect(voice.microphoneDeviceIndex).toBe(4)
    expect(voice.speakerDeviceIndex).toBe(7)
  })
})

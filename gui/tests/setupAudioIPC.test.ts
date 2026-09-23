import { readFileSync } from 'fs'
import { fileURLToPath } from 'url'
import { describe, expect, it } from 'vitest'

function source(relativePath: string): string {
  return readFileSync(fileURLToPath(new URL(relativePath, import.meta.url)), 'utf8')
}

const handlerSource = source('../src/main/handlers/setup.ts')
const preloadSource = source('../src/preload/index.ts')
const ipcSource = source('../src/types/ipc.ts')

describe('US-125 typed setup audio IPC boundary', () => {
  it('registers setup-only audio inventory and functional test handlers', () => {
    expect(handlerSource).toContain("'rex:getSetupAudioDevices'")
    expect(handlerSource).toContain("command: 'audio_devices'")
    expect(handlerSource).toContain("'rex:testSetupAudioDevice'")
    expect(handlerSource).toContain("command: 'test_audio_device'")
  })

  it('exposes only typed setup audio methods through the preload bridge', () => {
    expect(preloadSource).toContain('getSetupAudioDevices: (): Promise<SetupAudioDevicesResponse>')
    expect(preloadSource).toContain("ipcRenderer.invoke('rex:getSetupAudioDevices')")
    expect(preloadSource).toContain('testSetupAudioDevice: (')
    expect(preloadSource).toContain("kind: 'microphone' | 'speaker'")
    expect(preloadSource).toContain("ipcRenderer.invoke('rex:testSetupAudioDevice', kind, deviceIndex)")
  })

  it('defines a sanitized PortAudio inventory contract in RexAPI', () => {
    for (const declaration of [
      'export interface SetupAudioDevice',
      'index: number',
      'name: string',
      'max_input_channels: number',
      'max_output_channels: number',
      'host_api?: string',
      'alias_count?: number',
      'export interface SetupAudioDevicesResponse',
      'devices: SetupAudioDevice[]',
      'microphones?: SetupAudioDevice[]',
      'export interface SetupAudioTestResponse',
      'getSetupAudioDevices: () => Promise<SetupAudioDevicesResponse>',
      "testSetupAudioDevice: (kind: 'microphone' | 'speaker', deviceIndex: number) => Promise<SetupAudioTestResponse>"
    ]) {
      expect(ipcSource).toContain(declaration)
    }
  })
})

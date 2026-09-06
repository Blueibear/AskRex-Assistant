import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ElectronSessionIdentity } from '../src/main/sessionIdentity'

const { mockApp } = vi.hoisted(() => ({
  mockApp: {
    isPackaged: false,
    getAppPath: vi.fn().mockReturnValue('/fake/app'),
    getPath: vi.fn().mockReturnValue('/fake/user-data')
  }
}))
vi.mock('electron', () => ({ app: mockApp }))

const { mockConfigStore } = vi.hoisted(() => ({
  mockConfigStore: {
    readGuiSettings: vi.fn(),
    readRexConfig: vi.fn(),
    readRexConfigStrict: vi.fn(),
    writeGuiSettings: vi.fn(),
    writeRexConfig: vi.fn()
  }
}))
vi.mock('../src/main/configStore', () => mockConfigStore)

const { mockMirror } = vi.hoisted(() => ({
  mockMirror: { mirrorToRexConfig: vi.fn() }
}))
vi.mock('../src/main/settingsMirror', () => mockMirror)

const { mockVault } = vi.hoisted(() => ({
  mockVault: {
    vaultSetSecret: vi.fn(),
    vaultGetSecret: vi.fn(),
    vaultDeleteSecret: vi.fn()
  }
}))
vi.mock('../src/main/credentialVault', () => mockVault)

import { readSavedHomeAssistantCredentials, saveHomeAssistantCredentials } from '../src/main/homeAssistant'

const session: ElectronSessionIdentity = {
  userId: 'alice', sessionId: 'session-1', authentication: 'local-os-session'
}
const ref = `cred_${'H'.repeat(32)}`
const context = { scope: 'household', integration: 'home_assistant', account: null, slot: 'token' }
let guiSettings: Record<string, unknown>
let rexConfig: Record<string, unknown>

describe('Home Assistant credentials are vault-backed (S4)', () => {
  beforeEach(() => {
    guiSettings = {}
    rexConfig = {}
    mockConfigStore.readGuiSettings.mockReset().mockImplementation(() => guiSettings)
    mockConfigStore.readRexConfig.mockReset().mockImplementation(() => rexConfig)
    mockConfigStore.readRexConfigStrict.mockReset().mockImplementation(() => rexConfig)
    mockConfigStore.writeGuiSettings.mockReset().mockImplementation((value) => { guiSettings = value })
    mockConfigStore.writeRexConfig.mockReset().mockImplementation((value) => { rexConfig = value })
    mockMirror.mirrorToRexConfig.mockReset().mockReturnValue({ ok: true })
    mockVault.vaultSetSecret.mockReset().mockResolvedValue(ref)
    mockVault.vaultGetSecret.mockReset().mockResolvedValue(null)
    mockVault.vaultDeleteSecret.mockReset().mockResolvedValue(true)
  })

  it('writes the token to the vault and persists only its contextual opaque reference', async () => {
    await saveHomeAssistantCredentials(session, 'http://ha.local:8123', 'ha-token-value')

    expect(mockVault.vaultSetSecret).toHaveBeenCalledWith(session, 'ha-token-value', context)
    expect(JSON.stringify(guiSettings)).not.toContain('ha-token-value')
    expect(rexConfig).toMatchObject({
      credential_refs: {
        household: {
          HA_TOKEN: { ref, integration: 'home_assistant', account: null, slot: 'token' }
        }
      }
    })
  })

  it('rolls back config and GUI state when vault or mirror persistence fails', async () => {
    mockVault.vaultSetSecret.mockRejectedValueOnce(new Error('vault unavailable'))
    await expect(saveHomeAssistantCredentials(session, 'http://ha.local:8123', 'secret')).rejects.toThrow(
      'vault unavailable'
    )
    expect(rexConfig).toEqual({})
    expect(guiSettings).toEqual({})

    mockVault.vaultSetSecret.mockResolvedValueOnce(ref)
    mockMirror.mirrorToRexConfig.mockReturnValueOnce({ ok: false, error: 'mirror failed' })
    await expect(saveHomeAssistantCredentials(session, 'http://ha.local:8123', 'secret')).rejects.toThrow(
      'mirror failed'
    )
    expect(rexConfig).toEqual({})
    expect(guiSettings).toEqual({})
    expect(mockVault.vaultDeleteSecret).toHaveBeenCalledWith(session, ref, context)
  })

  it('treats blank token input as unchanged', async () => {
    await saveHomeAssistantCredentials(session, 'http://ha.local:8123', '')
    expect(mockVault.vaultSetSecret).not.toHaveBeenCalled()
  })

  it('reads only an exact saved reference and never preloads the token into settings', async () => {
    rexConfig = {
      home_assistant: { base_url: 'http://ha.local:8123' },
      credential_refs: { household: { HA_TOKEN: { ref, integration: 'home_assistant', account: null, slot: 'token' } } }
    }
    mockVault.vaultGetSecret.mockResolvedValue('ha-vault-token')
    const result = await readSavedHomeAssistantCredentials(session)
    expect(result).toEqual({ baseUrl: 'http://ha.local:8123', token: 'ha-vault-token', ref })
    expect(mockVault.vaultGetSecret).toHaveBeenCalledWith(session, ref, context)
  })

  it('fails closed when reference metadata is swapped', async () => {
    rexConfig = {
      credential_refs: { household: { HA_TOKEN: { ref, integration: 'openai', account: null, slot: 'api_key' } } }
    }
    await expect(readSavedHomeAssistantCredentials(session)).rejects.toThrow(/context/i)
    expect(mockVault.vaultGetSecret).not.toHaveBeenCalled()
  })
})

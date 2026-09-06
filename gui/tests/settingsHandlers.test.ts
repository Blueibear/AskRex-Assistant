import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ElectronSessionIdentity } from '../src/main/sessionIdentity'

const { mockApp } = vi.hoisted(() => ({
  mockApp: {
    isPackaged: false,
    getAppPath: vi.fn().mockReturnValue('/fake/app'),
    getPath: vi.fn().mockReturnValue('/fake/user-data')
  }
}))

const { registeredHandlers, mockHandle } = vi.hoisted(() => {
  const registeredHandlers = new Map<string, (...args: unknown[]) => unknown>()
  const mockHandle = vi.fn((channel: string, fn: (...args: unknown[]) => unknown) => {
    registeredHandlers.set(channel, fn)
  })
  return { registeredHandlers, mockHandle }
})
vi.mock('electron', () => ({ app: mockApp, ipcMain: { handle: mockHandle } }))

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

const { mockMirror } = vi.hoisted(() => ({ mockMirror: { mirrorToRexConfig: vi.fn() } }))
vi.mock('../src/main/settingsMirror', () => mockMirror)

const { mockIntegrationStatus } = vi.hoisted(() => ({
  mockIntegrationStatus: { reconcileIntegrationStatuses: vi.fn() }
}))
vi.mock('../src/main/integrationStatus', () => mockIntegrationStatus)

const { mockModelDiscovery } = vi.hoisted(() => ({
  mockModelDiscovery: { discoverAiModelsAtEndpoint: vi.fn() }
}))
vi.mock('../src/main/modelDiscovery', () => mockModelDiscovery)

const { mockVault } = vi.hoisted(() => ({
  mockVault: {
    vaultSetSecret: vi.fn(),
    vaultHasSecret: vi.fn(),
    vaultDeleteSecret: vi.fn()
  }
}))
vi.mock('../src/main/credentialVault', () => mockVault)

import { registerSettingsHandlers } from '../src/main/handlers/settings'

const session: ElectronSessionIdentity = {
  userId: 'alice', sessionId: 'session-1', authentication: 'local-os-session'
}
const ref = `cred_${'S'.repeat(32)}`
const openAiContext = { scope: 'household', integration: 'openai', account: null, slot: 'api_key' }
const openRouterContext = { scope: 'household', integration: 'openrouter', account: null, slot: 'api_key' }
const haContext = { scope: 'household', integration: 'home_assistant', account: null, slot: 'token' }
const openClawContext = { scope: 'household', integration: 'openclaw_gateway', account: null, slot: 'token' }
const emailContext = { scope: 'user', integration: 'email', account: 'work', slot: 'password' }
let guiSettings: Record<string, unknown>
let rexConfig: Record<string, unknown>

async function invoke(channel: string, ...args: unknown[]): Promise<unknown> {
  const handler = registeredHandlers.get(channel)
  if (!handler) throw new Error(`No handler registered for ${channel}`)
  return handler(null, ...args)
}

describe('settings vault routing (S4)', () => {
  beforeEach(() => {
    registeredHandlers.clear()
    guiSettings = {}
    rexConfig = {}
    mockConfigStore.readGuiSettings.mockReset().mockImplementation(() => guiSettings)
    mockConfigStore.readRexConfig.mockReset().mockImplementation(() => rexConfig)
    mockConfigStore.readRexConfigStrict.mockReset().mockImplementation(() => rexConfig)
    mockConfigStore.writeGuiSettings.mockReset().mockImplementation((value) => { guiSettings = value })
    mockConfigStore.writeRexConfig.mockReset().mockImplementation((value) => { rexConfig = value })
    mockMirror.mirrorToRexConfig.mockReset().mockReturnValue({ ok: true })
    mockIntegrationStatus.reconcileIntegrationStatuses.mockReset().mockResolvedValue(undefined)
    mockModelDiscovery.discoverAiModelsAtEndpoint.mockReset().mockResolvedValue({
      ok: true,
      models: []
    })
    mockVault.vaultSetSecret.mockReset().mockResolvedValue(ref)
    mockVault.vaultHasSecret.mockReset().mockResolvedValue(false)
    mockVault.vaultDeleteSecret.mockReset().mockResolvedValue(true)
    registerSettingsHandlers(session)
  })

  it('preserves an explicit AI autonomy change through save normalization', async () => {
    rexConfig = { models: { autonomy_mode: 'manual' } }

    const result = await invoke('rex:setSettings', 'ai', { autonomyMode: 'full-auto' })

    expect(result).toEqual({ ok: true })
    expect(mockMirror.mirrorToRexConfig).toHaveBeenCalledWith(
      'ai', expect.objectContaining({ autonomyMode: 'full-auto' })
    )
    expect(guiSettings).toMatchObject({ ai: expect.not.objectContaining({ autonomyMode: expect.anything() }) })
  })

  it('writes an API key to the vault and persists only a contextual reference', async () => {
    const result = await invoke('rex:setApiKey', 'OPENAI_API_KEY', 'sk-value')
    expect(result).toEqual({ ok: true })
    expect(mockVault.vaultSetSecret).toHaveBeenCalledWith(session, 'sk-value', openAiContext)
    expect(JSON.stringify(rexConfig)).not.toContain('sk-value')
    expect(rexConfig).toMatchObject({
      credential_refs: { household: { OPENAI_API_KEY: { ref, integration: 'openai', account: null, slot: 'api_key' } } }
    })
  })

  it('stores OpenRouter credentials under a separate vault context', async () => {
    const result = await invoke('rex:setApiKey', 'OPENROUTER_API_KEY', 'router-value')
    expect(result).toEqual({ ok: true })
    expect(mockVault.vaultSetSecret).toHaveBeenCalledWith(session, 'router-value', openRouterContext)
    expect(JSON.stringify(rexConfig)).not.toContain('router-value')
    expect(rexConfig).toMatchObject({
      credential_refs: { household: { OPENROUTER_API_KEY: { ref, integration: 'openrouter', account: null, slot: 'api_key' } } }
    })
  })

  it('does not treat blank input as deletion and rejects arbitrary key names', async () => {
    await expect(invoke('rex:setApiKey', 'OPENAI_API_KEY', '  ')).resolves.toEqual({ ok: true })
    expect(mockVault.vaultSetSecret).not.toHaveBeenCalled()

    await expect(invoke('rex:setApiKey', 'NOT_ALLOWED', 'x')).resolves.toEqual({
      ok: false, error: 'Key "NOT_ALLOWED" is not allowed'
    })
    expect(mockVault.vaultSetSecret).not.toHaveBeenCalled()
  })

  it('rolls back the API reference and deletes the staged secret on readback failure', async () => {
    mockConfigStore.readRexConfigStrict
      .mockReturnValueOnce({})
      .mockReturnValueOnce({})
    const result = await invoke('rex:setApiKey', 'OPENAI_API_KEY', 'sk-value')
    expect(result).toEqual({ ok: false, error: 'Credential reference readback failed' })
    expect(rexConfig).toEqual({})
    expect(mockVault.vaultDeleteSecret).toHaveBeenCalledWith(session, ref, openAiContext)
  })

  it('reports API-key status only when both an exact reference and vault entry exist', async () => {
    rexConfig = {
      credential_refs: { household: { OPENAI_API_KEY: { ref, integration: 'openai', account: null, slot: 'api_key' } } }
    }
    mockVault.vaultHasSecret.mockResolvedValue(true)
    await expect(invoke('rex:getApiKeys')).resolves.toEqual({ openai_key_set: true, openrouter_key_set: false })
    expect(mockVault.vaultHasSecret).toHaveBeenCalledWith(session, ref, openAiContext)
  })

  it('fails closed and reports swapped API-key metadata without consulting the vault', async () => {
    rexConfig = {
      credential_refs: { household: { OPENAI_API_KEY: { ref, integration: 'email', account: null, slot: 'password' } } }
    }
    await expect(invoke('rex:getApiKeys')).resolves.toEqual({
      openai_key_set: false,
      openrouter_key_set: false,
      error: 'Stored API-key state could not be verified'
    })
    expect(mockVault.vaultHasSecret).not.toHaveBeenCalled()
  })

  it('strips integration secrets from gui_settings and returns only ref status to the renderer', async () => {
    const result = await invoke('rex:setSettings', 'integrations', {
      haUrl: 'http://ha.local:8123', haToken: 'ha-secret-value'
    })
    expect(result).toEqual({ ok: true })
    expect(mockVault.vaultSetSecret).toHaveBeenCalledWith(session, 'ha-secret-value', haContext)
    expect(JSON.stringify(guiSettings)).not.toContain('ha-secret-value')
    expect((guiSettings.integrations as Record<string, unknown>).haToken).toBe('')

    mockVault.vaultHasSecret.mockResolvedValue(true)
    const loaded = await invoke('rex:getSettings', 'integrations') as Record<string, unknown>
    expect(loaded.haToken).toBe('')
    expect(loaded.credentialStatus).toMatchObject({ haToken: { ref, hasCredential: true } })
  })

  it('strips every supported integration secret field and per-account password', async () => {
    let index = 0
    mockVault.vaultSetSecret.mockImplementation(async () => {
      const character = String.fromCharCode(65 + index++)
      return `cred_${character.repeat(32)}`
    })
    const secretValues = {
      emailClientSecret: 'marker-email-client', // pragma: allowlist secret
      calendarClientSecret: 'marker-calendar-client', // pragma: allowlist secret
      smsSid: 'marker-sms-sid',
      smsAuthToken: 'marker-sms-token',
      smsFromNumber: 'marker-sms-number',
      haToken: 'marker-ha-token',
      phoneSid: 'marker-phone-sid',
      phoneAuthToken: 'marker-phone-token',
      phoneNumber: 'marker-phone-number',
      phoneTransferNumber: 'marker-transfer-number',
      telegramBotToken: 'marker-telegram-token'
    }
    const result = await invoke('rex:setSettings', 'integrations', {
      ...secretValues,
      emailAccounts: [{
        id: 'work', backend: 'imap', label: 'Work', host: 'imap.example.test',
        port: 993, username: 'alice@example.test', password: 'marker-account-password' // pragma: allowlist secret
      }]
    })
    expect(result).toEqual({ ok: true })
    expect(mockVault.vaultSetSecret).toHaveBeenCalledTimes(12)
    const persistedGui = JSON.stringify(guiSettings)
    const persistedConfig = JSON.stringify(rexConfig)
    for (const marker of [...Object.values(secretValues), 'marker-account-password']) {
      expect(persistedGui).not.toContain(marker)
      expect(persistedConfig).not.toContain(marker)
    }
    const account = ((guiSettings.integrations as Record<string, unknown>)
      .emailAccounts as Array<Record<string, unknown>>)[0]
    expect(account).not.toHaveProperty('password')
    expect(account).not.toHaveProperty('clientSecret')
    expect(account).not.toHaveProperty('credentialRef')
    expect(rexConfig).toHaveProperty('credential_refs.users.alice.email:work')

    mockVault.vaultHasSecret.mockResolvedValue(true)
    const loaded = await invoke('rex:getSettings', 'integrations') as Record<string, unknown>
    for (const field of Object.keys(secretValues)) expect(loaded[field]).toBe('')
    const loadedAccount = (loaded.emailAccounts as Array<Record<string, unknown>>)[0]
    expect(loadedAccount.password).toBe('')
    expect(loadedAccount.clientSecret).toBe('')
    expect(loadedAccount.hasCredential).toBe(true)
  })

  it('rejects duplicate email account ids before writing credentials or settings', async () => {
    const result = await invoke('rex:setSettings', 'integrations', {
      emailAccounts: [
        { id: 'work', backend: 'imap', password: 'first-secret' }, // pragma: allowlist secret
        { id: 'work', backend: 'imap', password: 'second-secret' } // pragma: allowlist secret
      ]
    })
    expect(result).toEqual({ ok: false, error: 'Duplicate email account id: work' })
    expect(guiSettings).toEqual({})
    expect(rexConfig).toEqual({})
    expect(mockVault.vaultSetSecret).not.toHaveBeenCalled()
  })

  it('returns a generic secret-free error and restores both stores when vault or mirror persistence fails', async () => {
    // Real vault/mirror failures can carry vault refs, filesystem paths, or
    // other internal context (S4) - the renderer must only ever see a fixed,
    // generic message, never err.message from these layers verbatim.
    mockVault.vaultSetSecret.mockRejectedValueOnce(new Error('vault unavailable'))
    await expect(invoke('rex:setSettings', 'integrations', { haToken: 'secret' })).resolves.toEqual({
      ok: false, error: 'Settings persistence failed'
    })
    expect(guiSettings).toEqual({})
    expect(rexConfig).toEqual({})

    mockVault.vaultSetSecret.mockResolvedValueOnce(ref)
    mockMirror.mirrorToRexConfig.mockReturnValueOnce({ ok: false, error: 'disk full' })
    await expect(invoke('rex:setSettings', 'integrations', { haToken: 'secret' })).resolves.toEqual({
      ok: false, error: 'Settings persistence failed'
    })
    expect(guiSettings).toEqual({})
    expect(rexConfig).toEqual({})
    expect(mockVault.vaultDeleteSecret).toHaveBeenCalledWith(session, ref, haContext)
  })

  it('never forwards a marker embedded in a vault/filesystem error to the renderer', async () => {
    const marker = 'C:\\Users\\alice\\.rex\\vault.json ref=cred_marker leak-should-not-appear'
    mockVault.vaultSetSecret.mockRejectedValueOnce(new Error(marker))
    const result = await invoke('rex:setSettings', 'integrations', { haToken: 'secret' }) as { error?: string }
    expect(result.error).not.toContain(marker)
    expect(JSON.stringify(result)).not.toContain('vault.json')
  })

  it('does not interpret an omitted email account as credential deletion', async () => {
    guiSettings = {
      integrations: {
        emailAccounts: [{ id: 'work', backend: 'imap', username: 'alice@example.test' }]
      }
    }
    rexConfig = {
      credential_refs: {
        users: {
          alice: {
            'email:work': {
              ref, integration: 'email', account: 'work', slot: 'password'
            }
          }
        }
      }
    }

    await expect(invoke('rex:setSettings', 'integrations', { emailAccounts: [] }))
      .resolves.toEqual({ ok: true })
    expect(mockVault.vaultDeleteSecret).not.toHaveBeenCalled()
    expect(rexConfig).toHaveProperty('credential_refs.users.alice.email:work.ref', ref)
  })

  it('preserves exact nonblank secret bytes while using trim only for blank detection', async () => {
    await expect(invoke('rex:setApiKey', 'OPENAI_API_KEY', '  exact-api-key  '))
      .resolves.toEqual({ ok: true })
    expect(mockVault.vaultSetSecret).toHaveBeenCalledWith(
      session, '  exact-api-key  ', openAiContext
    )

    mockVault.vaultSetSecret.mockClear().mockResolvedValue(ref)
    await expect(invoke('rex:setSettings', 'integrations', {
      emailAccounts: [{ id: 'work', backend: 'imap', password: '  exact-password  ' }]
    })).resolves.toEqual({ ok: true })
    expect(mockVault.vaultSetSecret).toHaveBeenCalledWith(
      session, '  exact-password  ', emailContext
    )
  })
  it('requires explicit confirmation before removing an email account', async () => {
    guiSettings = {
      integrations: {
        emailAccounts: [{ id: 'work', backend: 'imap', username: 'alice@example.test' }]
      }
    }

    await expect(invoke('rex:removeEmailAccount', 'work', false)).resolves.toEqual({
      ok: false, error: 'Email account removal requires confirmation'
    })
    expect(guiSettings).toHaveProperty('integrations.emailAccounts.0.id', 'work')
    expect(mockConfigStore.writeGuiSettings).not.toHaveBeenCalled()
    expect(mockVault.vaultDeleteSecret).not.toHaveBeenCalled()
  })

  it('removes a confirmed email account and its exact contextual credential', async () => {
    guiSettings = {
      integrations: {
        emailAccounts: [{ id: 'work', backend: 'imap', username: 'alice@example.test' }]
      }
    }
    rexConfig = {
      credential_refs: {
        users: {
          alice: {
            'email:work': {
              ref, integration: 'email', account: 'work', slot: 'password'
            }
          }
        }
      }
    }

    await expect(invoke('rex:removeEmailAccount', 'work', true)).resolves.toEqual({ ok: true })
    expect(guiSettings).toHaveProperty('integrations.emailAccounts', [])
    expect(rexConfig).not.toHaveProperty('credential_refs.users.alice.email:work')
    expect(mockVault.vaultDeleteSecret).toHaveBeenCalledWith(session, ref, emailContext)
  })

  it('restores account metadata and its reference when vault deletion fails', async () => {
    const originalGui = {
      integrations: {
        emailAccounts: [{ id: 'work', backend: 'imap', username: 'alice@example.test' }]
      }
    }
    const originalConfig = {
      credential_refs: {
        users: {
          alice: {
            'email:work': {
              ref, integration: 'email', account: 'work', slot: 'password'
            }
          }
        }
      }
    }
    guiSettings = structuredClone(originalGui)
    rexConfig = structuredClone(originalConfig)
    mockVault.vaultDeleteSecret.mockRejectedValueOnce(new Error('vault deletion failed'))

    await expect(invoke('rex:removeEmailAccount', 'work', true)).resolves.toEqual({
      ok: false, error: 'Email account removal failed'
    })
    expect(guiSettings).toEqual(originalGui)
    expect(rexConfig).toEqual(originalConfig)
  })
  it('stores the OpenClaw gateway token in the vault and never in GUI settings', async () => {
    const result = await invoke('rex:setSettings', 'integrations', {
      openclawGatewayUrl: 'http://127.0.0.1:18789',
      openclawToolsEnabled: true,
      openclawVoiceEnabled: false,
      openclawToken: 'openclaw-secret'
    })
    expect(result).toEqual({ ok: true })
    expect(mockVault.vaultSetSecret).toHaveBeenCalledWith(session, 'openclaw-secret', openClawContext)
    expect(JSON.stringify(guiSettings)).not.toContain('openclaw-secret')
    expect((guiSettings.integrations as Record<string, unknown>).openclawToken).toBe('')
    expect(rexConfig).toHaveProperty('credential_refs.household.OPENCLAW_GATEWAY_TOKEN')
  })

  it('hydrates unsaved OpenClaw GUI fields from existing rex_config without exposing the token', async () => {
    rexConfig = {
      openclaw: {
        gateway_url: 'http://existing-openclaw:18789',
        use_tools: true,
        use_voice_backend: false
      },
      credential_refs: {
        household: {
          OPENCLAW_GATEWAY_TOKEN: {
            ref, integration: 'openclaw_gateway', account: null, slot: 'token'
          }
        }
      }
    }
    mockVault.vaultHasSecret.mockResolvedValue(true)
    const loaded = await invoke('rex:getSettings', 'integrations') as Record<string, unknown>
    expect(loaded).toMatchObject({
      openclawGatewayUrl: 'http://existing-openclaw:18789',
      openclawToolsEnabled: true,
      openclawVoiceEnabled: false,
      openclawToken: ''
    })
    expect(loaded.credentialStatus).toMatchObject({
      openclawToken: { ref, hasCredential: true }
    })
  })

  it('discovers Ollama models only from the configured runtime endpoint', async () => {
    rexConfig = { ollama: { base_url: 'http://ollama.local:11434' } }
    mockModelDiscovery.discoverAiModelsAtEndpoint.mockResolvedValue({
      ok: true,
      models: ['llama3.2:3b']
    })

    await expect(invoke('rex:discoverAiModels', 'ollama')).resolves.toEqual({
      ok: true,
      models: ['llama3.2:3b']
    })
    expect(mockModelDiscovery.discoverAiModelsAtEndpoint).toHaveBeenCalledWith(
      'ollama',
      'http://ollama.local:11434'
    )
  })

  it('discovers LM Studio models only from the configured OpenAI-compatible endpoint', async () => {
    rexConfig = { openai: { base_url: 'http://lmstudio.local:1234/v1' } }
    mockModelDiscovery.discoverAiModelsAtEndpoint.mockResolvedValue({
      ok: true,
      models: ['qwen/qwen3-8b']
    })

    await expect(invoke('rex:discoverAiModels', 'lmstudio')).resolves.toEqual({
      ok: true,
      models: ['qwen/qwen3-8b']
    })
    expect(mockModelDiscovery.discoverAiModelsAtEndpoint).toHaveBeenCalledWith(
      'lmstudio',
      'http://lmstudio.local:1234/v1'
    )
  })

  it('rejects unsupported model discovery kinds without accepting a renderer URL', async () => {
    await expect(invoke(
      'rex:discoverAiModels',
      'https://attacker.example/models'
    )).resolves.toEqual({
      ok: false,
      models: [],
      error: 'Unsupported model discovery provider'
    })
    expect(mockModelDiscovery.discoverAiModelsAtEndpoint).not.toHaveBeenCalled()
  })

  it('returns a generic configuration error without leaking config read failures', async () => {
    mockConfigStore.readRexConfigStrict.mockImplementationOnce(() => {
      throw new Error('C:\\Users\\alice\\secret-config.json marker-should-not-leak')
    })

    const result = await invoke('rex:discoverAiModels', 'ollama') as Record<string, unknown>
    expect(result).toEqual({
      ok: false,
      models: [],
      error: 'Model discovery configuration could not be read'
    })
    expect(JSON.stringify(result)).not.toContain('marker-should-not-leak')
    expect(mockModelDiscovery.discoverAiModelsAtEndpoint).not.toHaveBeenCalled()
  })

})

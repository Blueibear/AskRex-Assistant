import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Settings } from '../src/types/ipc'
import type { ElectronSessionIdentity } from '../src/main/sessionIdentity'

let guiSettings: Record<string, Settings>
let rexConfig: Record<string, unknown>

vi.mock('../src/main/configStore', () => ({
  readGuiSettings: vi.fn(() => guiSettings),
  readRexConfig: vi.fn(() => rexConfig),
  readRexConfigStrict: vi.fn(() => rexConfig),
  writeGuiSettings: vi.fn((value: Record<string, Settings>) => {
    guiSettings = structuredClone(value)
  }),
  writeRexConfig: vi.fn((value: Record<string, unknown>) => {
    rexConfig = structuredClone(value)
  })
}))

vi.mock('../src/main/integrationStatus', () => ({
  reconcileIntegrationStatuses: vi.fn().mockResolvedValue(undefined)
}))

vi.mock('../src/main/credentialVault', () => ({
  vaultDeleteSecret: vi.fn().mockResolvedValue(true),
  vaultHasSecret: vi.fn().mockResolvedValue(false),
  vaultSetSecret: vi.fn()
}))

import { buildAiSettings } from '../src/main/aiSettings'
import { persistSettingsSection } from '../src/main/integrationSettingsStorage'

const session: ElectronSessionIdentity = {
  userId: 'alice',
  sessionId: 'session-1',
  authentication: 'local-os-session'
}

describe('AI provider persistence across settings reloads (US-071)', () => {
  beforeEach(() => {
    guiSettings = {
      ai: {
        provider: 'local',
        customModelId: ''
      }
    }
    rexConfig = {
      models: {
        llm_provider: 'transformers',
        llm_model: 'mistralai/Mistral-7B-Instruct-v0.3'
      }
    }
  })

  it('saves a provider-only switch and reloads it from canonical runtime config', async () => {
    const result = await persistSettingsSection(session, 'ai', {
      provider: 'ollama',
      customModelId: ''
    })

    expect(result).toEqual({ ok: true })
    expect(rexConfig).toMatchObject({
      models: {
        llm_provider: 'ollama',
        llm_model: 'mistralai/Mistral-7B-Instruct-v0.3'
      }
    })
    expect(guiSettings.ai).toMatchObject({ provider: 'ollama' })

    const reloaded = buildAiSettings(guiSettings.ai)
    expect(reloaded.provider).toBe('ollama')
  })
})


describe('discovered model persistence across settings reloads (US-072)', () => {
  it('persists and reloads an Ollama model selected from discovery', async () => {
    guiSettings = { ai: { provider: 'ollama', customModelId: '' } }
    rexConfig = {
      models: { llm_provider: 'ollama', llm_model: 'old-model:latest' },
      ollama: { base_url: 'http://127.0.0.1:11434' }
    }

    const result = await persistSettingsSection(session, 'ai', {
      provider: 'ollama',
      customModelId: 'qwen2.5:7b',
      ollamaBaseUrl: 'http://127.0.0.1:11434'
    })

    expect(result).toEqual({ ok: true })
    expect(rexConfig).toMatchObject({
      models: { llm_provider: 'ollama', llm_model: 'qwen2.5:7b' },
      ollama: { base_url: 'http://127.0.0.1:11434' }
    })
    const reloaded = buildAiSettings(guiSettings.ai)
    expect(reloaded.provider).toBe('ollama')
    expect(reloaded.customModelId).toBe('qwen2.5:7b')
  })

  it('persists and reloads an LM Studio model through the OpenAI-compatible runtime path', async () => {
    guiSettings = { ai: { provider: 'openai', model: 'gpt-4o', openaiBaseUrl: '' } }
    rexConfig = {
      models: { llm_provider: 'openai' },
      openai: { model: 'gpt-4o', base_url: null }
    }

    const result = await persistSettingsSection(session, 'ai', {
      provider: 'openai',
      model: 'qwen/qwen3-8b',
      openaiBaseUrl: 'http://127.0.0.1:1234/v1'
    })

    expect(result).toEqual({ ok: true })
    expect(rexConfig).toMatchObject({
      models: { llm_provider: 'openai' },
      openai: {
        model: 'qwen/qwen3-8b',
        base_url: 'http://127.0.0.1:1234/v1'
      }
    })
    const reloaded = buildAiSettings(guiSettings.ai)
    expect(reloaded.provider).toBe('openai')
    expect(reloaded.model).toBe('qwen/qwen3-8b')
    expect(reloaded.openaiBaseUrl).toBe('http://127.0.0.1:1234/v1')
  })
})

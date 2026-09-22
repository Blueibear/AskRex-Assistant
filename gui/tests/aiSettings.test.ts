import { beforeEach, describe, expect, it, vi } from 'vitest'

const { mockReadRexConfig } = vi.hoisted(() => ({ mockReadRexConfig: vi.fn() }))
vi.mock('../src/main/configStore', () => ({ readRexConfig: mockReadRexConfig }))

import {
  OPENAI_DEFAULT_MODEL,
  OPENROUTER_DEFAULT_BASE_URL,
  OPENROUTER_DEFAULT_MODEL,
  buildAiSettings,
  buildAiSettingsForSave,
  normalizeGuiAiProvider,
  toRuntimeAiProvider
} from '../src/main/aiSettings'

describe('AI provider settings', () => {
  beforeEach(() => mockReadRexConfig.mockReset().mockReturnValue({}))

  it('uses a provider-compatible OpenAI default instead of a Claude model', () => {
    const settings = buildAiSettings({ provider: 'openai' })
    expect(settings.provider).toBe('openai')
    expect(settings.model).toBe(OPENAI_DEFAULT_MODEL)
  })

  it('loads OpenRouter model and endpoint independently from OpenAI', () => {
    mockReadRexConfig.mockReturnValue({
      models: { llm_provider: 'openrouter' },
      openai: { model: 'gpt-openai-only' },
      openrouter: {
        model: 'anthropic/claude-sonnet-4',
        base_url: 'https://router.example.test/v1'
      }
    })
    const settings = buildAiSettings({})
    expect(settings.provider).toBe('openrouter')
    expect(settings.model).toBe('gpt-openai-only')
    expect(settings.openrouterModel).toBe('anthropic/claude-sonnet-4')
    expect(settings.openrouterBaseUrl).toBe('https://router.example.test/v1')
  })

  it('provides safe OpenRouter defaults and preserves runtime mapping', () => {
    expect(normalizeGuiAiProvider('openrouter')).toBe('openrouter')
    expect(toRuntimeAiProvider('openrouter')).toBe('openrouter')
    const settings = buildAiSettings({ provider: 'openrouter' })
    expect(settings.openrouterModel).toBe(OPENROUTER_DEFAULT_MODEL)
    expect(settings.openrouterBaseUrl).toBe(OPENROUTER_DEFAULT_BASE_URL)
  })
})


describe('AI provider reload behavior (US-071)', () => {
  beforeEach(() => mockReadRexConfig.mockReset().mockReturnValue({}))

  it('reloads the canonical runtime provider instead of stale GUI state', () => {
    mockReadRexConfig.mockReturnValue({
      models: { llm_provider: 'ollama', llm_model: 'llama3.2:3b' }
    })

    const settings = buildAiSettings({ provider: 'local', customModelId: 'stale-local-model' })

    expect(settings.provider).toBe('ollama')
    expect(settings.customModelId).toBe('stale-local-model')
  })

  it('maps the runtime transformers provider back to the Local Transformers GUI value', () => {
    mockReadRexConfig.mockReturnValue({
      models: { llm_provider: 'transformers', llm_model: 'mistralai/Mistral-7B-Instruct-v0.3' }
    })

    expect(buildAiSettings({}).provider).toBe('local')
  })

  it('falls back safely when the canonical runtime provider is invalid', () => {
    mockReadRexConfig.mockReturnValue({ models: { llm_provider: 'not-a-provider' } })

    expect(buildAiSettings({ provider: 'ollama' }).provider).toBe('openai')
  })
})


describe('OpenAI-compatible endpoint settings (US-072)', () => {
  beforeEach(() => mockReadRexConfig.mockReset().mockReturnValue({}))

  it('loads the configured OpenAI-compatible base URL for LM Studio discovery', () => {
    mockReadRexConfig.mockReturnValue({
      openai: { base_url: 'http://127.0.0.1:1234/v1' }
    })

    expect(buildAiSettings({}).openaiBaseUrl).toBe('http://127.0.0.1:1234/v1')
  })

  it('preserves an explicit blank GUI base URL so a configured compatible endpoint can be cleared', () => {
    mockReadRexConfig.mockReturnValue({
      openai: { base_url: 'http://127.0.0.1:1234/v1' }
    })

    expect(buildAiSettings({ openaiBaseUrl: '' }).openaiBaseUrl).toBe('')
  })

  it('preserves the persisted model when only the discovery endpoint is submitted for save (TEST-004)', () => {
    mockReadRexConfig.mockReturnValue({
      models: { llm_provider: 'openai' },
      openai: { model: 'openai/gpt-oss-20b', base_url: 'http://127.0.0.1:1234/v1' }
    })

    const saved = buildAiSettingsForSave({ openaiBaseUrl: 'http://127.0.0.1:1234/v1' })

    expect(saved.model).toBe('openai/gpt-oss-20b')
    expect(saved.model).not.toBe(OPENAI_DEFAULT_MODEL)
  })
})


describe('autonomy runtime authority (US-073)', () => {
  beforeEach(() => mockReadRexConfig.mockReset().mockReturnValue({}))

  it('loads the canonical runtime autonomy mode instead of stale GUI state', () => {
    mockReadRexConfig.mockReturnValue({ models: { autonomy_mode: 'supervised' } })

    expect(buildAiSettings({ autonomyMode: 'full-auto' }).autonomyMode).toBe('supervised')
  })

  it('falls back to a legacy AI value only when runtime authority is absent', () => {
    expect(buildAiSettings({ autonomyMode: 'supervised' }).autonomyMode).toBe('supervised')
  })

  it('preserves an explicitly submitted autonomy change during save normalization', () => {
    mockReadRexConfig.mockReturnValue({ models: { autonomy_mode: 'manual' } })

    expect(buildAiSettingsForSave({ autonomyMode: 'full-auto' }).autonomyMode).toBe('full-auto')
    expect(buildAiSettings({ autonomyMode: 'full-auto' }).autonomyMode).toBe('manual')
  })
})

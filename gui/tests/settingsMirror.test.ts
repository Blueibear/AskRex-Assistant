import { beforeEach, describe, expect, it, vi } from 'vitest'

const { mockApp } = vi.hoisted(() => ({
  mockApp: {
    isPackaged: false,
    getAppPath: vi.fn().mockReturnValue('/fake/app'),
    getPath: vi.fn().mockReturnValue('/fake/user-data')
  }
}))

vi.mock('electron', () => ({ app: mockApp }))

const { mockReadRexConfig, mockWriteRexConfig } = vi.hoisted(() => ({
  mockReadRexConfig: vi.fn(),
  mockWriteRexConfig: vi.fn()
}))

vi.mock('../src/main/configStore', () => ({
  readRexConfig: mockReadRexConfig,
  writeRexConfig: mockWriteRexConfig
}))

import { mirrorToRexConfig } from '../src/main/settingsMirror'

describe('mirrorToRexConfig truthful failures (S4)', () => {
  beforeEach(() => {
    mockReadRexConfig.mockReset().mockReturnValue({})
    mockWriteRexConfig.mockReset()
  })

  it('returns {ok:true} on a successful mirror', () => {
    const result = mirrorToRexConfig('ai', { temperature: 0.5 } as never)
    expect(result).toEqual({ ok: true })
    expect(mockWriteRexConfig).toHaveBeenCalledTimes(1)
  })

  it('mirrors OpenRouter model and endpoint separately from OpenAI', () => {
    mockReadRexConfig.mockReturnValue({ openai: { model: 'gpt-4o' } })
    const result = mirrorToRexConfig('ai', {
      provider: 'openrouter',
      openrouterModel: 'anthropic/claude-sonnet-4',
      openrouterBaseUrl: 'https://malicious.example.test/v1'
    } as never)
    expect(result).toEqual({ ok: true })
    expect(mockWriteRexConfig).toHaveBeenCalledWith(expect.objectContaining({
      models: expect.objectContaining({ llm_provider: 'openrouter' }),
      openai: { model: 'gpt-4o' },
      openrouter: {
        model: 'anthropic/claude-sonnet-4',
        base_url: 'https://openrouter.ai/api/v1'
      }
    }))
    const written = mockWriteRexConfig.mock.calls[0][0] as Record<string, unknown>
    expect((written.llm as Record<string, unknown>).provider).toBeUndefined()
  })

  it('rejects an empty OpenRouter model instead of persisting an unusable provider', () => {
    const result = mirrorToRexConfig('ai', {
      provider: 'openrouter', openrouterModel: '', openrouterBaseUrl: ''
    } as never)
    expect(result).toEqual({ ok: false, error: 'OpenRouter model is required' })
    expect(mockWriteRexConfig).not.toHaveBeenCalled()
  })

  it('returns {ok:false, error} instead of swallowing a write failure', () => {
    mockWriteRexConfig.mockImplementation(() => {
      throw new Error('disk full')
    })
    const result = mirrorToRexConfig('ai', { temperature: 0.5 } as never)
    expect(result.ok).toBe(false)
    expect(result.error).toContain('disk full')
  })

  it('returns {ok:false, error} when reading the existing config throws', () => {
    mockReadRexConfig.mockImplementation(() => {
      throw new Error('corrupt json')
    })
    const result = mirrorToRexConfig('ai', { temperature: 0.5 } as never)
    expect(result.ok).toBe(false)
    expect(result.error).toContain('corrupt json')
  })

  it('returns {ok:true} for a section with no mirror mapping (no-op)', () => {
    const result = mirrorToRexConfig('unmapped-section', {} as never)
    expect(result).toEqual({ ok: true })
  })
  it('mirrors background voice auto-start into canonical runtime config', () => {
    mockReadRexConfig.mockReturnValue({ runtime: { background_voice_enabled: true } })
    const result = mirrorToRexConfig('voice', { backgroundVoiceEnabled: false } as never)
    expect(result).toEqual({ ok: true })
    expect(mockWriteRexConfig).toHaveBeenCalledWith(expect.objectContaining({
      runtime: expect.objectContaining({ background_voice_enabled: false })
    }))
  })

  it('mirrors replacement audio device indices into canonical Python audio config', () => {
    mockReadRexConfig.mockReturnValue({
      audio: { input_device_index: 1, output_device_index: 2 },
    })
    const result = mirrorToRexConfig('voice', {
      microphoneDeviceIndex: 4,
      speakerDeviceIndex: 7,
    } as never)

    expect(result).toEqual({ ok: true })
    expect(mockWriteRexConfig).toHaveBeenCalledWith(expect.objectContaining({
      audio: {
        input_device_index: 4,
        output_device_index: 7,
      },
    }))
  })

  it('mirrors OpenClaw URL and enabled flags into the canonical block', () => {
    const result = mirrorToRexConfig('integrations', {
      openclawGatewayUrl: 'http://127.0.0.1:18789',
      openclawToolsEnabled: true,
      openclawVoiceEnabled: false
    } as never)
    expect(result).toEqual({ ok: true })
    expect(mockWriteRexConfig).toHaveBeenCalledWith(expect.objectContaining({
      openclaw: expect.objectContaining({
        gateway_url: 'http://127.0.0.1:18789',
        use_tools: true,
        use_voice_backend: false
      })
    }))
  })

})


describe('AI provider persistence (US-071)', () => {
  beforeEach(() => {
    mockReadRexConfig.mockReset().mockReturnValue({
      models: { llm_provider: 'transformers' }
    })
    mockWriteRexConfig.mockReset()
  })

  it('persists an Ollama provider switch before a model identifier is selected', () => {
    const result = mirrorToRexConfig('ai', {
      provider: 'ollama',
      customModelId: ''
    } as never)

    expect(result).toEqual({ ok: true })
    expect(mockWriteRexConfig).toHaveBeenCalledWith(expect.objectContaining({
      models: expect.objectContaining({ llm_provider: 'ollama' })
    }))
  })
})


describe('OpenAI-compatible endpoint persistence (US-072)', () => {
  beforeEach(() => {
    mockReadRexConfig.mockReset().mockReturnValue({
      openai: { model: 'gpt-4o', base_url: null }
    })
    mockWriteRexConfig.mockReset()
  })

  it('mirrors a configured LM Studio-compatible base URL into openai.base_url', () => {
    const result = mirrorToRexConfig('ai', {
      provider: 'openai',
      model: 'gpt-4o',
      openaiBaseUrl: '  http://127.0.0.1:1234/v1  '
    } as never)

    expect(result).toEqual({ ok: true })
    expect(mockWriteRexConfig).toHaveBeenCalledWith(expect.objectContaining({
      openai: expect.objectContaining({
        model: 'gpt-4o',
        base_url: 'http://127.0.0.1:1234/v1'
      })
    }))
  })

  it('clears openai.base_url when the compatible endpoint field is blank', () => {
    mockReadRexConfig.mockReturnValue({
      openai: { model: 'gpt-4o', base_url: 'http://127.0.0.1:1234/v1' }
    })

    const result = mirrorToRexConfig('ai', {
      provider: 'openai',
      model: 'gpt-4o',
      openaiBaseUrl: '   '
    } as never)

    expect(result).toEqual({ ok: true })
    expect(mockWriteRexConfig).toHaveBeenCalledWith(expect.objectContaining({
      openai: expect.objectContaining({ base_url: null })
    }))
  })
})


describe('autonomy runtime persistence (US-073)', () => {
  beforeEach(() => {
    mockReadRexConfig.mockReset().mockReturnValue({ models: { autonomy_mode: 'manual' } })
    mockWriteRexConfig.mockReset()
  })

  it('mirrors autonomy only from the AI settings section', () => {
    expect(mirrorToRexConfig('ai', { autonomyMode: 'supervised' } as never)).toEqual({ ok: true })
    expect(mockWriteRexConfig).toHaveBeenCalledWith(expect.objectContaining({
      models: expect.objectContaining({ autonomy_mode: 'supervised' })
    }))
  })

  it('rejects an invalid AI autonomy mode instead of corrupting runtime config', () => {
    const result = mirrorToRexConfig('ai', { autonomyMode: 'unbounded' } as never)

    expect(result).toEqual({ ok: false, error: 'Invalid autonomy mode' })
    expect(mockWriteRexConfig).not.toHaveBeenCalled()
  })

  it('ignores a legacy System autonomy field instead of changing runtime authority', () => {
    expect(mirrorToRexConfig('system', { autonomyMode: 'full-auto' } as never)).toEqual({ ok: true })
    expect(mockWriteRexConfig).toHaveBeenCalledWith(expect.objectContaining({
      models: expect.objectContaining({ autonomy_mode: 'manual' })
    }))
  })
})

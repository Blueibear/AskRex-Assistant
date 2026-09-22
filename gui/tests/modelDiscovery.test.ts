import { describe, expect, it, vi } from 'vitest'
import { discoverAiModelsAtEndpoint } from '../src/main/modelDiscovery'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' }
  })
}

describe('model discovery (US-072)', () => {
  it('discovers unique Ollama model names from the configured endpoint', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({
      models: [
        { name: 'llama3.2:3b' },
        { name: 'qwen2.5:7b' },
        { name: 'llama3.2:3b' },
        { name: '   ' }
      ]
    }))

    const result = await discoverAiModelsAtEndpoint(
      'ollama',
      'http://127.0.0.1:11434/',
      fetchImpl
    )

    expect(result).toEqual({ ok: true, models: ['llama3.2:3b', 'qwen2.5:7b'] })
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://127.0.0.1:11434/api/tags',
      expect.objectContaining({ method: 'GET' })
    )
  })

  it('discovers unique LM Studio model ids from its configured OpenAI-compatible endpoint', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({
      data: [
        { id: 'qwen/qwen3-8b' },
        { id: 'google/gemma-3-4b' },
        { id: 'qwen/qwen3-8b' }
      ]
    }))

    const result = await discoverAiModelsAtEndpoint(
      'lmstudio',
      'http://127.0.0.1:1234/v1/',
      fetchImpl
    )

    expect(result).toEqual({
      ok: true,
      models: ['qwen/qwen3-8b', 'google/gemma-3-4b']
    })
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://127.0.0.1:1234/v1/models',
      expect.objectContaining({ method: 'GET' })
    )
  })

  it('discovers LM Studio model ids from a full OpenAI-compatible /v1/models response (TEST-004)', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({
      object: 'list',
      data: [
        { id: 'openai/gpt-oss-20b', object: 'model', owned_by: 'organization_owner' }
      ]
    }))

    const result = await discoverAiModelsAtEndpoint(
      'lmstudio',
      'http://127.0.0.1:1234/v1',
      fetchImpl
    )

    expect(result).toEqual({ ok: true, models: ['openai/gpt-oss-20b'] })
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://127.0.0.1:1234/v1/models',
      expect.objectContaining({ method: 'GET' })
    )
  })

  it.each([
    ['ollama', { models: [] }],
    ['lmstudio', { data: [] }]
  ] as const)('treats an empty %s model list as a successful empty result', async (provider, body) => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(body))

    await expect(discoverAiModelsAtEndpoint(provider, 'http://127.0.0.1:9999', fetchImpl))
      .resolves.toEqual({ ok: true, models: [] })
  })

  it('returns a safe error for provider HTTP failure without exposing the response body', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(
      { error: 'secret internal provider detail' },
      500
    ))

    const result = await discoverAiModelsAtEndpoint(
      'ollama',
      'http://127.0.0.1:11434',
      fetchImpl
    )

    expect(result).toEqual({
      ok: false,
      models: [],
      error: 'Ollama model discovery failed (HTTP 500)'
    })
    expect(JSON.stringify(result)).not.toContain('secret internal provider detail')
  })

  it('returns a safe reachability error without forwarding exception payloads', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(
      new Error('connect ECONNREFUSED credential-marker-should-not-leak')
    )

    const result = await discoverAiModelsAtEndpoint(
      'lmstudio',
      'http://127.0.0.1:1234/v1',
      fetchImpl
    )

    expect(result).toEqual({
      ok: false,
      models: [],
      error: 'Unable to reach configured LM Studio endpoint'
    })
    expect(JSON.stringify(result)).not.toContain('credential-marker')
  })

  it.each([
    ['ollama', { data: [] }],
    ['lmstudio', { models: [] }]
  ] as const)('rejects a malformed %s payload instead of pretending it is empty', async (provider, body) => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(body))

    const result = await discoverAiModelsAtEndpoint(provider, 'http://127.0.0.1:9999', fetchImpl)

    expect(result.ok).toBe(false)
    expect(result.models).toEqual([])
    expect(result.error).toContain('invalid model list')
  })

  it.each([
    ['', 'Ollama model discovery endpoint is not configured'],
    ['ftp://127.0.0.1/models', 'Ollama model discovery endpoint must use HTTP or HTTPS'],
    ['not a url', 'Ollama model discovery endpoint must use HTTP or HTTPS'],
    ['http://127.0.0.1:11434?token=secret', 'Ollama model discovery endpoint must not include a query or fragment'],
    ['http://127.0.0.1:11434#models', 'Ollama model discovery endpoint must not include a query or fragment']
  ])('rejects missing or invalid configured endpoints without calling fetch', async (endpoint, error) => {
    const fetchImpl = vi.fn()

    const result = await discoverAiModelsAtEndpoint('ollama', endpoint, fetchImpl)

    expect(result).toEqual({ ok: false, models: [], error })
    expect(fetchImpl).not.toHaveBeenCalled()
  })
})

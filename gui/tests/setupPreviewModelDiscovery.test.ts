import { beforeEach, describe, expect, it, vi } from 'vitest'

const { registeredHandlers, mockHandle } = vi.hoisted(() => {
  const registeredHandlers = new Map<string, (...args: unknown[]) => unknown>()
  const mockHandle = vi.fn((channel: string, fn: (...args: unknown[]) => unknown) => {
    registeredHandlers.set(channel, fn)
  })
  return { registeredHandlers, mockHandle }
})
vi.mock('electron', () => ({ ipcMain: { handle: mockHandle, removeHandler: vi.fn() } }))

const { mockModelDiscovery } = vi.hoisted(() => ({
  mockModelDiscovery: { discoverAiModelsAtEndpoint: vi.fn() }
}))
vi.mock('../src/main/modelDiscovery', () => mockModelDiscovery)

import { registerSetupPreviewHandlers } from '../src/main/handlers/setupPreview'

async function invoke(channel: string, ...args: unknown[]): Promise<unknown> {
  const handler = registeredHandlers.get(channel)
  if (!handler) throw new Error(`No handler registered for ${channel}`)
  return handler(null, ...args)
}

describe('pre-auth setup-safe LM Studio model discovery (TEST-001)', () => {
  beforeEach(() => {
    registeredHandlers.clear()
    mockModelDiscovery.discoverAiModelsAtEndpoint.mockReset().mockResolvedValue({
      ok: true,
      models: ['openai/gpt-oss-20b']
    })
    registerSetupPreviewHandlers()
  })

  it('registers the discovery channel before any identity/session is available', () => {
    expect(registeredHandlers.has('rex:discoverSetupAiModels')).toBe(true)
  })

  it('discovers models using the exact renderer-supplied endpoint, not stored config', async () => {
    const result = await invoke('rex:discoverSetupAiModels', 'lmstudio', 'http://127.0.0.1:1234/v1')

    expect(mockModelDiscovery.discoverAiModelsAtEndpoint).toHaveBeenCalledWith(
      'lmstudio',
      'http://127.0.0.1:1234/v1'
    )
    expect(result).toEqual({ ok: true, models: ['openai/gpt-oss-20b'] })
  })

  it('rejects an unsupported discovery kind without calling the discovery implementation', async () => {
    const result = await invoke('rex:discoverSetupAiModels', 'anthropic', 'https://api.anthropic.com')

    expect(mockModelDiscovery.discoverAiModelsAtEndpoint).not.toHaveBeenCalled()
    expect(result).toEqual({
      ok: false,
      models: [],
      error: 'Unsupported model discovery provider'
    })
  })

  it('propagates a discovery failure without overwriting it as success', async () => {
    mockModelDiscovery.discoverAiModelsAtEndpoint.mockResolvedValue({
      ok: false,
      models: [],
      error: 'Unable to reach configured LM Studio endpoint'
    })

    const result = await invoke('rex:discoverSetupAiModels', 'lmstudio', 'http://127.0.0.1:1234/v1')

    expect(result).toEqual({
      ok: false,
      models: [],
      error: 'Unable to reach configured LM Studio endpoint'
    })
  })
})

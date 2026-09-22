import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'
import { ModelDiscoveryRequestGate } from '../src/types/modelDiscoveryRequestGate'

const source = readFileSync(
  join(process.cwd(), 'src/pages/settings/AiSettingsSection.tsx'),
  'utf8'
)

describe('AI model discovery UI wiring (US-072)', () => {
  it('exposes explicit user-triggered discovery for Ollama and LM Studio', () => {
    expect(source).toContain("onClick={() => handleDiscoverModels('ollama')}")
    expect(source).toContain("onClick={() => handleDiscoverModels('lmstudio')}")
    expect(source).toContain('Discover LM Studio Models')
    expect(source).toContain('Discover Models')
  })

  it('persists only the discovery endpoint before calling the configured-endpoint discovery IPC', () => {
    const saveIndex = source.indexOf(".setSettings('ai', endpointSettings)")
    const discoverIndex = source.indexOf('.discoverAiModels(provider)')
    expect(saveIndex).toBeGreaterThan(-1)
    expect(discoverIndex).toBeGreaterThan(saveIndex)
  })

  it('never sends the whole form (including model) as the pre-discovery save', () => {
    const handlerStart = source.indexOf('function handleDiscoverModels')
    const handlerEnd = source.indexOf('\n  function ', handlerStart + 1)
    const handlerSource = source.slice(handlerStart, handlerEnd)
    expect(handlerSource).not.toContain("setSettings('ai', form as unknown as Settings)")
    expect(handlerSource).not.toContain('model: form.model')
  })

  it('does not trigger model discovery from the settings load effect', () => {
    const effectStart = source.indexOf('useEffect(() => {')
    const effectEnd = source.indexOf('}, [addToast])', effectStart)
    const effectSource = source.slice(effectStart, effectEnd)
    expect(effectSource).not.toContain('discoverAiModels')
  })

  it('keeps manual model entry while labeling discovered availability separately', () => {
    expect(source).toContain('OpenAI Model ID')
    expect(source).toContain('Model Name / Tag')
    expect(source).toContain('OpenAI-compatible Base URL (LM Studio)')
    expect(source).toContain('<ModelDiscoveryResults')
  })

  it('ignores an older discovery completion after a newer request starts', () => {
    const gate = new ModelDiscoveryRequestGate()
    const olderRequest = gate.begin()
    const newerRequest = gate.begin()
    const applied: string[] = []

    if (gate.isCurrent(newerRequest)) applied.push('newer-model')
    if (gate.isCurrent(olderRequest)) applied.push('older-model')

    expect(applied).toEqual(['newer-model'])
    gate.invalidate()
    expect(gate.isCurrent(newerRequest)).toBe(false)
  })
})

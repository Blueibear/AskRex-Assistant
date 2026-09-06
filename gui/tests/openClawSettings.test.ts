import { readFileSync } from 'fs'
import { join } from 'path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ElectronSessionIdentity } from '../src/main/sessionIdentity'

const { mockConfig, mockRefs, mockVault } = vi.hoisted(() => ({
  mockConfig: { readRexConfigStrict: vi.fn() },
  mockRefs: { getVaultReference: vi.fn() },
  mockVault: { vaultGetSecret: vi.fn(), vaultHasSecret: vi.fn() }
}))

vi.mock('../src/main/configStore', () => mockConfig)
vi.mock('../src/main/credentialReferences', () => mockRefs)
vi.mock('../src/main/credentialVault', () => mockVault)

import { testOpenClawConnection } from '../src/main/openClaw'
import { integrationSettingsFrom } from '../src/main/integrationStatus'

const session: ElectronSessionIdentity = {
  userId: 'alice', sessionId: 'session-1', authentication: 'local-os-session'
}
const ref = `cred_${'O'.repeat(32)}`
const context = { scope: 'household', integration: 'openclaw_gateway', account: null, slot: 'token' }

describe('OpenClaw Electron integration', () => {
  beforeEach(() => {
    mockConfig.readRexConfigStrict.mockReset().mockReturnValue({
      openclaw: { gateway_url: 'http://127.0.0.1:18789', use_tools: true, use_voice_backend: false }
    })
    mockRefs.getVaultReference.mockReset().mockReturnValue({ ref })
    mockVault.vaultGetSecret.mockReset().mockResolvedValue('vault-token')
    mockVault.vaultHasSecret.mockReset().mockResolvedValue(true)
  })

  it('tests health with the vault token without returning the token', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, status: 200 })
    const result = await testOpenClawConnection(session, fetchImpl as never)

    expect(result).toMatchObject({ ok: true, state: 'reachable' })
    expect(JSON.stringify(result)).not.toContain('vault-token')
    expect(fetchImpl).toHaveBeenCalledWith(
      'http://127.0.0.1:18789/healthz',
      expect.objectContaining({ headers: { Authorization: 'Bearer vault-token' } })
    )
    expect(mockVault.vaultGetSecret).toHaveBeenCalledWith(session, ref, context)
  })

  it('fails closed as unconfigured when URL or credential is missing', async () => {
    mockRefs.getVaultReference.mockReturnValue(null)
    const fetchImpl = vi.fn()
    const result = await testOpenClawConnection(session, fetchImpl as never)
    expect(result).toEqual({ ok: false, state: 'unconfigured', error: 'OpenClaw gateway URL and token are required.' })
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('reports a sanitized degraded state when gateway health fails', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error('socket secret-internal-detail'))
    const result = await testOpenClawConnection(session, fetchImpl as never)
    expect(result).toEqual({ ok: false, state: 'degraded', error: 'OpenClaw gateway health check failed.' })
    expect(JSON.stringify(result)).not.toContain('secret-internal-detail')
  })
  it('exposes honest OpenClaw controls and status in the integrations UI', () => {
    const root = join(__dirname, '..')
    const section = readFileSync(join(root, 'src/pages/settings/integrations/IntegrationsSettingsSection.tsx'), 'utf8')
    const openclawSection = readFileSync(join(root, 'src/pages/settings/integrations/OpenClawIntegrationSection.tsx'), 'utf8')
    const controller = readFileSync(join(root, 'src/pages/settings/integrations/useIntegrationsSettingsController.ts'), 'utf8')
    const settingsUi = `${section}
${openclawSection}
${controller}`
    const inventory = readFileSync(join(root, 'src/main/integrationInventory.ts'), 'utf8')
    const types = readFileSync(join(root, 'src/types/ipc.ts'), 'utf8')
    const integrationsPage = readFileSync(join(root, 'src/pages/IntegrationsPage.tsx'), 'utf8')

    for (const field of ['openclawGatewayUrl', 'openclawToolsEnabled', 'openclawVoiceEnabled', 'openclawToken']) {
      expect(types).toContain(field)
      expect(settingsUi).toContain(field)
    }
    expect(openclawSection).toContain('Experimental - off by default')
    expect(section).toContain("onTest={() => handleTest('openclaw')}")
    expect(openclawSection).toContain('onTest={onTest}')
    expect(controller).toContain('function handleTest(section: IntegrationSection)')
    expect(controller).toContain('window.rex.testIntegration(section)')
    expect(openclawSection).toContain('Gateway reachable; authentication and tool capability are not yet proven.')
    expect(inventory).toContain("key: 'openclaw'")
    expect(inventory).toContain('testable: true')
    expect(inventory).toContain("configure_url: '/settings?section=integrations'")
    expect(integrationsPage).toContain('window.rex.testIntegration')
    expect(integrationsPage).toContain('Test connection')
    expect(integrationsPage).toContain('int.testable')
  })

  it('does not synthesize OpenClaw defaults into the startup mirror before explicit GUI save', () => {
    const untouched = integrationSettingsFrom({} as never)
    expect(untouched).not.toHaveProperty('openclawGatewayUrl')
    expect(untouched).not.toHaveProperty('openclawToolsEnabled')
    expect(untouched).not.toHaveProperty('openclawVoiceEnabled')

    const explicit = integrationSettingsFrom({
      integrations: { openclawGatewayUrl: 'http://127.0.0.1:18789', openclawToolsEnabled: true }
    } as never)
    expect(explicit).toMatchObject({
      openclawGatewayUrl: 'http://127.0.0.1:18789',
      openclawToolsEnabled: true
    })
  })

})
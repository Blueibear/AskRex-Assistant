import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ElectronSessionIdentity } from '../src/main/sessionIdentity'

const { registeredHandlers, mockHandle } = vi.hoisted(() => {
  const registeredHandlers = new Map<string, (...args: unknown[]) => unknown>()
  const mockHandle = vi.fn((channel: string, fn: (...args: unknown[]) => unknown) => {
    registeredHandlers.set(channel, fn)
  })
  return { registeredHandlers, mockHandle }
})

const { mockSpawnSync } = vi.hoisted(() => ({ mockSpawnSync: vi.fn() }))
vi.mock('electron', () => ({ ipcMain: { handle: mockHandle } }))
vi.mock('child_process', () => ({ spawnSync: mockSpawnSync }))
vi.mock('../src/main/bridgeResolver', () => ({
  resolvePythonCommand: () => 'python',
  resolveBridgePath: (name: string) => `/bridge/${name}`,
  bridgeSpawnOptions: () => ({ cwd: '/runtime', env: { SAFE: '1' } })
}))

import { registerPairingHandlers } from '../src/main/handlers/pairing'

const session: ElectronSessionIdentity = {
  userId: 'alice',
  sessionId: 'session-1',
  authentication: 'local-os-session'
}

async function invoke(channel: string, ...args: unknown[]): Promise<unknown> {
  const handler = registeredHandlers.get(channel)
  if (!handler) throw new Error(`No handler registered for ${channel}`)
  return handler(null, ...args)
}

describe('desktop pairing IPC authority', () => {
  beforeEach(() => {
    registeredHandlers.clear()
    mockSpawnSync.mockReset().mockReturnValue({
      status: 0,
      stdout: JSON.stringify({ ok: true, requests: [] }),
      stderr: ''
    })
    registerPairingHandlers(session)
  })

  it('registers only desktop authority operations', () => {
    expect([...registeredHandlers.keys()].sort()).toEqual([
      'rex:approvePairing',
      'rex:createPairingChallenge',
      'rex:denyPairing',
      'rex:listPairedDevices',
      'rex:listPendingPairings',
      'rex:revokePairedDevice'
    ])
  })

  it('binds challenge creation to the private AskRex session and approver identity', async () => {
    await invoke('rex:createPairingChallenge', ['chat.send', 'voice.use'])
    expect(mockSpawnSync).toHaveBeenCalledTimes(1)
    const [python, args, options] = mockSpawnSync.mock.calls[0]
    expect(python).toBe('python')
    expect(args).toEqual(['/bridge/rex_pairing_bridge.py'])
    const payload = JSON.parse(options.input)
    expect(payload).toMatchObject({
      action: 'create_challenge',
      scopes: ['chat.send', 'voice.use'],
      user: 'alice',
      session_id: 'session-1',
      data_scope: 'private',
      approver: 'alice'
    })
  })

  it('never forwards bridge stderr or raw exceptions to the renderer', async () => {
    mockSpawnSync.mockReturnValueOnce({
      status: 1,
      stdout: '',
      stderr: 'C:\\Users\\alice\\vault.db token=secret-internal-marker'
    })
    const failed = await invoke('rex:listPendingPairings') as { error?: string }
    expect(failed).toEqual({
      ok: false,
      error: 'Pairing service could not complete the request.'
    })
    expect(JSON.stringify(failed)).not.toContain('secret-internal-marker')

    mockSpawnSync.mockImplementationOnce(() => {
      throw new Error('private-path-marker')
    })
    const unavailable = await invoke('rex:listPairedDevices') as { error?: string }
    expect(unavailable).toEqual({ ok: false, error: 'Pairing service is unavailable.' })
    expect(JSON.stringify(unavailable)).not.toContain('private-path-marker')
  })

  it('uses explicit revoke and approval identifiers without accepting client authority fields', async () => {
    await invoke('rex:approvePairing', 'request-1')
    let payload = JSON.parse(mockSpawnSync.mock.calls[0][2].input)
    expect(payload).toMatchObject({ action: 'approve', request_id: 'request-1' })

    await invoke('rex:revokePairedDevice', 'device-1')
    payload = JSON.parse(mockSpawnSync.mock.calls[1][2].input)
    expect(payload).toMatchObject({ action: 'revoke', device_id: 'device-1' })
  })
})

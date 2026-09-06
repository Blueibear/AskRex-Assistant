import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ElectronSessionIdentity } from '../src/main/sessionIdentity'
import type { UserProfile } from '../src/types/ipc'

type Handler = (event: unknown, ...args: unknown[]) => unknown

const { registeredHandlers, mockHandle, mockSpawnSync } = vi.hoisted(() => {
  const registeredHandlers = new Map<string, Handler>()
  const mockHandle = vi.fn((channel: string, handler: Handler) => {
    registeredHandlers.set(channel, handler)
  })
  return { registeredHandlers, mockHandle, mockSpawnSync: vi.fn() }
})

vi.mock('electron', () => ({ ipcMain: { handle: mockHandle } }))
vi.mock('child_process', () => ({ spawnSync: mockSpawnSync }))
vi.mock('../src/main/bridgeResolver', () => ({
  resolvePythonCommand: () => 'python',
  resolveBridgePath: (name: string) => `/bridge/${name}`,
  bridgeSpawnOptions: () => ({ cwd: '/runtime', env: { SAFE: '1' } })
}))

import { registerProfileHandlers } from '../src/main/handlers/profile'

const session: ElectronSessionIdentity = {
  userId: 'alice',
  sessionId: 'session-1',
  authentication: 'local-os-session'
}

const validProfile: UserProfile = {
  user_id: 'alice',
  name: 'Alice Example',
  initials: 'AE',
  role: 'Administrator',
  permissions: ['admin'],
  preferences: { theme: 'dark' },
  voice_enrolled: true,
  voice_model_id: 'voice-model',
  voice_sample_count: 3,
  voice_updated_at: '2026-08-06T00:00:00Z',
  avatar_present: false,
  avatar_mime_type: null,
  avatar_data: null,
  scope_labels: { profile: 'user-private', household_settings: 'shared' }
}

function successfulProfile(profile: UserProfile = validProfile): void {
  mockSpawnSync.mockReturnValue({
    status: 0,
    stdout: JSON.stringify({ ok: true, profile }),
    stderr: ''
  })
}

function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = registeredHandlers.get(channel)
  if (!handler) throw new Error(`No handler registered for ${channel}`)
  return handler(null, ...args)
}

function spawnedPayload(): Record<string, unknown> {
  const options = mockSpawnSync.mock.calls[0][2] as { input: string }
  return JSON.parse(options.input) as Record<string, unknown>
}

describe('profile IPC authority', () => {
  beforeEach(() => {
    registeredHandlers.clear()
    mockHandle.mockClear()
    mockSpawnSync.mockReset()
    successfulProfile()
    registerProfileHandlers(session)
  })

  it('registers exactly four profile channels', () => {
    expect([...registeredHandlers.keys()].sort()).toEqual([
      'rex:getProfile',
      'rex:removeProfileAvatar',
      'rex:setProfileAvatar',
      'rex:updateProfilePreferences'
    ])
  })

  it('binds get to the immutable private desktop session', () => {
    expect(invoke('rex:getProfile')).toEqual({ ok: true, profile: validProfile })
    expect(mockSpawnSync).toHaveBeenCalledTimes(1)
    const [python, args, options] = mockSpawnSync.mock.calls[0] as [
      string,
      string[],
      { input: string; timeout: number; cwd: string; env: Record<string, string> }
    ]
    expect(python).toBe('python')
    expect(args).toEqual(['/bridge/rex_profile_bridge.py'])
    expect(options.timeout).toBe(15_000)
    expect(options.cwd).toBe('/runtime')
    expect(JSON.parse(options.input)).toMatchObject({
      action: 'get',
      user: 'alice',
      session_id: 'session-1',
      data_scope: 'private'
    })
  })

  it('rejects malformed preferences before spawning', () => {
    const cyclic: Record<string, unknown> = {}
    cyclic.self = cyclic
    const invalid: unknown[] = [null, [], { value: Number.NaN }, { value: 1n }, cyclic]

    for (const value of invalid) {
      mockSpawnSync.mockClear()
      const response = invoke('rex:updateProfilePreferences', value) as { ok: boolean }
      expect(response.ok).toBe(false)
      expect(mockSpawnSync).not.toHaveBeenCalled()
    }
  })

  it('rejects oversized preferences before spawning', () => {
    const response = invoke('rex:updateProfilePreferences', {
      notes: 'x'.repeat(33 * 1024)
    }) as { ok: boolean; error?: string }

    expect(response).toEqual({ ok: false, error: 'Preferences are too large.' })
    expect(mockSpawnSync).not.toHaveBeenCalled()
  })

  it('sends valid preferences without renderer authority fields', () => {
    const response = invoke('rex:updateProfilePreferences', { theme: 'light' })
    expect(response).toEqual({ ok: true, profile: validProfile })
    expect(spawnedPayload()).toEqual({
      action: 'update_preferences',
      preferences: { theme: 'light' },
      user: 'alice',
      session_id: 'session-1',
      data_scope: 'private'
    })
  })

  it('rejects invalid avatar arguments before spawning', () => {
    const invalidCases: unknown[][] = [
      ['image/gif', 'YWJjZA=='],
      ['image/png', 'not-base64!'],
      ['image/png', 'abc'],
      ['image/png', ''],
      ['image/png', 'a'.repeat(2_900_001)],
      [123, 'YWJjZA=='],
      ['image/png', {}]
    ]

    for (const args of invalidCases) {
      mockSpawnSync.mockClear()
      const response = invoke('rex:setProfileAvatar', ...args) as { ok: boolean }
      expect(response.ok).toBe(false)
      expect(mockSpawnSync).not.toHaveBeenCalled()
    }
  })

  it('sends a strictly validated avatar without a user argument', () => {
    const response = invoke('rex:setProfileAvatar', 'image/png', 'YWJjZA==')
    expect(response).toEqual({ ok: true, profile: validProfile })
    expect(spawnedPayload()).toEqual({
      action: 'set_avatar',
      mime_type: 'image/png',
      avatar_base64: 'YWJjZA==',
      user: 'alice',
      session_id: 'session-1',
      data_scope: 'private'
    })
  })

  it('removes only the immutable session avatar', () => {
    expect(invoke('rex:removeProfileAvatar')).toEqual({ ok: true, profile: validProfile })
    expect(spawnedPayload()).toMatchObject({
      action: 'remove_avatar',
      user: 'alice',
      data_scope: 'private'
    })
  })

  it('returns fixed errors for process and parsing failures', () => {
    const cases: Array<{
      result?: { status: number; stdout: string; stderr: string }
      thrown?: Error
      expected: string
    }> = [
      {
        result: { status: 1, stdout: '', stderr: 'C:\\private token=secret-marker' },
        expected: 'Profile service could not complete the request.'
      },
      {
        result: { status: 0, stdout: 'not-json', stderr: '' },
        expected: 'Profile service is unavailable.'
      },
      {
        result: { status: 0, stdout: JSON.stringify({ ok: true }), stderr: '' },
        expected: 'Profile service returned an invalid response.'
      },
      {
        thrown: new Error('C:\\private secret-marker'),
        expected: 'Profile service is unavailable.'
      }
    ]

    for (const testCase of cases) {
      mockSpawnSync.mockReset()
      if (testCase.thrown) mockSpawnSync.mockImplementationOnce(() => { throw testCase.thrown })
      else mockSpawnSync.mockReturnValueOnce(testCase.result)
      const response = invoke('rex:getProfile') as { ok: boolean; error?: string }
      expect(response).toEqual({ ok: false, error: testCase.expected })
      expect(JSON.stringify(response)).not.toContain('secret-marker')
      expect(JSON.stringify(response)).not.toContain('private')
    }
  })

  it('rejects malformed or cross-user profile success payloads', () => {
    const malformed: unknown[] = [
      {},
      { ...validProfile, user_id: 'bob' },
      { ...validProfile, permissions: 'admin' },
      { ...validProfile, preferences: null }
    ]
    for (const profile of malformed) {
      mockSpawnSync.mockReturnValueOnce({
        status: 0,
        stdout: JSON.stringify({ ok: true, profile }),
        stderr: ''
      })
      expect(invoke('rex:getProfile')).toEqual({
        ok: false,
        error: 'Profile service returned an invalid response.'
      })
    }
  })
})

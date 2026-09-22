import { beforeEach, describe, expect, it, vi } from 'vitest'

const { mockSpawnSync } = vi.hoisted(() => ({
  mockSpawnSync: vi.fn()
}))

vi.mock('child_process', () => ({ spawnSync: mockSpawnSync }))
vi.mock('../src/main/bridgeResolver', () => ({
  resolvePythonCommand: () => 'python',
  resolveBridgePath: (name: string) => name,
  bridgeSpawnOptions: () => ({ cwd: '.', env: {} })
}))

import {
  AmbiguousElectronIdentityError,
  createSessionIdentity,
  privateSessionPayload,
  resolveElectronSessionIdentity,
  selectElectronSessionUser,
  sharedHouseholdPayload,
  validateSessionUserId
} from '../src/main/sessionIdentity'

function bridgeResult(stdout: Record<string, unknown>, status = 0): {
  status: number
  stdout: string
  stderr: string
} {
  return { status, stdout: JSON.stringify(stdout), stderr: '' }
}

describe('Electron session identity', () => {
  it('rejects unsafe and reserved identities', () => {
    for (const userId of ['', '../cole', 'james/cole', 'CON', 'lpt1.profile']) {
      expect(() => validateSessionUserId(userId)).toThrow()
    }
  })

  it('binds private payloads to the immutable main-process identity', () => {
    const james = createSessionIdentity('james', 'session-james')
    expect(
      privateSessionPayload(james, { command: 'list', user: 'cole' })
    ).toEqual({
      command: 'list',
      user: 'james',
      session_id: 'session-james',
      data_scope: 'private'
    })
  })

  it('marks household data as explicitly shared while preserving actor identity', () => {
    const cole = createSessionIdentity('cole', 'session-cole')
    expect(sharedHouseholdPayload(cole, { command: 'list' })).toMatchObject({
      user: 'cole',
      session_id: 'session-cole',
      data_scope: 'shared_household'
    })
  })
})

describe('Electron active-user relaunch handoff', () => {
  beforeEach(() => {
    mockSpawnSync.mockReset()
  })

  it('restores a valid persisted or sole discoverable user without any selection', () => {
    mockSpawnSync.mockReturnValueOnce(
      bridgeResult({ ok: true, user_id: 'james', authentication: 'local-os-session' })
    )
    expect(resolveElectronSessionIdentity().userId).toBe('james')
  })

  it('hands off ambiguous missing/stale identity to the user-selection flow instead of failing the launch', () => {
    mockSpawnSync.mockReturnValueOnce(
      bridgeResult(
        {
          ok: false,
          error: 'AskRex needs a user selection before it can open.',
          code: 'ambiguous_identity',
          known_user_ids: ['cole', 'james']
        },
        1
      )
    )

    let caught: unknown
    try {
      resolveElectronSessionIdentity()
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(AmbiguousElectronIdentityError)
    expect((caught as AmbiguousElectronIdentityError).knownUserIds).toEqual(['cole', 'james'])
  })

  it('persists an explicit selection from the user-selection flow', () => {
    mockSpawnSync.mockReturnValueOnce(
      bridgeResult({ ok: true, user_id: 'cole', authentication: 'local-os-session' })
    )
    expect(selectElectronSessionUser('cole').userId).toBe('cole')
    expect(mockSpawnSync).toHaveBeenCalledWith(
      'python',
      ['rex_identity_bridge.py'],
      expect.objectContaining({
        input: JSON.stringify({ action: 'select_electron_session_user', user_id: 'cole' })
      })
    )
  })

  it('still fails closed for a plain identity error with no known users to select', () => {
    mockSpawnSync.mockReturnValueOnce(
      bridgeResult({ ok: false, error: 'AskRex setup has not created a desktop user yet.' }, 1)
    )
    expect(() => resolveElectronSessionIdentity()).toThrow(
      'AskRex setup has not created a desktop user yet.'
    )
  })
})

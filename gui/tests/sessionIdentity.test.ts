import { describe, expect, it } from 'vitest'
import {
  createSessionIdentity,
  privateSessionPayload,
  sharedHouseholdPayload,
  validateSessionUserId
} from '../src/main/sessionIdentity'

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

import { EventEmitter } from 'events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { mockApp } = vi.hoisted(() => ({
  mockApp: {
    isPackaged: false,
    getAppPath: vi.fn().mockReturnValue('/fake/app'),
    getPath: vi.fn().mockReturnValue('/fake/user-data')
  }
}))

vi.mock('electron', () => ({ app: mockApp }))

class FakeChildProcess extends EventEmitter {
  stdout = new EventEmitter()
  stderr = new EventEmitter()
  stdin = { write: vi.fn(), end: vi.fn() }
}

const { mockSpawn } = vi.hoisted(() => ({ mockSpawn: vi.fn() }))
vi.mock('child_process', () => ({ spawn: mockSpawn }))

function primeSpawn(): FakeChildProcess {
  const proc = new FakeChildProcess()
  mockSpawn.mockReturnValueOnce(proc)
  return proc
}

function finish(proc: FakeChildProcess, stdout: string, code = 0): void {
  if (stdout) proc.stdout.emit('data', Buffer.from(stdout))
  proc.emit('close', code)
}

import {
  vaultDeleteSecret,
  vaultGetSecret,
  vaultHasSecret,
  vaultListEntries,
  vaultSetSecret,
  type VaultContext
} from '../src/main/credentialVault'
import type { ElectronSessionIdentity } from '../src/main/sessionIdentity'
import { bridgeSpawnOptions } from '../src/main/bridgeResolver'

const session: ElectronSessionIdentity = {
  userId: 'alice',
  sessionId: 'session-1',
  authentication: 'local-os-session'
}
const context: VaultContext = {
  scope: 'user', integration: 'email', account: 'primary', slot: 'password'
}
const ref = `cred_${'A'.repeat(32)}`

describe('credentialVault (S4)', () => {
  const originalLegacyFlag = process.env.REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK

  beforeEach(() => {
    mockSpawn.mockReset()
    mockApp.isPackaged = false
  })

  afterEach(() => {
    if (originalLegacyFlag === undefined) delete process.env.REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK
    else process.env.REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK = originalLegacyFlag
  })

  it('sends session identity and complete caller-expected context', async () => {
    const proc = primeSpawn()
    const promise = vaultHasSecret(session, ref, context)
    finish(proc, JSON.stringify({ ok: true, has: true }))
    await expect(promise).resolves.toBe(true)

    const written = JSON.parse(proc.stdin.write.mock.calls[0][0] as string)
    expect(written).toEqual({
      action: 'has',
      key: ref,
      request_user_id: 'alice',
      scope: 'user',
      integration: 'email',
      account: 'primary',
      slot: 'password'
    })
  })

  it('actively strips the legacy fallback flag from packaged bridge children', async () => {
    process.env.REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK = '1'
    mockApp.isPackaged = true
    const options = bridgeSpawnOptions()
    expect(options.env.ASKREX_PACKAGED).toBe('1')
    expect(options.env.REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK).toBeUndefined()
  })

  it('preserves explicit legacy mode only for development bridge children', async () => {
    process.env.REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK = '1'
    const proc = primeSpawn()
    const promise = vaultHasSecret(session, ref, context)
    finish(proc, JSON.stringify({ ok: true, has: false }))
    await promise

    const [, , options] = mockSpawn.mock.calls[0]
    expect(options.env.ASKREX_PACKAGED).toBe('0')
    expect(options.env.REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK).toBe('1')
  })

  it('writes a secret and requires an opaque reference response', async () => {
    const proc = primeSpawn()
    const promise = vaultSetSecret(session, 'secret-value', context)
    finish(proc, JSON.stringify({ ok: true, ref }))
    await expect(promise).resolves.toBe(ref)

    const written = JSON.parse(proc.stdin.write.mock.calls[0][0] as string)
    expect(written).toMatchObject({ action: 'set', value: 'secret-value', request_user_id: 'alice' })
    expect(written).not.toHaveProperty('key')
  })

  it('rejects a successful bridge response without an opaque reference', async () => {
    const proc = primeSpawn()
    const promise = vaultSetSecret(session, 'secret-value', context)
    finish(proc, JSON.stringify({ ok: true }))
    await expect(promise).rejects.toThrow(/opaque reference/i)
  })

  it('gets, deletes, and lists without omitting authorization context', async () => {
    const getProc = primeSpawn()
    const getPromise = vaultGetSecret(session, ref, context)
    finish(getProc, JSON.stringify({ ok: true, value: 'secret-value' }))
    await expect(getPromise).resolves.toBe('secret-value')

    const deleteProc = primeSpawn()
    const deletePromise = vaultDeleteSecret(session, ref, context)
    finish(deleteProc, JSON.stringify({ ok: true, deleted: true }))
    await expect(deletePromise).resolves.toBe(true)

    const listProc = primeSpawn()
    const listPromise = vaultListEntries(session, 'user')
    finish(listProc, JSON.stringify({ ok: true, entries: [] }))
    await expect(listPromise).resolves.toEqual([])
  })

  it('rejects ok:false, process failure, malformed output, and spawn failure', async () => {
    const bridgeError = primeSpawn()
    const rejectedSet = vaultSetSecret(session, 'secret-value', context)
    finish(bridgeError, JSON.stringify({ ok: false, error: 'vault unavailable' }), 1)
    await expect(rejectedSet).rejects.toThrow('vault unavailable')

    const processError = primeSpawn()
    const rejectedGet = vaultGetSecret(session, ref, context)
    processError.stderr.emit('data', Buffer.from('python crashed'))
    finish(processError, '', 1)
    await expect(rejectedGet).rejects.toThrow(/exited with code 1/)

    const malformed = primeSpawn()
    const rejectedMalformed = vaultGetSecret(session, ref, context)
    finish(malformed, 'not json')
    await expect(rejectedMalformed).rejects.toThrow(/parse/i)

    const spawnError = primeSpawn()
    const rejectedSpawn = vaultGetSecret(session, ref, context)
    spawnError.emit('error', new Error('ENOENT'))
    await expect(rejectedSpawn).rejects.toThrow(/Failed to start credential vault bridge/)
  })
})

import { EventEmitter } from 'events'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ElectronSessionIdentity } from '../src/main/sessionIdentity'

const { registeredHandlers, mockHandle, payloads, mockSpawn } = vi.hoisted(() => {
  const registeredHandlers = new Map<string, (...args: unknown[]) => unknown>()
  const mockHandle = vi.fn((channel: string, fn: (...args: unknown[]) => unknown) => {
    registeredHandlers.set(channel, fn)
  })
  return { registeredHandlers, mockHandle, payloads: [] as Record<string, unknown>[], mockSpawn: vi.fn() }
})

vi.mock('electron', () => ({ ipcMain: { handle: mockHandle } }))
vi.mock('child_process', () => ({ spawn: mockSpawn }))
vi.mock('../src/main/bridgeResolver', () => ({
  resolvePythonCommand: () => 'python',
  resolveBridgePath: (name: string) => `/bridge/${name}`,
  bridgeSpawnOptions: () => ({ cwd: '/runtime', env: { SAFE: '1' } })
}))

import { registerOutputRoutingHandlers } from '../src/main/handlers/outputRouting'

const session: ElectronSessionIdentity = {
  userId: 'james',
  sessionId: 'session-routing-1',
  authentication: 'local-os-session'
}

async function invoke(channel: string, ...args: unknown[]): Promise<unknown> {
  const handler = registeredHandlers.get(channel)
  if (!handler) throw new Error(`No handler registered for ${channel}`)
  return handler(null, ...args)
}

describe('output routing IPC authority', () => {
  beforeEach(() => {
    registeredHandlers.clear()
    mockHandle.mockClear()
    payloads.length = 0
    mockSpawn.mockReset().mockImplementation(() => {
      const proc = new EventEmitter() as EventEmitter & {
        stdout: EventEmitter
        stderr: EventEmitter
        stdin: { write: (raw: string) => void; end: () => void }
      }
      proc.stdout = new EventEmitter()
      proc.stderr = new EventEmitter()
      proc.stdin = {
        write: vi.fn((raw: string) => payloads.push(JSON.parse(raw))),
        end: vi.fn(() => queueMicrotask(() => {
          proc.stdout.emit('data', Buffer.from(JSON.stringify({ ok: true, policy: {} })))
          proc.emit('close', 0)
        }))
      }
      return proc
    })
    registerOutputRoutingHandlers(session)
  })

  it('registers only the routing settings operations', () => {
    expect([...registeredHandlers.keys()].sort()).toEqual([
      'rex:getOutputRoutingPolicy',
      'rex:listMediaAccounts',
      'rex:testOutputRoutingTarget',
      'rex:updateOutputRoutingPolicy'
    ])
  })

  it('binds policy reads to the immutable desktop session', async () => {
    await invoke('rex:getOutputRoutingPolicy')
    expect(payloads[0]).toEqual({
      command: 'get_policy',
      user: 'james',
      session_id: 'session-routing-1',
      data_scope: 'private'
    })
  })

  it('does not accept a renderer-supplied user identity on policy writes', async () => {
    await invoke('rex:updateOutputRoutingPolicy', { media_target_id: 'ha:media_player.kitchen' })
    expect(payloads[0]).toMatchObject({
      command: 'update_policy',
      policy: { media_target_id: 'ha:media_player.kitchen' },
      user: 'james',
      session_id: 'session-routing-1',
      data_scope: 'private'
    })
    expect(payloads[0]).not.toHaveProperty('user_id')
  })
})

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

import { registerSpeakerHandlers } from '../src/main/handlers/speakers'

const session: ElectronSessionIdentity = {
  userId: 'james',
  sessionId: 'session-media-1',
  authentication: 'local-os-session'
}

async function invoke(channel: string, ...args: unknown[]): Promise<unknown> {
  const handler = registeredHandlers.get(channel)
  if (!handler) throw new Error(`No handler registered for ${channel}`)
  return handler(null, ...args)
}

describe('canonical speaker IPC authority', () => {
  beforeEach(() => {
    registeredHandlers.clear()
    mockHandle.mockClear()
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
          proc.stdout.emit('data', Buffer.from(JSON.stringify({ ok: true, targets: [], groups: [] })))
          proc.emit('close', 0)
        }))
      }
      return proc
    })
    payloads.length = 0
    registerSpeakerHandlers(session)
  })

  it('registers canonical target and group operations', () => {
    expect([...registeredHandlers.keys()].sort()).toEqual([
      'rex:createSpeakerGroup',
      'rex:deleteSpeakerGroup',
      'rex:getAudioTargets',
      'rex:listSpeakerGroups',
      'rex:refreshAudioTargets',
      'rex:renameSpeakerGroup',
      'rex:setSpeakerGroupMembers'
    ])
  })

  it('binds requests to the immutable desktop session', async () => {
    await invoke('rex:getAudioTargets')
    expect(payloads[0]).toMatchObject({
      command: 'list_targets', user: 'james', session_id: 'session-media-1', data_scope: 'private'
    })
  })
  it('forwards only operation data, never renderer authority fields', async () => {
    await invoke('rex:createSpeakerGroup', 'Downstairs', ['ha:media_player.kitchen'])
    expect(payloads[0]).toEqual({
      command: 'create_group',
      name: 'Downstairs',
      member_ids: ['ha:media_player.kitchen'],
      user: 'james',
      session_id: 'session-media-1',
      data_scope: 'private'
    })
  })
})

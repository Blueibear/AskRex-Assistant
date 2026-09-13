import { beforeEach, describe, expect, it, vi } from 'vitest'

const { mockReadFileSync, mockSpawnSync } = vi.hoisted(() => ({
  mockReadFileSync: vi.fn(),
  mockSpawnSync: vi.fn(),
}))

vi.mock('node:fs', () => ({ readFileSync: mockReadFileSync }))
vi.mock('child_process', () => ({ spawnSync: mockSpawnSync }))
vi.mock('../src/main/bridgeResolver', () => ({
  resolvePythonCommand: () => 'C:\\AskRex\\python.exe',
  resolveRuntimeRoot: () => 'C:\\RexRuntime',
  bridgeSpawnOptions: () => ({ cwd: 'C:\\RexRuntime', env: {} }),
}))

import {
  projectBackgroundListeningStatus,
  readBackgroundListeningStatus,
  requestBackgroundListeningPaused,
  requestBackgroundVoiceRecovery,
} from '../src/main/backgroundListening'

function health(voiceState = 'ready', detailCode: string | null = null) {
  return {
    core: { state: 'ready', detail_code: null },
    voice_agent: { state: voiceState, detail_code: detailCode },
    supervisor_pid: 4242,
    observed_at: 100,
  }
}
describe('background listening status', () => {
  beforeEach(() => {
    mockReadFileSync.mockReset()
    mockSpawnSync.mockReset()
  })

  it('projects ready, paused, degraded, starting, and offline truthfully', () => {
    expect(projectBackgroundListeningStatus(health(), 102)).toEqual({
      state: 'listening',
      detailCode: null,
    })
    expect(projectBackgroundListeningStatus(health('paused', 'listening_paused'), 102).state).toBe(
      'paused',
    )
    expect(
      projectBackgroundListeningStatus(health('unavailable', 'microphone_unavailable'), 102).state,
    ).toBe('degraded')
    expect(projectBackgroundListeningStatus(health('starting'), 102).state).toBe('starting')
    expect(projectBackgroundListeningStatus(health(), 110).state).toBe('offline')
  })

  it('reads only bounded health metadata from the runtime state file', () => {
    mockReadFileSync.mockReturnValue(JSON.stringify(health('paused', 'listening_paused')))
    expect(readBackgroundListeningStatus(102).state).toBe('paused')
    expect(mockReadFileSync).toHaveBeenCalledWith(
      expect.stringContaining('background'),
      'utf8',
    )
  })

  it('routes pause and resume through the canonical background CLI', () => {
    mockSpawnSync.mockReturnValue({ status: 0 })

    expect(requestBackgroundListeningPaused(true)).toBe(true)
    expect(requestBackgroundListeningPaused(false)).toBe(true)

    expect(mockSpawnSync.mock.calls[0][1]).toEqual([
      '-m',
      'rex.background.cli',
      'pause',
      '--runtime-root',
      'C:\\RexRuntime',
    ])
    expect(mockSpawnSync.mock.calls[1][1]).toEqual([
      '-m',
      'rex.background.cli',
      'resume',
      '--runtime-root',
      'C:\\RexRuntime',
    ])
  })

  it('does not claim a pause request succeeded when the CLI fails', () => {
    mockSpawnSync.mockReturnValue({ status: 1 })
    expect(requestBackgroundListeningPaused(true)).toBe(false)
  })

  it('routes replacement-device recovery through the bounded voice-only CLI request', () => {
    mockSpawnSync.mockReturnValue({ status: 0 })

    expect(requestBackgroundVoiceRecovery()).toBe(true)
    expect(mockSpawnSync.mock.calls[0][1]).toEqual([
      '-m',
      'rex.background.cli',
      'recover-voice',
      '--runtime-root',
      'C:\\RexRuntime',
    ])
  })
})

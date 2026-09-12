import { EventEmitter } from 'events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// US-074: wake-word runtime diagnostics must survive a VoicePage unmount/remount
// while the same voice process is still running. Authority for the latest
// normalized snapshots stays in the trusted Electron main process; the renderer
// only fetches and restores them.

type Handler = (...args: unknown[]) => unknown
const ipcHandlers = new Map<string, Handler>()

vi.mock('electron', () => ({
  ipcMain: {
    handle: (channel: string, handler: Handler) => {
      ipcHandlers.set(channel, handler)
    }
  },
  BrowserWindow: {
    getAllWindows: () => []
  }
}))

vi.mock('../src/main/bridgeResolver', () => ({
  bridgeSpawnOptions: () => ({ cwd: '/repo', env: {} }),
  resolveBridgePath: (name: string) => '/repo/bridge/' + name,
  resolvePythonCommand: () => 'python'
}))

vi.mock('../src/main/handlers/logs', () => ({
  appendElectronLog: () => {}
}))

vi.mock('../src/main/sessionIdentity', () => ({
  privateSessionPayload: () => ({})
}))

class FakeProcess extends EventEmitter {
  pid = 4242
  exitCode: number | null = null
  stdout = new EventEmitter()
  stderr = new EventEmitter()
  stdin = { write: vi.fn(), end: vi.fn() }
  kill = vi.fn()
  emitLine(obj: unknown): void {
    this.stdout.emit('data', Buffer.from(JSON.stringify(obj) + '\n'))
  }
}

let fakeProcess: FakeProcess

vi.mock('child_process', () => ({
  spawn: () => fakeProcess
}))

const RUNTIME_EVENT = {
  type: 'wakeword_runtime_status',
  runtime: {
    reason: 'wakeword_listening_cycle_started',
    configured_phrase: 'rex',
    active_phrase: 'rex',
    configured_backend: 'custom_embedding',
    active_backend: 'custom_embedding',
    threshold: 0.5,
    fallback_active: false,
    fallback_phrase: 'hey jarvis',
    detector_generation: 1,
    armed: true,
    microphone_label: 'USB Microphone',
    portaudio_device_index: 4
  }
}

const ATTEMPT_EVENT = {
  type: 'wakeword_attempt_evidence',
  evidence: {
    attempt_count: 2,
    latest_confidence: 0.2,
    max_confidence: 0.3,
    threshold: 0.5,
    audio_rms: 0.001,
    audio_peak: 0.004,
    reject_reason: 'below_threshold',
    active_phrase: 'rex',
    active_backend: 'custom_embedding',
    detector_generation: 1,
    accepted: false,
    microphone_label: 'USB Microphone',
    portaudio_device_index: 4
  }
}

async function loadHandlers(): Promise<void> {
  ipcHandlers.clear()
  fakeProcess = new FakeProcess()
  vi.resetModules()
  const mod = await import('../src/main/handlers/voice')
  mod.registerVoiceHandlers({
    userId: 'tester',
    sessionId: 's1',
    authentication: 'local-os-session'
  })
}

async function startVoice(): Promise<void> {
  const start = ipcHandlers.get('rex:startVoice')!
  const promise = start({ sender: { send: () => {} } })
  fakeProcess.emitLine({ type: 'ready' })
  await promise
}

function fetchSnapshots(): Promise<{
  runtimeStatus: Record<string, unknown> | null
  attemptEvidence: Record<string, unknown> | null
}> {
  return ipcHandlers.get('rex:getWakeWordRuntimeSnapshots')!() as Promise<{
    runtimeStatus: Record<string, unknown> | null
    attemptEvidence: Record<string, unknown> | null
  }>
}

beforeEach(async () => {
  await loadHandlers()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('wake-word runtime diagnostic snapshots (main-process authority)', () => {
  it('returns null snapshots when no voice process is running', async () => {
    expect(await fetchSnapshots()).toEqual({ runtimeStatus: null, attemptEvidence: null })
  })

  it('stores the latest normalized runtime status and attempt evidence for the running process', async () => {
    await startVoice()
    fakeProcess.emitLine(RUNTIME_EVENT)
    fakeProcess.emitLine(ATTEMPT_EVENT)

    const snapshots = await fetchSnapshots()
    expect(snapshots.runtimeStatus).toMatchObject({
      reason: 'wakeword_listening_cycle_started',
      activePhrase: 'rex',
      armed: true,
      detectorGeneration: 1,
      portAudioDeviceIndex: 4
    })
    expect(snapshots.attemptEvidence).toMatchObject({
      attemptCount: 2,
      rejectReason: 'below_threshold',
      accepted: false
    })
    const serialized = JSON.stringify(snapshots)
    expect(serialized).not.toContain('tester')
    expect(serialized).not.toContain('/repo')
  })

  it('drops stale attempt evidence when the detector generation advances', async () => {
    await startVoice()
    fakeProcess.emitLine(ATTEMPT_EVENT)
    fakeProcess.emitLine({
      ...RUNTIME_EVENT,
      runtime: { ...RUNTIME_EVENT.runtime, detector_generation: 2 }
    })

    const snapshots = await fetchSnapshots()
    expect(snapshots.runtimeStatus).toMatchObject({ detectorGeneration: 2 })
    expect(snapshots.attemptEvidence).toBeNull()
  })

  it('clears snapshots when the voice process exits', async () => {
    await startVoice()
    fakeProcess.emitLine(RUNTIME_EVENT)
    expect((await fetchSnapshots()).runtimeStatus).not.toBeNull()

    fakeProcess.exitCode = 0
    fakeProcess.emit('close', 0)

    expect(await fetchSnapshots()).toEqual({ runtimeStatus: null, attemptEvidence: null })
  })

  it('clears snapshots when the voice process is stopped', async () => {
    await startVoice()
    fakeProcess.emitLine(RUNTIME_EVENT)
    await ipcHandlers.get('rex:stopVoice')!()
    expect(await fetchSnapshots()).toEqual({ runtimeStatus: null, attemptEvidence: null })
  })

  it('ignores late diagnostics and errors from a stopped process after replacement starts', async () => {
    await startVoice()
    const stoppedProcess = fakeProcess
    await ipcHandlers.get('rex:stopVoice')!()

    fakeProcess = new FakeProcess()
    await startVoice()
    fakeProcess.emitLine(RUNTIME_EVENT)
    fakeProcess.emitLine(ATTEMPT_EVENT)

    stoppedProcess.emitLine({
      ...RUNTIME_EVENT,
      runtime: { ...RUNTIME_EVENT.runtime, active_phrase: 'stale-old-process' }
    })
    stoppedProcess.emitLine({
      ...ATTEMPT_EVENT,
      evidence: { ...ATTEMPT_EVENT.evidence, detector_generation: 99 }
    })
    stoppedProcess.emit('error', new Error('late stopped-process error'))

    const snapshots = await fetchSnapshots()
    expect(snapshots.runtimeStatus).toMatchObject({
      activePhrase: 'rex',
      detectorGeneration: 1,
      armed: true
    })
    expect(snapshots.attemptEvidence).toMatchObject({ detectorGeneration: 1 })
  })
})

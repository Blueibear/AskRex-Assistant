import { describe, expect, it } from 'vitest'
import {
  getWakeWordDiagnosticMessages,
  shouldRestoreWakeWordRuntimeSnapshot,
} from '../src/types/wakeWordDiagnostics'
import type {
  WakeWordAttemptEvidence,
  WakeWordRuntimeStatus,
} from '../src/types/ipc'

const runtimeStatus: WakeWordRuntimeStatus = {
  reason: 'wakeword_listening_cycle_started',
  configuredPhrase: 'rex',
  activePhrase: 'hey jarvis',
  configuredBackend: 'custom_embedding',
  activeBackend: 'openwakeword',
  threshold: 0.5,
  fallbackActive: true,
  fallbackPhrase: 'hey jarvis',
  detectorGeneration: 2,
  armed: true,
  microphoneLabel: 'USB Microphone',
  portAudioDeviceIndex: 4,
}

const attemptEvidence: WakeWordAttemptEvidence = {
  attemptCount: 10,
  latestConfidence: 0.2,
  maxConfidence: 0.3,
  threshold: 0.5,
  audioRms: 0.001,
  audioPeak: 0.004,
  rejectReason: 'below_threshold',
  activePhrase: 'hey jarvis',
  activeBackend: 'openwakeword',
  detectorGeneration: 2,
  accepted: false,
  microphoneLabel: 'USB Microphone',
  portAudioDeviceIndex: 4,
}

describe('wake-word diagnostics', () => {
  it('explains fallback, quiet input, and below-threshold attempts', () => {
    const messages = getWakeWordDiagnosticMessages(runtimeStatus, attemptEvidence)
    const text = messages.join(' ')

    expect(text).toMatch(/fallback/i)
    expect(text).toMatch(/very quiet/i)
    expect(text).toMatch(/below.*threshold/i)
    expect(text).toContain('hey jarvis')
    expect(text).toContain('rex')
  })

  it('does not invent an active wake phrase before runtime evidence exists', () => {
    expect(getWakeWordDiagnosticMessages(null, null)).toEqual([
      'Wake detector details will appear after wake-word mode arms.',
    ])
  })

  it('reports a detector that is no longer armed', () => {
    const messages = getWakeWordDiagnosticMessages(
      { ...runtimeStatus, armed: false, reason: 'wakeword_listener_loop_exited' },
      null,
    )

    expect(messages.join(' ')).toMatch(/not currently armed/i)
  })

  it('rejects a pending snapshot after newer live runtime evidence arrives', () => {
    const requestRevision = 4
    const liveRevisionAfterEvent = 5

    expect(shouldRestoreWakeWordRuntimeSnapshot(requestRevision, liveRevisionAfterEvent)).toBe(false)
    expect(shouldRestoreWakeWordRuntimeSnapshot(requestRevision, requestRevision)).toBe(true)
  })
})

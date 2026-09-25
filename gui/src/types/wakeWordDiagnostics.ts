import type { WakeWordAttemptEvidence, WakeWordRuntimeStatus } from './ipc'

export function getWakeWordDiagnosticMessages(
  status: WakeWordRuntimeStatus | null,
  evidence: WakeWordAttemptEvidence | null,
): string[] {
  if (status === null) {
    return ['Wake detector details will appear after wake-word mode arms.']
  }

  const messages: string[] = []
  if (!status.armed) {
    messages.push(
      'Wake-word detector is not currently armed. Restart wake-word mode if you expected listening.',
    )
  }
  if (status.fallbackActive) {
    const configured = status.configuredPhrase ?? 'the configured wake word'
    const active = status.activePhrase ?? status.fallbackPhrase ?? 'the fallback wake word'
    messages.push(`Fallback is active: ${configured} is not the live detector phrase. Say ${active}.`)
  }
  if (
    evidence?.audioRms !== null &&
    evidence?.audioRms !== undefined &&
    evidence?.audioPeak !== null &&
    evidence?.audioPeak !== undefined &&
    evidence.audioRms <= 0.006 &&
    evidence.audioPeak <= 0.025
  ) {
    messages.push(
      'Microphone input is very quiet. Move closer, speak more clearly, or check the selected microphone.',
    )
  }
  if (
    evidence?.rejectReason === 'below_threshold' &&
    evidence.maxConfidence !== null &&
    evidence.threshold !== null &&
    evidence.maxConfidence < evidence.threshold
  ) {
    messages.push(
      `Wake confidence ${evidence.maxConfidence.toFixed(3)} remains below the ${evidence.threshold.toFixed(3)} threshold.`,
    )
  }
  if (status.armed && status.activePhrase === null) {
    messages.push('The detector is armed but has not reported an active wake phrase yet.')
  }
  if (messages.length === 0) {
    messages.push('Wake detector is armed and reporting normal runtime evidence.')
  }
  return messages
}
export function shouldRestoreWakeWordRuntimeSnapshot(
  requestRevision: number,
  currentRevision: number,
): boolean {
  return requestRevision === currentRevision
}

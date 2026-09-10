import { spawnSync } from 'child_process'
import { readFileSync } from 'node:fs'
import { join } from 'path'

import {
  bridgeSpawnOptions,
  resolvePythonCommand,
  resolveRuntimeRoot,
} from './bridgeResolver'

export type BackgroundListeningState =
  | 'listening'
  | 'paused'
  | 'degraded'
  | 'offline'
  | 'starting'

export interface BackgroundListeningStatus {
  state: BackgroundListeningState
  detailCode: string | null
}

const HEALTH_MAX_AGE_SECONDS = 5
const HEALTH_STATES = new Set([
  'starting',
  'ready',
  'paused',
  'degraded',
  'unavailable',
  'failed',
  'stopped',
])
const DETAIL_CODES = new Set<string | null>([
  null,
  'health_unavailable',
  'core_starting',
  'core_unavailable',
  'restart_backoff',
  'restart_limit_exceeded',
  'stop_failed',
  'microphone_unavailable',
  'speaker_unavailable',
  'wakeword_unavailable',
  'listening_paused',
])

type ComponentStatus = {
  state: string
  detailCode: string | null
}

function unavailable(detailCode = 'health_unavailable'): BackgroundListeningStatus {
  return { state: 'offline', detailCode }
}

function componentStatus(payload: unknown): ComponentStatus | null {
  if (!payload || typeof payload !== 'object') return null
  const record = payload as Record<string, unknown>
  const state = record['state']
  const detailCode = record['detail_code']
  if (typeof state !== 'string' || !HEALTH_STATES.has(state)) return null
  if ((detailCode !== null && typeof detailCode !== 'string') || !DETAIL_CODES.has(detailCode as string | null)) {
    return null
  }
  return { state, detailCode: detailCode as string | null }
}
export function projectBackgroundListeningStatus(
  payload: unknown,
  nowSeconds = Date.now() / 1000,
): BackgroundListeningStatus {
  if (!payload || typeof payload !== 'object') return unavailable()
  const record = payload as Record<string, unknown>
  const observedAt = record['observed_at']
  if (typeof observedAt !== 'number' || !Number.isFinite(observedAt)) return unavailable()
  const age = nowSeconds - observedAt
  if (age < -HEALTH_MAX_AGE_SECONDS || age > HEALTH_MAX_AGE_SECONDS) return unavailable()

  const core = componentStatus(record['core'])
  const voice = componentStatus(record['voice_agent'])
  if (!core || !voice) return unavailable()

  if (['failed', 'stopped', 'unavailable'].includes(core.state)) {
    return unavailable(core.detailCode || 'core_unavailable')
  }
  if (voice.state === 'paused' || voice.detailCode === 'listening_paused') {
    return { state: 'paused', detailCode: 'listening_paused' }
  }
  if (core.state === 'starting' || voice.state === 'starting') {
    return { state: 'starting', detailCode: voice.detailCode || core.detailCode }
  }
  if (core.state === 'ready' && voice.state === 'ready') {
    return { state: 'listening', detailCode: null }
  }
  return { state: 'degraded', detailCode: voice.detailCode || core.detailCode }
}
export function readBackgroundListeningStatus(
  nowSeconds = Date.now() / 1000,
): BackgroundListeningStatus {
  try {
    const healthPath = join(resolveRuntimeRoot(), 'background', 'health.json')
    const payload = JSON.parse(readFileSync(healthPath, 'utf8')) as unknown
    return projectBackgroundListeningStatus(payload, nowSeconds)
  } catch {
    return unavailable()
  }
}

export function requestBackgroundListeningPaused(paused: boolean): boolean {
  try {
    const result = spawnSync(
      resolvePythonCommand(),
      [
        '-m',
        'rex.background.cli',
        paused ? 'pause' : 'resume',
        '--runtime-root',
        resolveRuntimeRoot(),
      ],
      {
        ...bridgeSpawnOptions(),
        encoding: 'utf8',
        timeout: 10_000,
        windowsHide: true,
      },
    )
    return result.status === 0
  } catch {
    return false
  }
}

export function requestBackgroundVoiceRecovery(): boolean {
  try {
    const result = spawnSync(
      resolvePythonCommand(),
      [
        '-m',
        'rex.background.cli',
        'recover-voice',
        '--runtime-root',
        resolveRuntimeRoot(),
      ],
      {
        ...bridgeSpawnOptions(),
        encoding: 'utf8',
        timeout: 10_000,
        windowsHide: true,
      },
    )
    return result.status === 0
  } catch {
    return false
  }
}

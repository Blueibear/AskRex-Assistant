import { randomUUID } from 'crypto'
import { spawnSync } from 'child_process'
import { bridgeSpawnOptions, resolveBridgePath, resolvePythonCommand } from './bridgeResolver'

const USER_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/
const WINDOWS_RESERVED_NAMES = new Set([
  'con',
  'prn',
  'aux',
  'nul',
  'clock$',
  ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`)
])

export interface ElectronSessionIdentity {
  userId: string
  sessionId: string
  authentication: 'local-os-session'
}

interface IdentityBridgeResponse {
  ok: boolean
  user_id?: string
  authentication?: string
  error?: string
}

export function validateSessionUserId(userId: string): string {
  if (!USER_ID_PATTERN.test(userId) || userId === '.' || userId === '..') {
    throw new Error(`Invalid Electron session user: ${JSON.stringify(userId)}`)
  }
  const deviceStem = userId
    .replace(/[ .]+$/, '')
    .split('.', 1)[0]
    .toLowerCase()
  if (WINDOWS_RESERVED_NAMES.has(deviceStem)) {
    throw new Error(`Invalid Electron session user: ${JSON.stringify(userId)}`)
  }
  return userId
}

export function createSessionIdentity(
  userId: string,
  sessionId: string = randomUUID()
): ElectronSessionIdentity {
  return {
    userId: validateSessionUserId(userId),
    sessionId,
    authentication: 'local-os-session'
  }
}

export function resolveElectronSessionIdentity(): ElectronSessionIdentity {
  const result = spawnSync(
    resolvePythonCommand(),
    [resolveBridgePath('rex_identity_bridge.py')],
    {
      ...bridgeSpawnOptions(),
      input: JSON.stringify({ action: 'resolve_electron_session' }),
      encoding: 'utf8',
      timeout: 10_000,
      windowsHide: true
    }
  )
  let response: IdentityBridgeResponse
  try {
    response = JSON.parse(
      (result.stdout || '').trim()
    ) as IdentityBridgeResponse
  } catch {
    throw new Error(
      `Identity bridge returned an invalid response: ${(result.stderr || '').slice(0, 200)}`
    )
  }
  if (result.status !== 0 || !response.ok || !response.user_id) {
    throw new Error(
      response.error || 'Electron session identity could not be established'
    )
  }
  if (response.authentication !== 'local-os-session') {
    throw new Error(
      'Identity bridge returned an unsupported authentication method'
    )
  }
  return createSessionIdentity(response.user_id)
}

export function privateSessionPayload(
  session: ElectronSessionIdentity,
  payload: Record<string, unknown>
): Record<string, unknown> {
  return {
    ...payload,
    user: session.userId,
    session_id: session.sessionId,
    data_scope: 'private'
  }
}

export function sharedHouseholdPayload(
  session: ElectronSessionIdentity,
  payload: Record<string, unknown>
): Record<string, unknown> {
  return {
    ...payload,
    user: session.userId,
    session_id: session.sessionId,
    data_scope: 'shared_household'
  }
}

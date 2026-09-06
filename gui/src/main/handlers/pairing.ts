import { ipcMain } from 'electron'
import { spawnSync } from 'child_process'
import { bridgeSpawnOptions, resolveBridgePath, resolvePythonCommand } from '../bridgeResolver'
import { privateSessionPayload, type ElectronSessionIdentity } from '../sessionIdentity'

interface PairingBridgeResponse {
  ok: boolean
  error?: string
  challenge?: Record<string, unknown>
  requests?: Array<Record<string, unknown>>
  devices?: Array<Record<string, unknown>>
  desktop_id?: string
  grant?: Record<string, unknown>
  revoked?: boolean
}

function callPairingBridge(
  session: ElectronSessionIdentity,
  payload: Record<string, unknown>
): PairingBridgeResponse {
  try {
    const result = spawnSync(
      resolvePythonCommand(),
      [resolveBridgePath('rex_pairing_bridge.py')],
      {
        ...bridgeSpawnOptions(),
        input: JSON.stringify(
          privateSessionPayload(session, {
            ...payload,
            approver: session.userId
          })
        ),
        encoding: 'utf8',
        timeout: 10_000,
        windowsHide: true
      }
    )
    if (result.status !== 0) {
      return { ok: false, error: 'Pairing service could not complete the request.' }
    }
    const parsed = JSON.parse((result.stdout || '').trim()) as PairingBridgeResponse
    if (!parsed.ok) return { ok: false, error: 'Pairing operation failed.' }
    return parsed
  } catch {
    return { ok: false, error: 'Pairing service is unavailable.' }
  }
}

export function registerPairingHandlers(session: ElectronSessionIdentity): void {
  ipcMain.handle('rex:createPairingChallenge', (_event, scopes: string[]) =>
    callPairingBridge(session, { action: 'create_challenge', scopes })
  )
  ipcMain.handle('rex:listPendingPairings', () =>
    callPairingBridge(session, { action: 'list_pending' })
  )
  ipcMain.handle('rex:approvePairing', (_event, requestId: string) =>
    callPairingBridge(session, { action: 'approve', request_id: requestId })
  )
  ipcMain.handle('rex:denyPairing', (_event, requestId: string) =>
    callPairingBridge(session, { action: 'deny', request_id: requestId })
  )
  ipcMain.handle('rex:listPairedDevices', () =>
    callPairingBridge(session, { action: 'list_devices' })
  )
  ipcMain.handle('rex:revokePairedDevice', (_event, deviceId: string) =>
    callPairingBridge(session, { action: 'revoke', device_id: deviceId })
  )
}

import { execFileSync, spawn, spawnSync } from 'child_process'
import { app } from 'electron'
import { win32 as pathWin32 } from 'path'

import {
  bridgeSpawnOptions,
  resolvePythonCommand,
  resolvePythonwCommand,
  resolveRuntimeRoot,
} from './bridgeResolver'
import { appendElectronLog } from './handlers/logs'
import type { ElectronSessionIdentity } from './sessionIdentity'

export interface BackgroundRuntimeBootstrapState {
  attempted: boolean
  registrationOk: boolean
  launched: boolean
}

function internalArgs(command: string): string[] {
  return ['-m', 'rex.background.cli', command]
}

function resolveWindowsTokenPrincipal(): string | null {
  const systemRoot = (process.env.SystemRoot || process.env.WINDIR || '').trim()
  if (!systemRoot || !pathWin32.isAbsolute(systemRoot)) return null
  const whoami = pathWin32.join(systemRoot, 'System32', 'whoami.exe')
  try {
    const principal = execFileSync(whoami, [], {
      encoding: 'utf8',
      timeout: 5_000,
      windowsHide: true,
      shell: false,
    }).trim()
    if (
      !principal ||
      principal.length > 512 ||
      !principal.includes('\\') ||
      Array.from(principal).some((character) => {
        const code = character.charCodeAt(0)
        return code < 32 || code === 127
      })
    ) {
      return null
    }
    return principal
  } catch {
    return null
  }
}

function installStartup(identity: ElectronSessionIdentity): boolean {
  const principal = resolveWindowsTokenPrincipal()
  if (!principal) return false
  try {
    const runtimeRoot = resolveRuntimeRoot()
    const result = spawnSync(
      resolvePythonCommand(),
      [
        ...internalArgs('install-startup'),
        '--runtime-root',
        runtimeRoot,
        '--pythonw-path',
        resolvePythonwCommand(),
        '--user',
        identity.userId,
        '--packaged',
        '--run-as-user',
        principal,
      ],
      {
        ...bridgeSpawnOptions(),
        encoding: 'utf8',
        timeout: 15_000,
        windowsHide: true,
      },
    )
    return result.status === 0
  } catch {
    return false
  }
}

function runtimeIsCurrent(): boolean {
  try {
    const result = spawnSync(
      resolvePythonCommand(),
      [...internalArgs('status'), '--runtime-root', resolveRuntimeRoot()],
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

function launchDetached(identity: ElectronSessionIdentity): void {
  const options = bridgeSpawnOptions()
  const child = spawn(
    resolvePythonwCommand(),
    [
      ...internalArgs('supervisor'),
      '--runtime-root',
      resolveRuntimeRoot(),
      '--user',
      identity.userId,
      '--packaged',
    ],
    {
      ...options,
      detached: true,
      windowsHide: true,
      stdio: 'ignore',
    },
  )
  // Prevent a later asynchronous spawn error from becoming an unhandled
  // EventEmitter error after Electron has already continued startup. Keep the
  // public detail bounded rather than forwarding arbitrary exception text.
  child.once('error', () => {
    appendElectronLog('ERROR', 'Background runtime detached launch failed', {
      event: 'background_runtime_spawn_failed',
      detail_code: 'background_runtime_spawn_failed',
    })
  })
  if (child.pid === undefined) {
    throw new Error('AskRex background runtime failed to launch')
  }
  child.unref()
}

export function ensureBackgroundRuntime(
  identity: ElectronSessionIdentity,
): BackgroundRuntimeBootstrapState {
  if (!app.isPackaged || process.platform !== 'win32') {
    return { attempted: false, registrationOk: false, launched: false }
  }

  const registrationOk = installStartup(identity)
  if (runtimeIsCurrent()) {
    return { attempted: true, registrationOk, launched: false }
  }

  launchDetached(identity)
  return { attempted: true, registrationOk, launched: true }
}

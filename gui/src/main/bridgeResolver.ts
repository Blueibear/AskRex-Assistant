/**
 * Centralized bridge path resolver for Electron → Python bridge scripts.
 *
 * All Electron spawn/exec calls for bridge scripts must route through
 * resolveBridgePath() so paths are never hardcoded inline.
 *
 * Call validateBridges() at app launch to surface missing scripts early,
 * before a user action triggers a confusing "bridge exited" error.
 */

import { app } from 'electron'
import { join } from 'path'
import { existsSync } from 'fs'

// ---------------------------------------------------------------------------
// Bridge registry — maps bridge name to repo-relative script filename
// ---------------------------------------------------------------------------

const BRIDGE_REGISTRY: Record<string, string> = {
  rex_tasks_bridge: 'rex_tasks_bridge.py',
  rex_reminders_bridge: 'rex_reminders_bridge.py',
  rex_shopping_list_bridge: 'rex_shopping_list_bridge.py',
  rex_speaker_bridge: 'rex_speaker_bridge.py',
  rex_chat_bridge: 'rex_chat_bridge.py',
  rex_chat_stream_bridge: 'rex_chat_stream_bridge.py',
  rex_voices_bridge: 'rex_voices_bridge.py',
  rex_voice_enrollment_bridge: 'rex_voice_enrollment_bridge.py',
  rex_voice_sample_bridge: 'rex_voice_sample_bridge.py',
  rex_voice_upload_bridge: 'rex_voice_upload_bridge.py',
  rex_wakeword_list_bridge: 'rex_wakeword_list_bridge.py',
  rex_wakeword_train_bridge: 'rex_wakeword_train_bridge.py',
  rex_wakeword_sample_bridge: 'rex_wakeword_sample_bridge.py',
  rex_stt_bridge: 'rex_stt_bridge.py',
  rex_memories_bridge: 'rex_memories_bridge.py',
  rex_file_extract_bridge: 'rex_file_extract_bridge.py',
  rex_history_bridge: 'rex_history_bridge.py',
  rex_ha_mutation_bridge: 'rex_ha_mutation_bridge.py',
  rex_identity_bridge: 'rex_identity_bridge.py',
  rex_voice_bridge: 'rex_voice_bridge.py',
  rex_calendar_bridge: 'rex_calendar_bridge.py',
  rex_email_bridge: 'rex_email_bridge.py',
  rex_sms_bridge: 'rex_sms_bridge.py',
  rex_setup_bridge: 'rex_setup_bridge.py',
  rex_credential_vault_bridge: 'rex_credential_vault_bridge.py',
  rex_pairing_bridge: 'rex_pairing_bridge.py',
  rex_profile_bridge: 'rex_profile_bridge.py'
}

// ---------------------------------------------------------------------------
// Path resolution
// ---------------------------------------------------------------------------

/**
 * Return the absolute path to a bridge script given its filename
 * (e.g. `"rex_tasks_bridge.py"`).
 *
 * Bridge scripts live under the `bridge/` subdirectory of the repo root
 * (moved from the root in US-019).
 *
 * Path strategy:
 *   - Packaged mode (`app.isPackaged === true`): electron-builder copies bridge
 *     scripts into `extraResources`, placing them at
 *     `process.resourcesPath/bridge/<script>` — outside the .asar archive.
 *     Using `process.resourcesPath` as the base is required because
 *     `app.getAppPath()` resolves inside the archive in packaged mode, and
 *     `../bridge/` does not exist there.
 *   - Dev mode (`app.isPackaged === false`): bridge scripts are in the source
 *     tree. `app.getAppPath()` is the compiled app directory; `../bridge/`
 *     reaches the repo root where scripts live during development.
 */
export function resolveBridgePath(scriptFilename: string): string {
  if (app.isPackaged) {
    // Packaged: bridge scripts are in extraResources, outside the .asar archive.
    return join(process.resourcesPath, 'bridge', scriptFilename)
  }
  // Dev: bridge scripts are in the source tree relative to the repo root.
  return join(app.getAppPath(), '..', 'bridge', scriptFilename)
}


/** Return the writable runtime root shared by Electron and Python bridges. */
export function resolveRuntimeRoot(): string {
  return app.isPackaged ? app.getPath('userData') : join(app.getAppPath(), '..')
}

/**
 * Return the canonical CWD and path environment for every Python bridge.
 * This prevents config, profiles, data, and memory from being created beneath
 * whichever bridge directory happened to launch the process.
 */
export function bridgeSpawnOptions(): { cwd: string; env: NodeJS.ProcessEnv } {
  const runtimeRoot = resolveRuntimeRoot()
  const env = { ...process.env }
  if (app.isPackaged) {
    // Packaged production must not inherit an operator shell's legacy
    // plaintext credential escape hatch. Every managed Python child is
    // vault-only even when the parent environment contains the flag.
    delete env.REX_ALLOW_PLAINTEXT_CREDENTIAL_FALLBACK
  }
  return {
    cwd: runtimeRoot,
    env: {
      ...env,
      ASKREX_PACKAGED: app.isPackaged ? '1' : '0',
      ASKREX_RUNTIME_DIR: runtimeRoot,
      ASKREX_CONFIG_PATH: join(runtimeRoot, 'config', 'rex_config.json'),
      ASKREX_ENV_PATH: join(runtimeRoot, '.env'),
      ASKREX_PROFILES_DIR: join(runtimeRoot, 'profiles'),
      REX_DATA_DIR: join(runtimeRoot, 'data'),
      ASKREX_HOUSEHOLD_DATA_DIR: join(runtimeRoot, 'data', 'household'),
      ASKREX_USERS_DATA_DIR: join(runtimeRoot, 'data', 'users'),
      ASKREX_MEMORY_DIR: join(runtimeRoot, 'Memory')
    }
  }
}

// ---------------------------------------------------------------------------
// Python executable resolution
// ---------------------------------------------------------------------------

/** Return managed Python in packages; development may use the repo venv or PATH. */
export function resolvePythonCommand(): string {
  if (app.isPackaged) {
    const managedPython = join(process.resourcesPath, 'python', 'python.exe')
    if (!existsSync(managedPython)) {
      throw new Error(`AskRex managed Python runtime is missing: ${managedPython}`)
    }
    return managedPython
  }
  const bundledVenvPython = join(app.getAppPath(), '..', '.venv', 'Scripts', 'python.exe')
  return existsSync(bundledVenvPython) ? bundledVenvPython : 'python'
}

/** Return windowless managed Python for detached background-runtime launches. */
export function resolvePythonwCommand(): string {
  if (app.isPackaged) {
    const managedPythonw = join(process.resourcesPath, 'python', 'pythonw.exe')
    if (!existsSync(managedPythonw)) {
      throw new Error(`AskRex managed Python windowless runtime is missing: ${managedPythonw}`)
    }
    return managedPythonw
  }
  const bundledVenvPythonw = join(app.getAppPath(), '..', '.venv', 'Scripts', 'pythonw.exe')
  return existsSync(bundledVenvPythonw) ? bundledVenvPythonw : 'pythonw'
}

// ---------------------------------------------------------------------------
// Startup validation
// ---------------------------------------------------------------------------

/**
 * Validate that all registered bridge scripts exist on disk.
 * Call once at app launch (inside `app.whenReady()`).
 * Logs an error for each missing script so the developer knows exactly
 * which bridge is absent and where it should be.
 */
export function validateBridges(): void {
  // Log the bridge base directory so it can be inspected in packaged app logs.
  const bridgeBase = app.isPackaged
    ? join(process.resourcesPath, 'bridge')
    : join(app.getAppPath(), '..', 'bridge')
  console.log(`[bridgeResolver] Bridge base path: ${bridgeBase} (isPackaged=${app.isPackaged})`)

  let allPresent = true
  for (const [name, filename] of Object.entries(BRIDGE_REGISTRY)) {
    const resolvedPath = resolveBridgePath(filename)
    if (!existsSync(resolvedPath)) {
      console.error(
        `[bridgeResolver] Missing bridge script "${name}": expected at ${resolvedPath}`
      )
      allPresent = false
    }
  }
  if (allPresent) {
    console.log('[bridgeResolver] All bridge scripts validated successfully.')
  }
  if (app.isPackaged) {
    resolvePythonCommand()
    console.log('[bridgeResolver] Managed Python runtime validated successfully.')
  }
}

import { app, BrowserWindow, dialog } from 'electron'
import { electronApp, optimizer } from '@electron-toolkit/utils'
import type { Settings } from '../types/ipc'
import { createTray, destroyTray } from './tray'
import { appendElectronLog, writeElectronSessionStart } from './handlers/logs'
import { registerSetupHandlers, readSetupStatus } from './handlers/setup'
import {
  registerSetupPreviewHandlers,
  unregisterSetupPreviewHandlers
} from './handlers/setupPreview'
import { validateBridges } from './bridgeResolver'
import { applyBackgroundVoicePreference, ensureBackgroundRuntime } from './backgroundRuntime'
import { readGuiSettings } from './configStore'
import { planElectronStartup } from './firstRunStartup'
import { integrationSettingsFrom } from './integrationStatus'
import { mirrorToRexConfig } from './settingsMirror'
import { registerAuthenticatedIpcHandlers } from './ipc'
import { resolveElectronSessionIdentity } from './sessionIdentity'
import { createWindow } from './window'
import { runInstalledArtifactSmoke, runInstalledFirstRunSmoke } from './artifactSmoke'

const artifactSmokeRuntimeRoot = process.env['ASKREX_ARTIFACT_SMOKE_RUNTIME_ROOT']
if (process.env['ASKREX_ARTIFACT_SMOKE'] === '1' && artifactSmokeRuntimeRoot) {
  app.setPath('userData', artifactSmokeRuntimeRoot)
}

app.whenReady().then(async () => {
  electronApp.setAppUserModelId('com.rex-ai.rex-gui')
  writeElectronSessionStart()

  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  validateBridges()
  appendElectronLog('INFO', 'Electron bridge validation completed', {
    event: 'bridge_validation'
  })
  mirrorToRexConfig('integrations', integrationSettingsFrom(readGuiSettings()) as Settings)

  let initialSetupStatus
  try {
    initialSetupStatus = await readSetupStatus()
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    appendElectronLog('ERROR', 'Electron setup status could not be established', {
      event: 'electron_setup_status_failed',
      error: message
    })
    dialog.showErrorBox('AskRex setup is unavailable', message)
    app.quit()
    return
  }

  const startupPlan = planElectronStartup({
    needsSetup: initialSetupStatus.needs_setup,
    backgroundVoiceEnabled: initialSetupStatus.background_voice_enabled
  })

  let mainWindow: BrowserWindow | null = null
  let authenticatedBootstrapped = false

  const bootstrapAuthenticatedRuntime = (backgroundVoiceEnabled: boolean): boolean => {
    if (authenticatedBootstrapped) return true
    if (!mainWindow) return false

    let sessionIdentity
    try {
      sessionIdentity = resolveElectronSessionIdentity()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      appendElectronLog('ERROR', 'Electron user session could not be established', {
        event: 'electron_identity_failed',
        error: message
      })
      dialog.showErrorBox('AskRex needs an active user', message)
      return false
    }

    unregisterSetupPreviewHandlers()
    registerAuthenticatedIpcHandlers(mainWindow, sessionIdentity)
    authenticatedBootstrapped = true

    if (backgroundVoiceEnabled) {
      try {
        const background = ensureBackgroundRuntime(sessionIdentity)
        appendElectronLog(
          background.registrationOk ? 'INFO' : 'WARNING',
          'Background Rex runtime bootstrap completed',
          {
            event: 'background_runtime_bootstrap',
            attempted: background.attempted,
            registration_ok: background.registrationOk,
            launched: background.launched
          }
        )
      } catch {
        appendElectronLog('WARNING', 'Background Rex runtime bootstrap degraded', {
          event: 'background_runtime_bootstrap_failed',
          detail_code: 'background_runtime_bootstrap_failed'
        })
      }
    } else {
      const disabled = applyBackgroundVoicePreference(sessionIdentity, false)
      appendElectronLog(
        disabled.ok ? 'INFO' : 'WARNING',
        disabled.ok
          ? 'Background Rex runtime remains disabled by user choice'
          : 'Background Rex runtime disable reconciliation degraded',
        {
          event: disabled.ok
            ? 'background_runtime_disabled_by_user'
            : 'background_runtime_disable_reconcile_failed'
        }
      )
    }

    appendElectronLog('INFO', 'Electron user session established', {
      event: 'electron_identity_established',
      user_id: sessionIdentity.userId,
      authentication: sessionIdentity.authentication
    })
    return true
  }

  registerSetupPreviewHandlers()
  registerSetupHandlers(async () => {
    const completedStatus = await readSetupStatus()
    if (!bootstrapAuthenticatedRuntime(completedStatus.background_voice_enabled)) {
      throw new Error('Setup was saved, but the authenticated Rex runtime could not start.')
    }
    if (mainWindow) {
      createTray(mainWindow)
    }
  })

  mainWindow = createWindow()

  if (startupPlan.mode === 'setup') {
    appendElectronLog('INFO', 'Electron started in first-run setup mode', {
      event: 'electron_first_run_setup'
    })
    if (process.env['ASKREX_ARTIFACT_SMOKE_FIRST_RUN'] === '1') {
      runInstalledFirstRunSmoke(mainWindow)
    }
  } else {
    const forceArtifactBackground =
      process.env['ASKREX_ARTIFACT_SMOKE'] === '1' &&
      process.env['ASKREX_ARTIFACT_SMOKE_FORCE_BACKGROUND'] === '1'
    if (!bootstrapAuthenticatedRuntime(startupPlan.bootstrapBackground || forceArtifactBackground)) {
      app.quit()
      return
    }
    const runningArtifactSmoke = runInstalledArtifactSmoke(mainWindow)
    if (!runningArtifactSmoke) {
      createTray(mainWindow)
    }
  }

  appendElectronLog('INFO', 'Electron GUI main window created', {
    event: 'window_created'
  })

  app.on('activate', function () {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  destroyTray()
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

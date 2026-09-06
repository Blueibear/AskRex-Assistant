import { app, BrowserWindow, dialog } from 'electron'
import { electronApp, optimizer } from '@electron-toolkit/utils'
import type { Settings } from '../types/ipc'
import { createTray, destroyTray } from './tray'
import { appendElectronLog, writeElectronSessionStart } from './handlers/logs'
import { validateBridges } from './bridgeResolver'
import { ensureBackgroundRuntime } from './backgroundRuntime'
import { readGuiSettings } from './configStore'
import { integrationSettingsFrom } from './integrationStatus'
import { mirrorToRexConfig } from './settingsMirror'
import { registerIpcHandlers } from './ipc'
import { resolveElectronSessionIdentity } from './sessionIdentity'
import { createWindow } from './window'
import { runInstalledArtifactSmoke } from './artifactSmoke'

const artifactSmokeRuntimeRoot = process.env['ASKREX_ARTIFACT_SMOKE_RUNTIME_ROOT']
if (process.env['ASKREX_ARTIFACT_SMOKE'] === '1' && artifactSmokeRuntimeRoot) {
  app.setPath('userData', artifactSmokeRuntimeRoot)
}

app.whenReady().then(() => {
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
    app.quit()
    return
  }
  try {
    const background = ensureBackgroundRuntime(sessionIdentity)
    appendElectronLog(background.registrationOk ? 'INFO' : 'WARNING', 'Background Rex runtime bootstrap completed', {
      event: 'background_runtime_bootstrap',
      attempted: background.attempted,
      registration_ok: background.registrationOk,
      launched: background.launched
    })
  } catch {
    appendElectronLog('WARNING', 'Background Rex runtime bootstrap degraded', {
      event: 'background_runtime_bootstrap_failed',
      detail_code: 'background_runtime_bootstrap_failed'
    })
  }
  const mainWindow = createWindow()
  appendElectronLog('INFO', 'Electron user session established', {
    event: 'electron_identity_established',
    user_id: sessionIdentity.userId,
    authentication: sessionIdentity.authentication
  })
  registerIpcHandlers(mainWindow, sessionIdentity)
  const runningArtifactSmoke = runInstalledArtifactSmoke(mainWindow)
  if (!runningArtifactSmoke) createTray(mainWindow)
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

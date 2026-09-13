import {
  app,
  Menu,
  nativeImage,
  Tray,
  BrowserWindow,
  type MenuItemConstructorOptions,
} from 'electron'
import { join } from 'path'

import {
  readBackgroundListeningStatus,
  requestBackgroundListeningPaused,
  type BackgroundListeningState,
} from './backgroundListening'
import { readRexConfig } from './configStore'
import { appendElectronLog } from './handlers/logs'

let tray: Tray | null = null
let refreshTimer: NodeJS.Timeout | null = null
let isQuitting = false

function backgroundVoiceEnabled(): boolean {
  try {
    const config = readRexConfig()
    const runtime =
      config.runtime && typeof config.runtime === 'object'
        ? (config.runtime as Record<string, unknown>)
        : {}
    return runtime.background_voice_enabled === true
  } catch {
    return false
  }
}

/** Resolve the path to the AskRex tray icon. */
function getIconPath(): string {
  // Packed app: assets live in process.resourcesPath/assets/brand/
  // Dev:        assets live at ../../assets/brand/ (four levels up from compiled main/index.js)
  const assetsBase = app.isPackaged
    ? join(process.resourcesPath, 'assets', 'brand')
    : join(__dirname, '../../../assets', 'brand')
  return join(assetsBase, 'icon-tray-24.png')
}

const LISTENING_LABELS: Record<BackgroundListeningState, string> = {
  listening: 'Listening',
  paused: 'Paused',
  degraded: 'Degraded',
  offline: 'Offline / Unavailable',
  starting: 'Starting / Recovering',
}

function requestListeningState(paused: boolean, refresh: () => void): void {
  const accepted = requestBackgroundListeningPaused(paused)
  const action = paused ? 'pause' : 'resume'
  appendElectronLog(
    accepted ? 'INFO' : 'WARNING',
    accepted
      ? `Background listening ${action} request accepted`
      : `Background listening ${action} request failed`,
    {
      event: accepted
        ? `background_listening_${action}_requested`
        : `background_listening_${action}_request_failed`,
      requested_state: paused ? 'paused' : 'listening',
    },
  )
  refresh()
}

function buildContextMenu(
  mainWindow: BrowserWindow,
  refresh: () => void,
): Menu {
  const enabled = backgroundVoiceEnabled()
  const listening = readBackgroundListeningStatus()
  const statusLabel = enabled
    ? LISTENING_LABELS[listening.state]
    : listening.state === 'offline'
      ? 'Off'
      : listening.state === 'paused'
        ? 'Paused'
        : 'Degraded'
  const template: MenuItemConstructorOptions[] = [
    { label: `Listening status: ${statusLabel}`, enabled: false },
  ]

  if (enabled) {
    const paused = listening.state === 'paused'
    template.push({
      label: paused ? 'Resume Listening' : 'Pause Listening',
      click: () => requestListeningState(!paused, refresh),
    })
  } else if (listening.state !== 'offline' && listening.state !== 'paused') {
    template.push({
      label: 'Pause Listening',
      click: () => requestListeningState(true, refresh),
    })
  }

  template.push(
    { type: 'separator' },
    {
      label: 'Show Rex',
      click: () => {
        mainWindow.show()
        mainWindow.focus()
      },
    },
    {
      label: 'New Chat',
      click: () => {
        mainWindow.show()
        mainWindow.focus()
        mainWindow.webContents.send('rex:navigate', '/chat')
        mainWindow.webContents.send('rex:focusChatInput')
      },
    },
    {
      label: 'Toggle Voice',
      click: () => {
        mainWindow.show()
        mainWindow.focus()
        mainWindow.webContents.send('rex:toggleVoice')
      },
    },
    { type: 'separator' },
    {
      label: 'Quit Rex',
      click: () => {
        isQuitting = true
        app.quit()
      },
    },
  )
  return Menu.buildFromTemplate(template)
}

export function createTray(mainWindow: BrowserWindow): void {
  const icon = nativeImage
    .createFromPath(getIconPath())
    .resize({ width: 24, height: 24 })

  tray = new Tray(icon)
  tray.setToolTip('AskRex Assistant')
  const refresh = (): void => {
    if (tray) tray.setContextMenu(buildContextMenu(mainWindow, refresh))
  }
  refresh()

  refreshTimer = setInterval(refresh, 1_000)
  refreshTimer.unref()

  // Single-click on the tray icon restores the window
  tray.on('click', () => {
    mainWindow.show()
    mainWindow.focus()
  })

  // Mark that we are about to quit so the close handler below allows it
  app.on('before-quit', () => {
    isQuitting = true
  })

  // Intercept window close, hide to tray unless the app is quitting
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault()
      mainWindow.hide()
    }
  })
}

export function destroyTray(): void {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
  if (tray) {
    tray.destroy()
    tray = null
  }
}

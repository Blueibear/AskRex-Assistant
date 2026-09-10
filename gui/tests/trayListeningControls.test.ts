import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  app: { isPackaged: true, on: vi.fn() },
  tray: {
    setToolTip: vi.fn(),
    setContextMenu: vi.fn(),
    on: vi.fn(),
    destroy: vi.fn(),
  },
  buildFromTemplate: vi.fn((template) => template),
  createFromPath: vi.fn(() => ({ resize: vi.fn(() => ({})) })),
  readStatus: vi.fn(),
  requestPaused: vi.fn(),
  readRexConfig: vi.fn(),
  appendLog: vi.fn(),
}))

vi.mock('electron', () => ({
  app: mocks.app,
  BrowserWindow: class {},
  Menu: { buildFromTemplate: mocks.buildFromTemplate },
  nativeImage: { createFromPath: mocks.createFromPath },
  Tray: vi.fn(() => mocks.tray),
}))
vi.mock('../src/main/backgroundListening', () => ({
  readBackgroundListeningStatus: mocks.readStatus,
  requestBackgroundListeningPaused: mocks.requestPaused,
}))
vi.mock('../src/main/configStore', () => ({ readRexConfig: mocks.readRexConfig }))
vi.mock('../src/main/handlers/logs', () => ({ appendElectronLog: mocks.appendLog }))

import { createTray, destroyTray } from '../src/main/tray'
const mainWindow = {
  show: vi.fn(),
  focus: vi.fn(),
  webContents: { send: vi.fn() },
  on: vi.fn(),
} as never

function latestTemplate(): Array<Record<string, unknown>> {
  const calls = mocks.buildFromTemplate.mock.calls
  return calls[calls.length - 1][0] as Array<Record<string, unknown>>
}

function item(label: string): Record<string, unknown> {
  const found = latestTemplate().find((entry) => entry['label'] === label)
  if (!found) throw new Error(`Missing menu item: ${label}`)
  return found
}

describe('tray listening controls', () => {
  beforeEach(() => {
    destroyTray()
    vi.clearAllMocks()
    Object.defineProperty(process, 'resourcesPath', {
      value: 'C:\\AskRex\\resources',
      configurable: true,
    })
    mocks.readStatus.mockReturnValue({ state: 'listening', detailCode: null })
    mocks.requestPaused.mockReturnValue(true)
    mocks.readRexConfig.mockReturnValue({ runtime: { background_voice_enabled: true } })
  })

  it('shows live listening state and exposes Pause Listening', () => {
    createTray(mainWindow)
    expect(item('Listening status: Listening')['enabled']).toBe(false)
    expect(item('Pause Listening')).toBeTruthy()
  })

  it('shows Resume Listening only after runtime health confirms paused', () => {
    mocks.readStatus.mockReturnValue({ state: 'paused', detailCode: 'listening_paused' })
    createTray(mainWindow)
    expect(item('Listening status: Paused')['enabled']).toBe(false)
    expect(item('Resume Listening')).toBeTruthy()
  })

  it('routes pause through the canonical adapter without claiming completion', () => {
    createTray(mainWindow)
    const click = item('Pause Listening')['click'] as () => void
    click()
    expect(mocks.requestPaused).toHaveBeenCalledWith(true)
    expect(mocks.appendLog).toHaveBeenCalledWith(
      'INFO',
      'Background listening pause request accepted',
      expect.objectContaining({ event: 'background_listening_pause_requested' }),
    )
  })

  it('shows Off when disabled and runtime health confirms no active listener', () => {
    mocks.readRexConfig.mockReturnValue({ runtime: { background_voice_enabled: false } })
    mocks.readStatus.mockReturnValue({ state: 'offline', detailCode: 'health_unavailable' })
    createTray(mainWindow)
    expect(item('Listening status: Off')['enabled']).toBe(false)
    expect(latestTemplate().some((entry) => entry['label'] === 'Pause Listening')).toBe(false)
  })

  it('does not hide an unexpectedly active listener behind an Off status', () => {
    mocks.readRexConfig.mockReturnValue({ runtime: { background_voice_enabled: false } })
    mocks.readStatus.mockReturnValue({ state: 'listening', detailCode: null })
    createTray(mainWindow)
    expect(item('Listening status: Degraded')['enabled']).toBe(false)
    expect(item('Pause Listening')).toBeTruthy()
  })

  it('re-reads canonical background consent whenever the tray refreshes', () => {
    createTray(mainWindow)
    const click = item('Pause Listening')['click'] as () => void
    mocks.readRexConfig.mockReturnValue({ runtime: { background_voice_enabled: false } })

    click()

    expect(item('Listening status: Degraded')['enabled']).toBe(false)
    expect(item('Pause Listening')).toBeTruthy()
  })
})

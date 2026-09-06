import { beforeEach, describe, expect, it, vi } from 'vitest'

const {
  mockApp,
  mockSpawn,
  mockSpawnSync,
  mockExecFileSync,
  mockBridge,
  mockAppendElectronLog,
} = vi.hoisted(() => ({
  mockApp: { isPackaged: true },
  mockSpawn: vi.fn(),
  mockSpawnSync: vi.fn(),
  mockExecFileSync: vi.fn(),
  mockAppendElectronLog: vi.fn(),
  mockBridge: {
    python: 'C:\\Program Files\\AskRex\\python\\python.exe',
    pythonw: 'C:\\Program Files\\AskRex\\python\\pythonw.exe',
    runtimeRoot: 'C:\\Users\\James\\AppData\\Roaming\\AskRex',
    cwd: 'C:\\Users\\James\\AppData\\Roaming\\AskRex',
    env: {
      ASKREX_PACKAGED: '1',
      ASKREX_RUNTIME_DIR: 'C:\\Users\\James\\AppData\\Roaming\\AskRex',
    },
  },
}))

vi.mock('electron', () => ({ app: mockApp }))
vi.mock('child_process', () => ({
  execFileSync: mockExecFileSync,
  spawn: mockSpawn,
  spawnSync: mockSpawnSync,
}))
vi.mock('../src/main/handlers/logs', () => ({ appendElectronLog: mockAppendElectronLog }))
vi.mock('../src/main/bridgeResolver', () => ({
  resolvePythonCommand: () => mockBridge.python,
  resolvePythonwCommand: () => mockBridge.pythonw,
  resolveRuntimeRoot: () => mockBridge.runtimeRoot,
  bridgeSpawnOptions: () => ({
    cwd: mockBridge.cwd,
    env: { ...mockBridge.env },
  }),
}))

import { ensureBackgroundRuntime } from '../src/main/backgroundRuntime'

const identity = {
  userId: 'james',
  sessionId: 'session-1',
  authentication: 'local-os-session' as const,
}

function result(status: number): {
  status: number
  stdout: string
  stderr: string
} {
  return { status, stdout: '', stderr: '' }
}

describe('ensureBackgroundRuntime', () => {
  beforeEach(() => {
    mockApp.isPackaged = true
    process.env.SystemRoot = 'C:\\Windows'
    mockSpawn.mockReset()
    mockSpawnSync.mockReset()
    mockExecFileSync.mockReset().mockReturnValue('CONTOSO\\james\r\n')
    mockAppendElectronLog.mockReset()
    Object.defineProperty(process, 'platform', {
      value: 'win32',
      configurable: true,
    })
  })

  it('does nothing in development mode', () => {
    mockApp.isPackaged = false
    const state = ensureBackgroundRuntime(identity)
    expect(state).toEqual({
      attempted: false,
      registrationOk: false,
      launched: false,
    })
    expect(mockSpawnSync).not.toHaveBeenCalled()
    expect(mockSpawn).not.toHaveBeenCalled()
  })

  it('does nothing outside Windows', () => {
    Object.defineProperty(process, 'platform', {
      value: 'linux',
      configurable: true,
    })
    const state = ensureBackgroundRuntime(identity)
    expect(state.attempted).toBe(false)
    expect(mockSpawnSync).not.toHaveBeenCalled()
  })

  it('registers startup but does not spawn when supervisor status is current', () => {
    mockSpawnSync.mockReturnValueOnce(result(0)).mockReturnValueOnce(result(0))

    const state = ensureBackgroundRuntime(identity)

    expect(state).toEqual({
      attempted: true,
      registrationOk: true,
      launched: false,
    })
    expect(mockSpawnSync).toHaveBeenCalledTimes(2)
    expect(mockSpawnSync.mock.calls[0][0]).toBe(mockBridge.python)
    expect(mockSpawnSync.mock.calls[0][1]).toContain('install-startup')
    expect(mockSpawnSync.mock.calls[0][1]).toContain(mockBridge.pythonw)
    expect(mockSpawnSync.mock.calls[0][1]).toContain('CONTOSO\\james')
    expect(mockSpawnSync.mock.calls[1][1]).toContain('status')
    expect(mockSpawn).not.toHaveBeenCalled()
  })

  it.each([
    'CONTOSO\\james',
    'AzureAD\\james@example.com',
  ])('uses the domain-qualified Windows token principal %s for /RU', (principal) => {
    mockExecFileSync.mockReturnValue(`${principal}\r\n`)
    mockSpawnSync.mockReturnValueOnce(result(0)).mockReturnValueOnce(result(0))

    const state = ensureBackgroundRuntime(identity)

    expect(state.registrationOk).toBe(true)
    expect(mockExecFileSync).toHaveBeenCalledTimes(1)
    const [whoami, whoamiArgs, whoamiOptions] = mockExecFileSync.mock.calls[0]
    expect(String(whoami).toLowerCase()).toMatch(/\\system32\\whoami\.exe$/)
    expect(whoamiArgs).toEqual([])
    expect(whoamiOptions).toMatchObject({ encoding: 'utf8', windowsHide: true })
    const registrationArgs = mockSpawnSync.mock.calls[0][1]
    expect(registrationArgs).toContain(principal)
    expect(registrationArgs).not.toContain('James')
  })

  it('still launches now when authoritative Windows principal resolution fails', () => {
    const child = { pid: 4242, once: vi.fn(), unref: vi.fn() }
    mockExecFileSync.mockImplementation(() => {
      throw new Error('whoami unavailable')
    })
    mockSpawnSync.mockReturnValueOnce(result(1))
    mockSpawn.mockReturnValue(child)

    const state = ensureBackgroundRuntime(identity)

    expect(state).toEqual({ attempted: true, registrationOk: false, launched: true })
    expect(mockSpawnSync).toHaveBeenCalledTimes(1)
    expect(mockSpawnSync.mock.calls[0][1]).toContain('status')
    expect(child.unref).toHaveBeenCalledTimes(1)
  })

  it('treats a status process exception as unavailable and launches detached', () => {
    const child = { pid: 4242, once: vi.fn(), unref: vi.fn() }
    mockSpawnSync
      .mockReturnValueOnce(result(0))
      .mockImplementationOnce(() => {
        throw new Error('status failed')
      })
    mockSpawn.mockReturnValue(child)

    const state = ensureBackgroundRuntime(identity)

    expect(state).toEqual({ attempted: true, registrationOk: true, launched: true })
    expect(mockSpawn).toHaveBeenCalledTimes(1)
    expect(child.unref).toHaveBeenCalledTimes(1)
  })

  it('launches supervisor detached when status is unavailable', () => {
    const child = { pid: 4242, once: vi.fn(), unref: vi.fn() }
    mockSpawnSync.mockReturnValueOnce(result(0)).mockReturnValueOnce(result(1))
    mockSpawn.mockReturnValue(child)

    const state = ensureBackgroundRuntime(identity)

    expect(state).toEqual({
      attempted: true,
      registrationOk: true,
      launched: true,
    })
    expect(mockSpawn).toHaveBeenCalledTimes(1)
    const [command, args, options] = mockSpawn.mock.calls[0]
    expect(command).toBe(mockBridge.pythonw)
    expect(args).toEqual([
      '-m',
      'rex.background.cli',
      'supervisor',
      '--runtime-root',
      mockBridge.runtimeRoot,
      '--user',
      identity.userId,
      '--packaged',
    ])
    expect(options).toMatchObject({
      cwd: mockBridge.cwd,
      env: mockBridge.env,
      detached: true,
      windowsHide: true,
      stdio: 'ignore',
    })
    expect(child.unref).toHaveBeenCalledTimes(1)
  })

  it('still launches now when scheduler registration fails', () => {
    const child = { pid: 4242, once: vi.fn(), unref: vi.fn() }
    mockSpawnSync.mockReturnValueOnce(result(1)).mockReturnValueOnce(result(1))
    mockSpawn.mockReturnValue(child)

    const state = ensureBackgroundRuntime(identity)

    expect(state).toEqual({
      attempted: true,
      registrationOk: false,
      launched: true,
    })
    expect(mockSpawn).toHaveBeenCalledTimes(1)
    expect(child.unref).toHaveBeenCalledTimes(1)
  })

  it('rejects a detached child that failed before receiving a pid', () => {
    const child = { pid: undefined, once: vi.fn(), unref: vi.fn() }
    mockSpawnSync.mockReturnValueOnce(result(0)).mockReturnValueOnce(result(1))
    mockSpawn.mockReturnValue(child)

    expect(() => ensureBackgroundRuntime(identity)).toThrow(
      'AskRex background runtime failed to launch',
    )
    expect(child.once).toHaveBeenCalledWith('error', expect.any(Function))
    expect(child.unref).not.toHaveBeenCalled()
  })

  it('logs a later asynchronous detached spawn error', () => {
    let errorListener: ((error: Error) => void) | undefined
    const child = {
      pid: 4242,
      once: vi.fn((event: string, listener: (error: Error) => void) => {
        if (event === 'error') errorListener = listener
        return child
      }),
      unref: vi.fn(),
    }
    mockSpawnSync.mockReturnValueOnce(result(0)).mockReturnValueOnce(result(1))
    mockSpawn.mockReturnValue(child)

    expect(ensureBackgroundRuntime(identity).launched).toBe(true)
    expect(errorListener).toBeTypeOf('function')
    errorListener?.(new Error('CreateProcess failed'))
    expect(mockAppendElectronLog).toHaveBeenCalledWith(
      'ERROR',
      'Background runtime detached launch failed',
      expect.objectContaining({ event: 'background_runtime_spawn_failed' }),
    )
  })
  it('surfaces a detached spawn failure to the GUI bootstrap caller', () => {
    mockSpawnSync.mockReturnValueOnce(result(0)).mockReturnValueOnce(result(1))
    mockSpawn.mockImplementation(() => {
      throw new Error('spawn failed')
    })

    expect(() => ensureBackgroundRuntime(identity)).toThrow('spawn failed')
  })
})

describe('Electron background runtime ownership', () => {
  it('bootstraps only after the authenticated Electron session is resolved', async () => {
    const { readFileSync } = await import('node:fs')
    const source = readFileSync(
      new URL('../src/main/index.ts', import.meta.url),
      'utf8',
    )
    const identityIndex = source.indexOf('resolveElectronSessionIdentity()')
    const bootstrapIndex = source.indexOf(
      'ensureBackgroundRuntime(sessionIdentity)',
    )
    const windowIndex = source.indexOf('createWindow()')

    expect(source).toContain("from './backgroundRuntime'")
    expect(identityIndex).toBeGreaterThan(-1)
    expect(bootstrapIndex).toBeGreaterThan(identityIndex)
    expect(windowIndex).toBeGreaterThan(bootstrapIndex)
  })

  it('keeps tray close and explicit GUI quit independent of runtime stop', async () => {
    const { readFileSync } = await import('node:fs')
    const source = readFileSync(
      new URL('../src/main/tray.ts', import.meta.url),
      'utf8',
    )

    expect(source).toContain("label: 'Quit Rex'")
    expect(source).toContain('app.quit()')
    expect(source).toContain('event.preventDefault()')
    expect(source).toContain('mainWindow.hide()')
    expect(source).not.toContain('rex.background.cli')
    expect(source).not.toContain('background stop')
  })
})

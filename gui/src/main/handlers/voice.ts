import { BrowserWindow, ipcMain } from 'electron'
import { spawn } from 'child_process'
import type { ChildProcess } from 'child_process'
import { bridgeSpawnOptions, resolveBridgePath, resolvePythonCommand } from '../bridgeResolver'
import { appendElectronLog } from './logs'
import { privateSessionPayload, type ElectronSessionIdentity } from '../sessionIdentity'

let voiceProcess: ChildProcess | null = null
let currentVoiceState = 'idle'

// Latest normalized wake-word diagnostics for the currently running voice
// process. Authority stays in this trusted main process: these are content-free
// projections (no transcripts, prompts, paths, or identity) that let VoicePage
// restore its panel after an unmount/remount while the same process is alive.
// They are cleared whenever the voice process stops, restarts, or exits.
let latestWakeWordRuntimeStatus: Record<string, unknown> | null = null
let latestWakeWordAttemptEvidence: Record<string, unknown> | null = null

function clearWakeWordDiagnosticSnapshots(): void {
  latestWakeWordRuntimeStatus = null
  latestWakeWordAttemptEvidence = null
}

type BridgeResult<T> = T & { ok: boolean; error?: string }
type VoiceStartOptions = { microphoneLabel?: string }
type VoiceBridgeEvent = {
  type: string
  state?: string
  text?: string
  role?: string
  timestamp?: number
  error?: string
  status?: string
  turn_id?: string
  sequence?: number
  terminal?: boolean
  level?: string
  message?: string
  extra?: Record<string, unknown>
  traceback?: string
  code?: string
  device_kind?: string
  runtime?: Record<string, unknown>
  evidence?: Record<string, unknown>
}
type VoiceBridgeEventContext = {
  process: ChildProcess
  settleStartup: (result: { ok: boolean; error?: string }) => void
  failStartup: (error: string) => void
  setStartupStatus: (status: string) => void
}
type VoiceBridgeEventHandler = (
  event: VoiceBridgeEvent,
  context: VoiceBridgeEventContext
) => void

function normalizeMicrophoneLabel(options: VoiceStartOptions | undefined): string | undefined {
  const label = typeof options?.microphoneLabel === 'string' ? options.microphoneLabel.trim() : ''
  return label ? label.slice(0, 256) : undefined
}

function resolveBridgeScript(scriptName: string): string {
  return resolveBridgePath(scriptName)
}

function normalizeBridgeVoiceState(state: string): string {
  if (state === 'thinking' || state === 'executing') return 'processing'
  if (state === 'done') return 'idle'
  return state
}

function formatVoiceStatus(status: string): string {
  return status.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

function boundedString(value: unknown, maxLength = 256): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed ? trimmed.slice(0, maxLength) : null
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function integer(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isInteger(value) ? value : fallback
}

function normalizeWakeWordRuntimeStatus(payload: unknown): Record<string, unknown> | null {
  if (typeof payload !== 'object' || payload === null) return null
  const value = payload as Record<string, unknown>
  return {
    reason: boundedString(value.reason, 128) ?? 'wakeword_runtime_update',
    configuredPhrase: boundedString(value.configured_phrase, 128),
    activePhrase: boundedString(value.active_phrase, 128),
    configuredBackend: boundedString(value.configured_backend, 64),
    activeBackend: boundedString(value.active_backend, 64),
    threshold: finiteNumber(value.threshold),
    fallbackActive: value.fallback_active === true,
    fallbackPhrase: boundedString(value.fallback_phrase, 128),
    detectorGeneration: integer(value.detector_generation, 1),
    armed: value.armed === true,
    microphoneLabel: boundedString(value.microphone_label, 256),
    portAudioDeviceIndex: finiteNumber(value.portaudio_device_index)
  }
}

function normalizeWakeWordAttemptEvidence(payload: unknown): Record<string, unknown> | null {
  if (typeof payload !== 'object' || payload === null) return null
  const value = payload as Record<string, unknown>
  const attemptCount = integer(value.attempt_count, -1)
  if (attemptCount < 0) return null
  return {
    attemptCount,
    latestConfidence: finiteNumber(value.latest_confidence),
    maxConfidence: finiteNumber(value.max_confidence),
    threshold: finiteNumber(value.threshold),
    audioRms: finiteNumber(value.audio_rms),
    audioPeak: finiteNumber(value.audio_peak),
    rejectReason: boundedString(value.reject_reason, 128),
    activePhrase: boundedString(value.active_phrase, 128),
    activeBackend: boundedString(value.active_backend, 64),
    detectorGeneration: integer(value.detector_generation, 1),
    accepted: value.accepted === true,
    microphoneLabel: boundedString(value.microphone_label, 256),
    portAudioDeviceIndex: finiteNumber(value.portaudio_device_index)
  }
}

function broadcastVoiceEvent(channel: string, data: unknown): void {
  for (const window of BrowserWindow.getAllWindows()) {
    const contents = window.webContents
    if (!contents.isDestroyed()) {
      contents.send(channel, data)
    }
  }
}

function setVoiceState(state: string): void {
  currentVoiceState = state
  broadcastVoiceEvent('rex:voiceState', { state })
}

function parseVoiceBridgeEvent(line: string): VoiceBridgeEvent | null {
  try {
    const parsed = JSON.parse(line) as unknown
    if (typeof parsed !== 'object' || parsed === null) return null
    const event = parsed as VoiceBridgeEvent
    return typeof event.type === 'string' ? event : null
  } catch {
    return null
  }
}

function handleVoiceReadyEvent(
  _event: VoiceBridgeEvent,
  context: VoiceBridgeEventContext
): void {
  appendElectronLog('INFO', 'GUI voice bridge reported wake listener ready', {
    event: 'voice_bridge_ready',
    pid: context.process.pid
  })
  context.settleStartup({ ok: true })
}

function handleVoiceStatusEvent(
  event: VoiceBridgeEvent,
  context: VoiceBridgeEventContext
): void {
  if (!event.status) return
  context.setStartupStatus(event.status.replace(/_/g, ' '))
  appendElectronLog('DEBUG', 'GUI voice bridge status', {
    event: 'voice_bridge_status',
    status: event.status,
    pid: context.process.pid
  })
  broadcastVoiceEvent('rex:voiceStatus', {
    status: event.status,
    label: formatVoiceStatus(event.status)
  })
}

function handleVoiceTurnStatusEvent(event: VoiceBridgeEvent): void {
  if (
    !event.status ||
    typeof event.turn_id !== 'string' ||
    typeof event.sequence !== 'number' ||
    typeof event.terminal !== 'boolean'
  ) {
    return
  }
  broadcastVoiceEvent('rex:turnStatus', {
    turn_id: event.turn_id,
    sequence: event.sequence,
    status: event.status,
    terminal: event.terminal
  })
}

function handleWakeWordRuntimeStatusEvent(
  event: VoiceBridgeEvent,
  context: VoiceBridgeEventContext
): void {
  if (voiceProcess !== context.process) return
  const status = normalizeWakeWordRuntimeStatus(event.runtime)
  if (!status) return
  latestWakeWordRuntimeStatus = status
  // Attempt evidence from an older detector generation is stale once the
  // runtime reports a rebuild/fallback; mirror the renderer's own guard.
  if (
    latestWakeWordAttemptEvidence &&
    latestWakeWordAttemptEvidence.detectorGeneration !== status.detectorGeneration
  ) {
    latestWakeWordAttemptEvidence = null
  }
  appendElectronLog('DEBUG', 'GUI wake-word runtime status', {
    event: 'wakeword_runtime_status',
    reason: status.reason,
    active_backend: status.activeBackend,
    fallback_active: status.fallbackActive,
    detector_generation: status.detectorGeneration,
    armed: status.armed,
    portaudio_device_index: status.portAudioDeviceIndex
  })
  broadcastVoiceEvent('rex:wakeWordRuntimeStatus', status)
}

function handleWakeWordAttemptEvidenceEvent(
  event: VoiceBridgeEvent,
  context: VoiceBridgeEventContext
): void {
  if (voiceProcess !== context.process) return
  const evidence = normalizeWakeWordAttemptEvidence(event.evidence)
  if (!evidence) return
  latestWakeWordAttemptEvidence = evidence
  appendElectronLog('DEBUG', 'GUI wake-word attempt evidence', {
    event: 'wakeword_attempt_evidence',
    attempt_count: evidence.attemptCount,
    latest_confidence: evidence.latestConfidence,
    max_confidence: evidence.maxConfidence,
    threshold: evidence.threshold,
    accepted: evidence.accepted,
    reject_reason: evidence.rejectReason,
    detector_generation: evidence.detectorGeneration
  })
  broadcastVoiceEvent('rex:wakeWordAttemptEvidence', evidence)
}

function handleVoiceLogEvent(event: VoiceBridgeEvent, context: VoiceBridgeEventContext): void {
  appendElectronLog(event.level ?? 'INFO', event.message ?? 'GUI voice bridge log', {
    ...(event.extra ?? {}),
    source_logger: 'rex_voice_bridge',
    pid: context.process.pid
  })
}

function handleVoiceStateEvent(
  event: VoiceBridgeEvent,
  context: VoiceBridgeEventContext
): void {
  if (!event.state) return
  const normalizedState = normalizeBridgeVoiceState(event.state)
  setVoiceState(normalizedState)
  appendElectronLog('DEBUG', 'GUI voice bridge state', {
    event: 'voice_bridge_state',
    state: normalizedState,
    raw_state: event.state,
    pid: context.process.pid
  })
  if (normalizedState === 'wake_listening') {
    appendElectronLog('INFO', 'GUI voice bridge wake listen acknowledged', {
      event: 'voice_listen_acknowledged',
      pid: context.process.pid
    })
  }
}

function handleVoiceTranscriptEvent(
  event: VoiceBridgeEvent,
  context: VoiceBridgeEventContext
): void {
  const transcript = {
    text: event.text ?? '',
    role: event.role ?? 'rex',
    timestamp: event.timestamp ?? Date.now()
  }
  appendElectronLog('INFO', 'GUI voice bridge transcript event', {
    event: 'voice_bridge_transcript',
    ...transcript,
    pid: context.process.pid
  })
  broadcastVoiceEvent('rex:voiceTranscript', transcript)
}

function handleVoiceErrorEvent(event: VoiceBridgeEvent, context: VoiceBridgeEventContext): void {
  const error = event.error ?? 'Unknown voice error'
  appendElectronLog('ERROR', 'GUI voice bridge error event', {
    event: 'voice_bridge_error',
    error,
    code: event.code,
    device_kind: event.device_kind,
    traceback: event.traceback,
    pid: context.process.pid
  })
  context.failStartup(error)
}

const VOICE_BRIDGE_EVENT_HANDLERS: Record<string, VoiceBridgeEventHandler> = {
  ready: handleVoiceReadyEvent,
  status: handleVoiceStatusEvent,
  turn_status: handleVoiceTurnStatusEvent,
  wakeword_runtime_status: handleWakeWordRuntimeStatusEvent,
  wakeword_attempt_evidence: handleWakeWordAttemptEvidenceEvent,
  log: handleVoiceLogEvent,
  state: handleVoiceStateEvent,
  transcript: handleVoiceTranscriptEvent,
  error: handleVoiceErrorEvent
}

function dispatchVoiceBridgeEvent(
  event: VoiceBridgeEvent,
  context: VoiceBridgeEventContext
): void {
  VOICE_BRIDGE_EVENT_HANDLERS[event.type]?.(event, context)
}

function killVoiceProcess(): void {
  const py = voiceProcess
  voiceProcess = null
  clearWakeWordDiagnosticSnapshots()
  setVoiceState('idle')
  if (py) {
    appendElectronLog('INFO', 'Stopping GUI voice bridge process', {
      event: 'voice_bridge_stop_requested',
      pid: py.pid
    })
    try {
      py.stdin?.write(JSON.stringify({ command: 'stop' }) + '\n')
      py.stdin?.end()
    } catch {
      py.kill()
    }
  }
}

export function registerVoiceHandlers(session: ElectronSessionIdentity): void {
  ipcMain.handle(
    'rex:startVoice',
    async (event, options?: VoiceStartOptions): Promise<{ ok: boolean; error?: string }> => {
    if (voiceProcess) {
      const existingState = getCurrentVoiceState()
      if (existingState !== 'idle' && existingState !== 'error') {
        appendElectronLog('INFO', 'GUI wake listen request reused existing voice bridge process', {
          event: 'voice_listen_reused',
          pid: voiceProcess.pid,
          state: existingState
        })
        event.sender.send('rex:voiceState', { state: existingState })
        return { ok: true }
      }
      appendElectronLog('WARNING', 'Restarting unarmed GUI voice bridge process', {
        event: 'voice_bridge_restart_unarmed',
        pid: voiceProcess.pid,
        state: existingState
      })
      killVoiceProcess()
    }

    const scriptPath = resolveBridgeScript('rex_voice_bridge.py')
    const voiceBridgeOptions = bridgeSpawnOptions()
    const bridgeCwd = voiceBridgeOptions.cwd
    const microphoneLabel = normalizeMicrophoneLabel(options)
    appendElectronLog('INFO', 'Starting GUI voice bridge process', {
      event: 'voice_bridge_start_requested',
      script_path: scriptPath,
      cwd: bridgeCwd,
      microphone_label: microphoneLabel ?? null
    })
    appendElectronLog('INFO', 'GUI wake listen requested by renderer', {
      event: 'voice_listen_requested',
      script_path: scriptPath,
      cwd: bridgeCwd,
      microphone_label: microphoneLabel ?? null
    })

    const bridgeArgs = [scriptPath, '--user', session.userId]
    if (microphoneLabel) {
      bridgeArgs.push('--microphone-label', microphoneLabel)
    }
    const py = spawn(resolvePythonCommand(), bridgeArgs, {
      ...voiceBridgeOptions,
      stdio: ['pipe', 'pipe', 'pipe']
    })

    voiceProcess = py
    clearWakeWordDiagnosticSnapshots()
    setVoiceState('starting')
    appendElectronLog('INFO', 'GUI voice bridge process spawned', {
      event: 'voice_bridge_spawned',
      pid: py.pid,
      script_path: scriptPath,
      cwd: bridgeCwd
    })

    return new Promise((resolve) => {
      let startupSettled = false
      let stderr = ''
      let startupStatus = 'starting bridge process'

      function settleStartup(result: { ok: boolean; error?: string }): void {
        if (startupSettled) return
        startupSettled = true
        clearTimeout(startupTimer)
        appendElectronLog(result.ok ? 'INFO' : 'ERROR', 'GUI voice bridge startup settled', {
          event: 'voice_bridge_startup_settled',
          ok: result.ok,
          error: result.error,
          pid: py.pid
        })
        resolve(result)
      }

      function failStartup(error: string): void {
        broadcastVoiceEvent('rex:voiceError', { error })
        if (!startupSettled) {
          appendElectronLog('ERROR', 'GUI voice bridge startup failed', {
            event: 'voice_bridge_startup_failed',
            error,
            pid: py.pid
          })
          if (voiceProcess === py) {
            voiceProcess = null
            clearWakeWordDiagnosticSnapshots()
            setVoiceState('error')
          }
          try {
            py.kill()
          } catch {
            // Process may already be gone.
          }
          settleStartup({ ok: false, error })
        }
      }

      const startupTimer = setTimeout(() => {
        const detail = stderr.trim() ? ` Last stderr: ${stderr.trim().slice(-500)}` : ''
        failStartup(`Voice bridge did not become ready within 90 seconds while ${startupStatus}.${detail}`)
      }, 90_000)

      let lineBuffer = ''

      const bridgeEventContext: VoiceBridgeEventContext = {
        process: py,
        settleStartup,
        failStartup,
        setStartupStatus: (status) => {
          startupStatus = status
        }
      }

      py.stdout.on('data', (chunk: Buffer) => {
        lineBuffer += chunk.toString()
        const lines = lineBuffer.split('\n')
        lineBuffer = lines.pop() ?? ''
        for (const line of lines) {
          const event = parseVoiceBridgeEvent(line.trim())
          if (event) dispatchVoiceBridgeEvent(event, bridgeEventContext)
        }
      })

      py.stderr.on('data', (chunk: Buffer) => {
        stderr += chunk.toString()
      })

      py.on('close', (code) => {
        appendElectronLog('INFO', 'GUI voice bridge process closed', {
          event: 'voice_bridge_closed',
          code,
          pid: py.pid
        })
        if (voiceProcess === py) {
          voiceProcess = null
          clearWakeWordDiagnosticSnapshots()
          setVoiceState('idle')
        }
        if (!startupSettled) {
          const detail = stderr.trim() ? `: ${stderr.trim().slice(-500)}` : ''
          settleStartup({
            ok: false,
            error: `Voice bridge exited before wake-word mode was ready (code ${code ?? 'unknown'})${detail}`
          })
        }
      })

      py.on('error', (err) => {
        appendElectronLog('ERROR', 'GUI voice bridge process failed to spawn', {
          event: 'voice_bridge_spawn_error',
          error: err.message,
          pid: py.pid
        })
        if (voiceProcess === py) {
          voiceProcess = null
          clearWakeWordDiagnosticSnapshots()
          setVoiceState('error')
        }
        failStartup(`Failed to start voice bridge: ${err.message}`)
      })
    })
  })

  ipcMain.handle('rex:stopVoice', async (): Promise<{ ok: boolean }> => {
    killVoiceProcess()
    return { ok: true }
  })

  // Trusted snapshot fetch so VoicePage can restore its diagnostics panel after
  // an unmount/remount while the same voice process is still running. Returns
  // null snapshots when no voice process is active.
  ipcMain.handle(
    'rex:getWakeWordRuntimeSnapshots',
    async (): Promise<{
      runtimeStatus: Record<string, unknown> | null
      attemptEvidence: Record<string, unknown> | null
    }> => {
      if (!voiceProcess || voiceProcess.exitCode !== null) {
        return { runtimeStatus: null, attemptEvidence: null }
      }
      return {
        runtimeStatus: latestWakeWordRuntimeStatus,
        attemptEvidence: latestWakeWordAttemptEvidence
      }
    }
  )

  ipcMain.handle('rex:listVoices', async (_event, provider: string): Promise<{ ok: boolean; voices: unknown[]; error?: string }> => {
    const scriptPath = resolveBridgeScript('rex_voices_bridge.py')
    return new Promise((resolve) => {
      const py = spawn(resolvePythonCommand(), [scriptPath], {
        ...bridgeSpawnOptions(),
        stdio: ['pipe', 'pipe', 'pipe']
      })
      let stdout = ''
      let stderr = ''
      py.stdout.on('data', (chunk: Buffer) => {
        stdout += chunk.toString()
      })
      py.stderr.on('data', (chunk: Buffer) => {
        stderr += chunk.toString()
      })
      py.on('close', (code) => {
        if (code !== 0 && stdout.trim() === '') {
          resolve({
            ok: false,
            voices: [],
            error: stderr || `Bridge exited with code ${code}`
          })
          return
        }
        try {
          const result = JSON.parse(stdout.trim()) as {
            ok: boolean
            voices?: unknown[]
            error?: string
          }
          resolve({
            ok: result.ok,
            voices: result.voices ?? [],
            error: result.error
          })
        } catch {
          resolve({
            ok: false,
            voices: [],
            error: stderr || 'Failed to parse response'
          })
        }
      })
      py.on('error', (err) => {
        resolve({
          ok: false,
          voices: [],
          error: `Failed to start bridge: ${err.message}`
        })
      })
      py.stdin?.write(JSON.stringify({ provider }) + '\n')
      py.stdin?.end()
    })
  })

  ipcMain.handle('rex:previewVoice', async (_event, provider: string, voiceId: string): Promise<{ ok: boolean; audio_base64?: string; error?: string }> => {
    const scriptPath = resolveBridgeScript('rex_voice_sample_bridge.py')
    return new Promise((resolve) => {
      const py = spawn(resolvePythonCommand(), [scriptPath], {
        ...bridgeSpawnOptions(),
        stdio: ['pipe', 'pipe', 'pipe']
      })
      let stdout = ''
      let stderr = ''
      py.stdout.on('data', (chunk: Buffer) => {
        stdout += chunk.toString()
      })
      py.stderr.on('data', (chunk: Buffer) => {
        stderr += chunk.toString()
      })
      py.on('close', (code) => {
        if (code !== 0 && stdout.trim() === '') {
          resolve({
            ok: false,
            error: stderr || `Bridge exited with code ${code}`
          })
          return
        }
        try {
          const result = JSON.parse(stdout.trim()) as {
            ok: boolean
            audio_base64?: string
            error?: string
          }
          resolve(result)
        } catch {
          resolve({ ok: false, error: stderr || 'Failed to parse response' })
        }
      })
      py.on('error', (err) => {
        resolve({
          ok: false,
          error: `Failed to start bridge: ${err.message}`
        })
      })
      py.stdin?.write(JSON.stringify({ provider, voice_id: voiceId }) + '\n')
      py.stdin?.end()
    })
  })

  ipcMain.handle(
    'rex:synthesizeSpeech',
    async (
      _event,
      provider: string,
      voiceId: string,
      text: string
    ): Promise<{ ok: boolean; audio_base64?: string; error?: string }> => {
      const scriptPath = resolveBridgeScript('rex_voice_sample_bridge.py')
      return new Promise((resolve) => {
        const py = spawn(resolvePythonCommand(), [scriptPath], {
          ...bridgeSpawnOptions(),
          stdio: ['pipe', 'pipe', 'pipe']
        })
        let stdout = ''
        let stderr = ''
        py.stdout.on('data', (chunk: Buffer) => {
          stdout += chunk.toString()
        })
        py.stderr.on('data', (chunk: Buffer) => {
          stderr += chunk.toString()
        })
        py.on('close', (code) => {
          try {
            const result = JSON.parse(stdout.trim()) as {
              ok: boolean
              audio_base64?: string
              error?: string
            }
            resolve(result)
          } catch {
            resolve({
              ok: false,
              error: stderr || `Speech bridge exited with code ${code}`
            })
          }
        })
        py.on('error', (error) => {
          resolve({ ok: false, error: `Failed to start speech bridge: ${error.message}` })
        })
        py.stdin?.write(JSON.stringify({ provider, voice_id: voiceId, text, mode: 'response' }))
        py.stdin?.end()
      })
    }
  )

  ipcMain.handle(
    'rex:logVoiceTiming',
    (_event, turnId: string, stage: string, durationMs: number): { ok: boolean } => {
      appendElectronLog('INFO', 'Hold-to-Talk stage completed', {
        event: 'hold_to_talk_stage_timing',
        turn_id: turnId,
        stage,
        duration_ms: Math.max(0, Math.round(durationMs))
      })
      return { ok: true }
    }
  )

  ipcMain.handle(
    'rex:listWakeWords',
    async (): Promise<{
      ok: boolean
      wake_words: unknown[]
      error?: string
      warning?: string
    }> => {
      const scriptPath = resolveBridgeScript('rex_wakeword_list_bridge.py')
      return new Promise((resolve) => {
        const py = spawn(resolvePythonCommand(), [scriptPath], {
          ...bridgeSpawnOptions(),
          stdio: ['pipe', 'pipe', 'pipe']
        })
        let stdout = ''
        let stderr = ''
        py.stdout.on('data', (chunk: Buffer) => {
          stdout += chunk.toString()
        })
        py.stderr.on('data', (chunk: Buffer) => {
          stderr += chunk.toString()
        })
        py.on('close', (code) => {
          if (code !== 0 && stdout.trim() === '') {
            resolve({
              ok: false,
              wake_words: [],
              error: stderr || `Bridge exited with code ${code}`
            })
            return
          }
          try {
            const result = JSON.parse(stdout.trim()) as {
              ok: boolean
              wake_words?: unknown[]
              error?: string
              warning?: string
            }
            resolve({
              ok: result.ok,
              wake_words: result.wake_words ?? [],
              error: result.error,
              warning: result.warning
            })
          } catch {
            resolve({
              ok: false,
              wake_words: [],
              error: stderr || 'Failed to parse response'
            })
          }
        })
        py.on('error', (err) => {
          resolve({
            ok: false,
            wake_words: [],
            error: `Failed to start bridge: ${err.message}`
          })
        })
        py.stdin?.write('{}')
        py.stdin?.end()
      })
    }
  )

  ipcMain.handle(
    'rex:uploadCustomVoice',
    async (
      _event,
      filePath: string,
      voiceName: string
    ): Promise<{
      ok: boolean
      voice_id?: string
      voice_name?: string
      duration?: number
      error?: string
    }> => {
      const scriptPath = resolveBridgeScript('rex_voice_upload_bridge.py')
      return new Promise((resolve) => {
        const py = spawn(resolvePythonCommand(), [scriptPath], {
          ...bridgeSpawnOptions(),
          stdio: ['pipe', 'pipe', 'pipe']
        })
        let stdout = ''
        let stderr = ''
        py.stdout.on('data', (chunk: Buffer) => {
          stdout += chunk.toString()
        })
        py.stderr.on('data', (chunk: Buffer) => {
          stderr += chunk.toString()
        })
        py.on('close', (code) => {
          if (code !== 0 && stdout.trim() === '') {
            resolve({
              ok: false,
              error: stderr || `Bridge exited with code ${code}`
            })
            return
          }
          try {
            const result = JSON.parse(stdout.trim()) as {
              ok: boolean
              voice_id?: string
              voice_name?: string
              duration?: number
              error?: string
            }
            resolve(result)
          } catch {
            resolve({ ok: false, error: stderr || 'Failed to parse response' })
          }
        })
        py.on('error', (err) => {
          resolve({
            ok: false,
            error: `Failed to start bridge: ${err.message}`
          })
        })
        py.stdin?.write(JSON.stringify({ file_path: filePath, voice_name: voiceName }) + '\n')
        py.stdin?.end()
      })
    }
  )

  ipcMain.handle(
    'rex:previewWakeWordSample',
    async (
      _event,
      wakeWordId: string
    ): Promise<{
      ok: boolean
      audio_base64?: string
      has_sample?: boolean
      error?: string
    }> => {
      const scriptPath = resolveBridgeScript('rex_wakeword_sample_bridge.py')
      return new Promise((resolve) => {
        const py = spawn(resolvePythonCommand(), [scriptPath], {
          ...bridgeSpawnOptions(),
          stdio: ['pipe', 'pipe', 'pipe']
        })
        let stdout = ''
        let stderr = ''
        py.stdout.on('data', (chunk: Buffer) => {
          stdout += chunk.toString()
        })
        py.stderr.on('data', (chunk: Buffer) => {
          stderr += chunk.toString()
        })
        py.on('close', (code) => {
          if (code !== 0 && stdout.trim() === '') {
            resolve({
              ok: false,
              error: stderr || `Bridge exited with code ${code}`
            })
            return
          }
          try {
            const result = JSON.parse(stdout.trim()) as {
              ok: boolean
              audio_base64?: string
              has_sample?: boolean
              error?: string
            }
            resolve(result)
          } catch {
            resolve({ ok: false, error: stderr || 'Failed to parse response' })
          }
        })
        py.on('error', (err) => {
          resolve({
            ok: false,
            error: `Failed to start bridge: ${err.message}`
          })
        })
        py.stdin?.write(JSON.stringify({ wake_word_id: wakeWordId }) + '\n')
        py.stdin?.end()
      })
    }
  )

  ipcMain.handle(
    'rex:trainWakeWord',
    async (
      _event,
      phrase: string,
      positiveSamples: number[][],
      negativeSamples: number[][]
    ): Promise<{
      ok: boolean
      model_path?: string
      phrase?: string
      error?: string
    }> => {
      const scriptPath = resolveBridgeScript('rex_wakeword_train_bridge.py')
      return new Promise((resolve) => {
        const py = spawn(resolvePythonCommand(), [scriptPath], {
          ...bridgeSpawnOptions(),
          stdio: ['pipe', 'pipe', 'pipe']
        })
        let stdout = ''
        let stderr = ''
        py.stdout.on('data', (chunk: Buffer) => {
          stdout += chunk.toString()
        })
        py.stderr.on('data', (chunk: Buffer) => {
          stderr += chunk.toString()
        })
        py.on('close', (code) => {
          if (code !== 0 && stdout.trim() === '') {
            resolve({
              ok: false,
              error: stderr || `Bridge exited with code ${code}`
            })
            return
          }
          try {
            const result = JSON.parse(stdout.trim()) as {
              ok: boolean
              model_path?: string
              phrase?: string
              error?: string
            }
            resolve(result)
          } catch {
            resolve({ ok: false, error: stderr || 'Failed to parse response' })
          }
        })
        py.on('error', (err) => {
          resolve({
            ok: false,
            error: `Failed to start bridge: ${err.message}`
          })
        })
        py.stdin?.write(
          JSON.stringify({
            phrase,
            positive_samples: positiveSamples,
            negative_samples: negativeSamples
          }) + '\n'
        )
        py.stdin?.end()
      })
    }
  )

  ipcMain.handle('rex:getVoiceEnrollments', async (): Promise<BridgeResult<{ active_user_id: string; enrollments: unknown[] }>> => {
    return callEnrollmentBridge({
      ...privateSessionPayload(session, { action: 'list' })
    }) as Promise<BridgeResult<{ active_user_id: string; enrollments: unknown[] }>>
  })

  ipcMain.handle('rex:enrollVoice', async (_event, userId: string, samples: number[][]): Promise<BridgeResult<{ enrollment?: unknown }>> => {
    if (userId !== session.userId) {
      return {
        ok: false,
        error: 'Voice enrollment must match the authenticated Electron user'
      }
    }
    return callEnrollmentBridge(
      privateSessionPayload(session, {
        action: 'enroll',
        user_id: session.userId,
        samples
      })
    )
  })

  ipcMain.handle('rex:deleteVoiceEnrollment', async (_event, userId: string): Promise<BridgeResult<{ deleted?: boolean }>> => {
    if (userId !== session.userId) {
      return {
        ok: false,
        error: 'Voice enrollment must match the authenticated Electron user'
      }
    }
    return callEnrollmentBridge(
      privateSessionPayload(session, {
        action: 'delete',
        user_id: session.userId
      })
    )
  })
}

export function getCurrentVoiceState(): string {
  if ((!voiceProcess || voiceProcess.exitCode !== null) && currentVoiceState !== 'error') {
    return 'idle'
  }
  return currentVoiceState
}

function callEnrollmentBridge(payload: Record<string, unknown>): Promise<BridgeResult<Record<string, unknown>>> {
  const scriptPath = resolveBridgeScript('rex_voice_enrollment_bridge.py')
  return new Promise((resolve) => {
    const py = spawn(resolvePythonCommand(), [scriptPath], {
      ...bridgeSpawnOptions(),
      stdio: ['pipe', 'pipe', 'pipe']
    })
    let stdout = ''
    let stderr = ''

    py.stdout.on('data', (chunk: Buffer) => {
      stdout += chunk.toString()
    })
    py.stderr.on('data', (chunk: Buffer) => {
      stderr += chunk.toString()
    })
    py.on('close', (code) => {
      if (code !== 0 && stdout.trim() === '') {
        resolve({
          ok: false,
          error: stderr || `Bridge exited with code ${code}`
        })
        return
      }
      try {
        resolve(JSON.parse(stdout.trim()) as BridgeResult<Record<string, unknown>>)
      } catch {
        resolve({ ok: false, error: stderr || 'Failed to parse response' })
      }
    })
    py.on('error', (err) => {
      resolve({ ok: false, error: `Failed to start bridge: ${err.message}` })
    })
    py.stdin?.write(JSON.stringify(payload) + '\n')
    py.stdin?.end()
  })
}

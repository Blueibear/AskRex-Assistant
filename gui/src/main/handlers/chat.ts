import { ipcMain } from 'electron'
import { spawn, spawnSync, ChildProcess } from 'child_process'
import { bridgeSpawnOptions, resolveBridgePath, resolvePythonCommand } from '../bridgeResolver'
import { logChatLatency } from '../chatLatency'
import { privateSessionPayload, type ElectronSessionIdentity } from '../sessionIdentity'
import type { ChatRecoveryPlan } from '../../types/ipc'

/**
 * Spawn the rex_chat_bridge.py script, pass the message via stdin,
 * and resolve with the reply string from stdout.
 */
function callRexBackend(message: string, session: ElectronSessionIdentity): Promise<string> {
  return new Promise((resolve, reject) => {
    const scriptPath = resolveBridgePath('rex_chat_bridge.py')

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
      if (code !== 0) {
        reject(new Error(`Rex exited with code ${code}: ${stderr.slice(0, 300)}`))
        return
      }
      try {
        const result = JSON.parse(stdout.trim()) as {
          ok: boolean
          reply?: string
          error?: string
        }
        if (result.ok) {
          resolve(result.reply ?? '')
        } else {
          reject(new Error(result.error ?? 'Unknown error from Rex backend'))
        }
      } catch {
        reject(new Error(`Failed to parse Rex response: ${stdout.slice(0, 200)}`))
      }
    })

    py.on('error', (err) => {
      reject(new Error(`Failed to spawn Python: ${err.message}`))
    })

    py.stdin.write(JSON.stringify(privateSessionPayload(session, { message })))
    py.stdin.end()
  })
}

function callConversationBackend(
  session: ElectronSessionIdentity,
  action: string,
  payload: Record<string, unknown> = {}
): unknown {
  const result = spawnSync(resolvePythonCommand(), [resolveBridgePath('rex_conversation_bridge.py')], {
    ...bridgeSpawnOptions(), input: JSON.stringify(privateSessionPayload(session, { action, ...payload })), encoding: 'utf8', timeout: 10_000
  })
  const response = JSON.parse((result.stdout || '').trim()) as { ok: boolean; result?: unknown; error?: string }
  if (result.status !== 0 || !response.ok) throw new Error(response.error || 'Conversation request failed')
  return response.result
}

// ---------------------------------------------------------------------------
// Persistent STT bridge — keeps Whisper loaded between requests so each
// transcription does not pay the 2-10 s model-load cost.
// ---------------------------------------------------------------------------

let sttProcess: ChildProcess | null = null
let sttReady = false
let sttLineBuffer = ''
const sttReadyCallbacks: Array<(err?: Error) => void> = []
const sttPendingRequests = new Map<string, { resolve: (t: string) => void; reject: (e: Error) => void }>()
const chatStreamProcesses = new Map<string, ChildProcess>()

/**
 * Ensure the persistent STT bridge process is running and ready.
 * If already ready, resolves immediately.
 * If starting, queues until the "ready" event arrives.
 */
function ensureSTTProcess(): Promise<void> {
  if (sttProcess !== null && sttReady) {
    return Promise.resolve()
  }

  if (sttProcess !== null && !sttReady) {
    // Bridge is starting — queue until ready.
    return new Promise((resolve, reject) => {
      sttReadyCallbacks.push((err) => (err ? reject(err) : resolve()))
    })
  }

  // Spawn a new bridge process.
  return new Promise((resolve, reject) => {
    const scriptPath = resolveBridgePath('rex_stt_bridge.py')
    sttProcess = spawn(resolvePythonCommand(), [scriptPath], {
      ...bridgeSpawnOptions(),
      stdio: ['pipe', 'pipe', 'pipe']
    })
    sttReady = false
    sttLineBuffer = ''

    sttProcess.stdout!.on('data', (chunk: Buffer) => {
      sttLineBuffer += chunk.toString()
      const lines = sttLineBuffer.split('\n')
      sttLineBuffer = lines.pop() ?? ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        try {
          const obj = JSON.parse(trimmed) as {
            type?: string
            ok?: boolean
            transcript?: string
            error?: string
            request_id?: string
            model?: string
          }

          if (obj.type === 'ready') {
            sttReady = true
            resolve()
            const cbs = sttReadyCallbacks.splice(0)
            cbs.forEach((cb) => cb())
          } else if (obj.type === 'error') {
            const err = new Error(obj.error ?? 'STT bridge startup error')
            reject(err)
            const cbs = sttReadyCallbacks.splice(0)
            cbs.forEach((cb) => cb(err))
            sttProcess = null
            sttReady = false
          } else if (typeof obj.request_id === 'string') {
            const pending = sttPendingRequests.get(obj.request_id)
            if (pending) {
              sttPendingRequests.delete(obj.request_id)
              if (obj.ok) {
                pending.resolve(obj.transcript ?? '')
              } else {
                pending.reject(new Error(obj.error ?? 'Transcription failed'))
              }
            }
          }
        } catch {
          // skip malformed NDJSON lines
        }
      }
    })

    sttProcess.on('close', () => {
      sttProcess = null
      sttReady = false
      const err = new Error('STT bridge process closed unexpectedly')
      sttPendingRequests.forEach((p) => p.reject(err))
      sttPendingRequests.clear()
    })

    sttProcess.on('error', (err) => {
      const startErr = new Error(`Failed to spawn STT bridge: ${err.message}`)
      reject(startErr)
      sttProcess = null
      sttReady = false
    })
  })
}

async function transcribeAudio(audioBase64: string): Promise<string> {
  await ensureSTTProcess()

  if (!sttProcess) {
    throw new Error('STT process is not available')
  }

  return new Promise((resolve, reject) => {
    const requestId = `${Date.now()}-${Math.random()}`
    sttPendingRequests.set(requestId, { resolve, reject })
    sttProcess!.stdin!.write(JSON.stringify({ audio_base64: audioBase64, request_id: requestId }) + '\n')
  })
}

export function registerChatHandlers(session: ElectronSessionIdentity): void {
  ipcMain.handle('rex:conversation', (_event, action: string, payload?: Record<string, unknown>) =>
    callConversationBackend(session, action, payload)
  )
  ipcMain.handle('rex:sendChat', async (_event, message: string): Promise<string> => {
    const startedAt = performance.now()
    try {
      const reply = await callRexBackend(message, session)
      logChatLatency('chat_ipc_complete', 'single', performance.now() - startedAt, 'ok')
      return reply
    } catch (error) {
      logChatLatency('chat_ipc_complete', 'single', performance.now() - startedAt, 'error')
      throw error
    }
  })

  ipcMain.handle('rex:startChatStream', async (event, { message, streamId, conversationId }: { message: string; streamId: string; conversationId?: string }): Promise<{ ok: boolean }> => {
    const scriptPath = resolveBridgePath('rex_chat_stream_bridge.py')

    const py = spawn(resolvePythonCommand(), [scriptPath], {
      ...bridgeSpawnOptions(),
      stdio: ['pipe', 'pipe', 'pipe']
    })
    chatStreamProcesses.set(streamId, py)

    const streamStartedAt = performance.now()
    let firstTokenLogged = false
    let sentFinal = false

    function sendToken(token: string): void {
      if (!firstTokenLogged) {
        firstTokenLogged = true
        logChatLatency('chat_first_token', 'stream', performance.now() - streamStartedAt, 'ok')
      }
      if (!event.sender.isDestroyed()) {
        event.sender.send('rex:chatToken', { streamId, token })
      }
    }

    function sendStatus(data: {
      status: string
      turn_id?: string
      sequence?: number
      terminal?: boolean
    }): void {
      if (event.sender.isDestroyed()) return
      if (data.status === 'model_failure') {
        event.sender.send('rex:chatStatus', { streamId, status: data.status })
      }
      if (
        data.status !== 'model_failure' &&
        typeof data.turn_id === 'string' &&
        typeof data.sequence === 'number' &&
        typeof data.terminal === 'boolean'
      ) {
        event.sender.send('rex:turnStatus', {
          turn_id: data.turn_id,
          sequence: data.sequence,
          status: data.status,
          terminal: data.terminal
        })
      }
    }

    function sendRecovery(recovery: ChatRecoveryPlan): void {
      if (!event.sender.isDestroyed()) {
        event.sender.send('rex:chatRecovery', { streamId, recovery })
      }
    }

    function sendDone(): void {
      if (!sentFinal) {
        sentFinal = true
        logChatLatency('chat_ipc_complete', 'stream', performance.now() - streamStartedAt, 'ok')
        if (!event.sender.isDestroyed()) {
          event.sender.send('rex:chatDone', { streamId })
        }
      }
    }

    function sendError(error: string): void {
      if (!sentFinal) {
        sentFinal = true
        logChatLatency('chat_ipc_complete', 'stream', performance.now() - streamStartedAt, 'error')
        if (!event.sender.isDestroyed()) {
          event.sender.send('rex:chatError', { streamId, error })
        }
      }
    }

    let lineBuffer = ''

    py.stdout.on('data', (chunk: Buffer) => {
      lineBuffer += chunk.toString()
      const lines = lineBuffer.split('\n')
      lineBuffer = lines.pop() ?? ''
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        try {
          const obj = JSON.parse(trimmed) as {
            type: string
            token?: string
            status?: string
            turn_id?: string
            sequence?: number
            terminal?: boolean
            error?: string
            recovery?: ChatRecoveryPlan
          }
          if (obj.type === 'token' && obj.token !== undefined) {
            sendToken(obj.token)
          } else if (obj.type === 'status' && obj.status !== undefined) {
            sendStatus({
              status: obj.status,
              turn_id: obj.turn_id,
              sequence: obj.sequence,
              terminal: obj.terminal
            })
          } else if (obj.type === 'recovery' && obj.recovery !== undefined) {
            sendRecovery(obj.recovery)
          } else if (obj.type === 'done') {
            sendDone()
          } else if (obj.type === 'error') {
            sendError(obj.error ?? 'Unknown streaming error')
          }
        } catch {
          // skip malformed NDJSON lines
        }
      }
    })

    py.on('close', (code) => {
      chatStreamProcesses.delete(streamId)
      // Flush any remaining buffered line
      if (lineBuffer.trim()) {
        try {
          const obj = JSON.parse(lineBuffer.trim()) as {
            type: string
            token?: string
            status?: string
            turn_id?: string
            sequence?: number
            terminal?: boolean
            error?: string
            recovery?: ChatRecoveryPlan
          }
          if (obj.type === 'token' && obj.token !== undefined) sendToken(obj.token)
          else if (obj.type === 'status' && obj.status !== undefined) {
            sendStatus({
              status: obj.status,
              turn_id: obj.turn_id,
              sequence: obj.sequence,
              terminal: obj.terminal
            })
          }
          else if (obj.type === 'recovery' && obj.recovery !== undefined) sendRecovery(obj.recovery)
          else if (obj.type === 'done') sendDone()
          else if (obj.type === 'error') sendError(obj.error ?? 'Streaming error')
        } catch {
          // ignore
        }
      }
      if (code !== 0) {
        sendError(`Streaming bridge exited with code ${code}`)
      } else {
        sendDone()
      }
    })

    py.on('error', (_err) => {
      chatStreamProcesses.delete(streamId)
      // Streaming bridge not available — fall back to non-streaming
      callRexBackend(message, session)
        .then((reply) => {
          sendToken(reply)
          sendDone()
        })
        .catch((e: Error) => {
          sendError(e.message)
        })
    })

    py.stdin.write(JSON.stringify(privateSessionPayload(session, { message, conversation_id: conversationId })))
    py.stdin.end()

    return { ok: true }
  })

  ipcMain.handle('rex:cancelChatStream', async (_event, streamId: string): Promise<{ ok: boolean }> => {
    const process = chatStreamProcesses.get(streamId)
    if (!process) return { ok: true }
    chatStreamProcesses.delete(streamId)
    process.kill()
    return { ok: true }
  })

  ipcMain.handle('rex:sendChatAudio', async (_event, audioBase64: string): Promise<{ ok: boolean; transcript?: string; error?: string }> => {
    try {
      const transcript = await transcribeAudio(audioBase64)
      return { ok: true, transcript }
    } catch (err) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : String(err)
      }
    }
  })
}

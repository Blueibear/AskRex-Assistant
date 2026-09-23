import { ipcMain } from 'electron'
import { spawn } from 'child_process'
import type {
  ModelDiscoveryProvider,
  ModelDiscoveryResponse,
  Settings,
  WakeWordInfo,
  WakeWordStatus
} from '../../types/ipc'
import { bridgeSpawnOptions, resolveBridgePath, resolvePythonCommand } from '../bridgeResolver'
import { discoverAiModelsAtEndpoint } from '../modelDiscovery'
import { buildWakeWordStatus } from '../voiceSettings'

const SETUP_PREVIEW_CHANNELS = [
  'rex:listVoices',
  'rex:previewVoice',
  'rex:listWakeWords',
  'rex:previewWakeWordSample',
  'rex:getSetupWakeWordStatus',
  'rex:discoverSetupAiModels'
] as const

const setupVoiceInventory = new Map<string, Set<string>>()
const setupWakeWordInventory = new Map<string, WakeWordInfo>()

function normalizeVoiceProvider(provider: string): string {
  return provider.trim().toLowerCase()
}

function enumeratedVoiceIds(voices: unknown[]): Set<string> {
  const ids = new Set<string>()
  for (const voice of voices) {
    if (typeof voice !== 'object' || voice === null) continue
    const id = (voice as Record<string, unknown>).id
    if (typeof id === 'string' && id) ids.add(id)
  }
  return ids
}

function asWakeWordInfo(value: unknown): WakeWordInfo | null {
  if (typeof value !== 'object' || value === null) return null
  const item = value as Record<string, unknown>
  const { id, name, engine } = item
  if (typeof id !== 'string' || !id.trim()) return null
  if (typeof name !== 'string' || !name.trim()) return null
  if (
    engine !== 'openwakeword' &&
    engine !== 'custom_onnx' &&
    engine !== 'custom_embedding'
  ) {
    return null
  }
  if (item.model_path !== undefined && typeof item.model_path !== 'string') return null
  if (item.has_sample !== undefined && typeof item.has_sample !== 'boolean') return null

  return {
    id,
    name,
    engine,
    has_sample: typeof item.has_sample === 'boolean' ? item.has_sample : undefined,
    model_path: typeof item.model_path === 'string' ? item.model_path : undefined
  }
}

function enumeratedWakeWords(values: unknown[]): WakeWordInfo[] {
  const wakeWords: WakeWordInfo[] = []
  for (const value of values) {
    const wakeWord = asWakeWordInfo(value)
    if (wakeWord) wakeWords.push(wakeWord)
  }
  return wakeWords
}

function setupWakeWordSettings(wakeWord: WakeWordInfo): Settings {
  const phrase = wakeWord.name.trim() || wakeWord.id.replace(/_/g, ' ')
  if (wakeWord.engine === 'custom_embedding') {
    return {
      wakeWordBackend: 'custom_embedding',
      customWakeWordId: wakeWord.id,
      wakeWordPhrase: phrase,
      wakeWordEmbeddingPath: wakeWord.model_path ?? ''
    }
  }
  if (wakeWord.engine === 'custom_onnx') {
    return {
      wakeWordBackend: 'custom_onnx',
      wakeWordPhrase: phrase,
      wakeWordModelPath: wakeWord.model_path ?? ''
    }
  }
  return {
    wakeWordBackend: 'openwakeword',
    wakeWord: wakeWord.id,
    wakeWordPhrase: phrase
  }
}

function callJsonBridge(
  scriptName: string,
  payload: Record<string, unknown>
): Promise<Record<string, unknown>> {
  return new Promise((resolve) => {
    const py = spawn(resolvePythonCommand(), [resolveBridgePath(scriptName)], {
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
        resolve({ ok: false, error: stderr || `Bridge exited with code ${code}` })
        return
      }
      try {
        resolve(JSON.parse(stdout.trim()) as Record<string, unknown>)
      } catch {
        resolve({ ok: false, error: stderr || 'Failed to parse response' })
      }
    })
    py.on('error', (error) => {
      resolve({ ok: false, error: `Failed to start bridge: ${error.message}` })
    })
    py.stdin?.write(JSON.stringify(payload) + '\n')
    py.stdin?.end()
  })
}

export function registerSetupPreviewHandlers(): void {
  setupVoiceInventory.clear()
  setupWakeWordInventory.clear()

  ipcMain.handle('rex:listVoices', async (_event, provider: string) => {
    const result = await callJsonBridge('rex_voices_bridge.py', { provider })
    const voices = Array.isArray(result.voices) ? result.voices : []
    const providerKey = normalizeVoiceProvider(provider)
    if (result.ok === true) {
      setupVoiceInventory.set(providerKey, enumeratedVoiceIds(voices))
    } else {
      setupVoiceInventory.delete(providerKey)
    }
    return {
      ok: result.ok === true,
      voices,
      error: typeof result.error === 'string' ? result.error : undefined
    }
  })

  ipcMain.handle('rex:previewVoice', async (_event, provider: string, voiceId: string) => {
    const allowedVoiceIds = setupVoiceInventory.get(normalizeVoiceProvider(provider))
    if (!allowedVoiceIds?.has(voiceId)) {
      return {
        ok: false,
        error: 'Choose a voice from the available setup list before previewing it.'
      }
    }
    return callJsonBridge('rex_voice_sample_bridge.py', { provider, voice_id: voiceId })
  })

  ipcMain.handle('rex:listWakeWords', async () => {
    setupWakeWordInventory.clear()
    const result = await callJsonBridge('rex_wakeword_list_bridge.py', {})
    const wakeWords = Array.isArray(result.wake_words)
      ? enumeratedWakeWords(result.wake_words)
      : []
    if (result.ok === true) {
      for (const wakeWord of wakeWords) {
        setupWakeWordInventory.set(wakeWord.id, wakeWord)
      }
    }
    return {
      ok: result.ok === true,
      wake_words: wakeWords,
      error: typeof result.error === 'string' ? result.error : undefined,
      warning: typeof result.warning === 'string' ? result.warning : undefined
    }
  })

  ipcMain.handle('rex:previewWakeWordSample', async (_event, wakeWordId: string) =>
    callJsonBridge('rex_wakeword_sample_bridge.py', { wake_word_id: wakeWordId })
  )

  ipcMain.handle('rex:getSetupWakeWordStatus', (_event, wakeWordId: string): WakeWordStatus => {
    const selectedWakeWord = setupWakeWordInventory.get(wakeWordId)
    if (!selectedWakeWord) {
      throw new Error('Choose a wake word from the available setup list before checking it.')
    }
    return buildWakeWordStatus(setupWakeWordSettings(selectedWakeWord))
  })

  ipcMain.handle(
    'rex:discoverSetupAiModels',
    async (
      _event,
      provider: ModelDiscoveryProvider | string,
      endpoint: string
    ): Promise<ModelDiscoveryResponse> => {
      if (provider !== 'ollama' && provider !== 'lmstudio') {
        return { ok: false, models: [], error: 'Unsupported model discovery provider' }
      }
      return discoverAiModelsAtEndpoint(provider, typeof endpoint === 'string' ? endpoint : '')
    }
  )
}

export function unregisterSetupPreviewHandlers(): void {
  setupVoiceInventory.clear()
  setupWakeWordInventory.clear()
  for (const channel of SETUP_PREVIEW_CHANNELS) {
    ipcMain.removeHandler(channel)
  }
}

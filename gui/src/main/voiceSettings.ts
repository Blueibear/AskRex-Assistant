import { basename, dirname, join } from 'path'
import { existsSync } from 'fs'
import type { Settings, VoiceSettings, WakeWordBackend, WakeWordStatus } from '../types/ipc'
import { getConfigDir, readRexConfig } from './configStore'

function normalizeWakeWordBackend(raw: unknown): WakeWordBackend {
  return raw === 'custom_onnx' || raw === 'custom_embedding' ? raw : 'openwakeword'
}

function normalizeWakeWordId(raw: unknown): string {
  if (typeof raw !== 'string') return ''
  return raw.trim().replace(/\s+/g, '_').toLowerCase()
}

function normalizeAudioDeviceIndex(raw: unknown, fallback: unknown): number | null {
  if (raw === null) return null
  if (typeof raw === 'number' && Number.isInteger(raw) && raw >= 0) return raw
  if (typeof fallback === 'number' && Number.isInteger(fallback) && fallback >= 0) return fallback
  return null
}

export function wakeWordIdToPhrase(raw: unknown): string {
  return typeof raw === 'string' ? raw.trim().replace(/_/g, ' ') : ''
}

function inferCustomWakeWordIdFromPath(assetPath: string): string {
  if (!assetPath) return ''
  const parent = basename(dirname(assetPath))
  if (parent && parent !== 'wake_words') return parent
  return basename(assetPath).replace(/\.[^.]+$/, '')
}

export function defaultCustomWakeWordAssetPath(
  backend: WakeWordBackend,
  phraseOrId: string
): string {
  const slug = normalizeWakeWordId(phraseOrId || 'hey rex') || 'hey_rex'
  const baseDir = join(getConfigDir(), 'wake_words', slug)
  return backend === 'custom_onnx'
    ? join(baseDir, 'model.onnx')
    : join(baseDir, 'embedding.pt')
}

export function buildVoiceSettings(raw: Settings = {}): VoiceSettings {
  const rexConfig = readRexConfig()
  const models =
    rexConfig.models && typeof rexConfig.models === 'object'
      ? (rexConfig.models as Record<string, unknown>)
      : {}
  const wakeword =
    rexConfig.wakeword && typeof rexConfig.wakeword === 'object'
      ? (rexConfig.wakeword as Record<string, unknown>)
      : {}
  const runtime =
    rexConfig.runtime && typeof rexConfig.runtime === 'object'
      ? (rexConfig.runtime as Record<string, unknown>)
      : {}
  const audio =
    rexConfig.audio && typeof rexConfig.audio === 'object'
      ? (rexConfig.audio as Record<string, unknown>)
      : {}

  const rawEngine =
    typeof raw.ttsEngine === 'string'
      ? raw.ttsEngine
      : typeof models.tts_provider === 'string'
        ? models.tts_provider
        : null
  const ttsEngine: VoiceSettings['ttsEngine'] =
    rawEngine === 'xtts' || rawEngine === 'edge-tts' || rawEngine === 'pyttsx3'
      ? rawEngine
      : rawEngine === 'elevenlabs'
        ? 'xtts'
        : rawEngine === 'openai'
          ? 'edge-tts'
          : 'pyttsx3'

  const rawSttDevice =
    typeof raw.sttDevice === 'string'
      ? raw.sttDevice
      : typeof models.whisper_device === 'string'
        ? models.whisper_device
        : null
  const sttDevice: VoiceSettings['sttDevice'] =
    rawSttDevice === 'cpu' || rawSttDevice === 'cuda' ? rawSttDevice : 'auto'

  const backend = normalizeWakeWordBackend(
    raw.wakeWordBackend ?? wakeword.backend
  )
  const configuredPhrase =
    typeof wakeword.wakeword === 'string' ? wakeword.wakeword.trim() : ''
  const configuredKeyword =
    typeof wakeword.keyword === 'string' ? wakeword.keyword.trim() : ''
  const configuredFallbackKeyword =
    typeof wakeword.fallback_keyword === 'string' ? wakeword.fallback_keyword.trim() : ''
  const legacyWakeWordModel =
    typeof wakeword.model === 'string' ? wakeword.model.trim() : ''

  const wakeWord = normalizeWakeWordId(
    typeof raw.wakeWord === 'string' && raw.wakeWord.trim()
      ? raw.wakeWord
      : backend === 'openwakeword'
        ? configuredKeyword || configuredPhrase || legacyWakeWordModel
        : configuredFallbackKeyword
  )

  const rawPhrase =
    typeof raw.wakeWordPhrase === 'string' && raw.wakeWordPhrase.trim()
      ? raw.wakeWordPhrase.trim()
      : configuredPhrase
  const embeddingPath =
    typeof raw.wakeWordEmbeddingPath === 'string' && raw.wakeWordEmbeddingPath.trim()
      ? raw.wakeWordEmbeddingPath.trim()
      : typeof wakeword.embedding_path === 'string'
        ? wakeword.embedding_path
        : ''
  const customWakeWordId =
    typeof raw.customWakeWordId === 'string' && raw.customWakeWordId.trim()
      ? raw.customWakeWordId.trim()
      : inferCustomWakeWordIdFromPath(embeddingPath)
  const wakeWordPhrase =
    rawPhrase || wakeWordIdToPhrase(customWakeWordId || wakeWord) || 'hey rex'
  const modelPath =
    typeof raw.wakeWordModelPath === 'string' && raw.wakeWordModelPath.trim()
      ? raw.wakeWordModelPath.trim()
      : typeof wakeword.model_path === 'string'
        ? wakeword.model_path
        : ''

  return {
    backgroundVoiceEnabled:
      typeof raw.backgroundVoiceEnabled === 'boolean'
        ? raw.backgroundVoiceEnabled
        : runtime.background_voice_enabled === true,
    microphoneDeviceIndex: normalizeAudioDeviceIndex(
      raw.microphoneDeviceIndex,
      audio.input_device_index,
    ),
    speakerDeviceIndex: normalizeAudioDeviceIndex(
      raw.speakerDeviceIndex,
      audio.output_device_index,
    ),
    microphoneDeviceId:
      typeof raw.microphoneDeviceId === 'string' ? raw.microphoneDeviceId : '',
    speakerDeviceId:
      typeof raw.speakerDeviceId === 'string' ? raw.speakerDeviceId : '',
    ttsEngine,
    ttsVoice:
      typeof raw.ttsVoice === 'string'
        ? raw.ttsVoice
        : typeof models.tts_voice === 'string'
          ? models.tts_voice
          : '',
    speechRate:
      typeof raw.speechRate === 'number'
        ? raw.speechRate
        : typeof models.tts_speed === 'number'
          ? models.tts_speed
          : typeof models.tts_speed === 'string'
            ? parseFloat(models.tts_speed) || 1.0
            : 1.0,
    volume: typeof raw.volume === 'number' ? raw.volume : 1.0,
    sttModel:
      typeof raw.sttModel === 'string'
        ? raw.sttModel
        : typeof models.whisper_model === 'string'
          ? models.whisper_model
          : 'base',
    sttLanguage:
      typeof raw.sttLanguage === 'string'
        ? raw.sttLanguage
        : typeof models.stt_language === 'string'
          ? models.stt_language
          : 'auto',
    sttDevice,
    wakeWord,
    wakeWordBackend: backend,
    customWakeWordId,
    wakeWordPhrase,
    wakeWordModelPath:
      modelPath || (backend === 'custom_onnx' ? defaultCustomWakeWordAssetPath('custom_onnx', wakeWordPhrase) : ''),
    wakeWordEmbeddingPath:
      embeddingPath || (backend === 'custom_embedding'
        ? defaultCustomWakeWordAssetPath('custom_embedding', customWakeWordId || wakeWordPhrase)
        : '')
  }
}

export function buildWakeWordStatus(raw: Settings = {}): WakeWordStatus {
  const voice = buildVoiceSettings(raw)
  const rexConfig = readRexConfig()
  const wakeword =
    rexConfig.wakeword && typeof rexConfig.wakeword === 'object'
      ? (rexConfig.wakeword as Record<string, unknown>)
      : {}
  const fallbackEnabled =
    typeof wakeword.fallback_to_builtin === 'boolean' ? wakeword.fallback_to_builtin : true
  const fallbackKeyword =
    wakeWordIdToPhrase(voice.wakeWord)
    || (typeof wakeword.fallback_keyword === 'string' ? wakeword.fallback_keyword.trim() : '')
    || 'hey jarvis'

  if (voice.wakeWordBackend === 'openwakeword') {
    return {
      requestedBackend: 'openwakeword',
      configuredPhrase: wakeWordIdToPhrase(voice.wakeWord) || 'hey jarvis',
      fallbackEnabled,
      fallbackKeyword,
      assetKind: 'builtin',
      assetPath: '',
      assetExists: false,
      fallbackActive: false,
      status: 'built_in',
      statusLabel: 'Built-in wake path active',
      detail: 'Built-in openWakeWord is configured. No custom asset file is required.'
    }
  }

  const configuredPhrase = voice.wakeWordPhrase.trim() || 'hey rex'
  const assetPath =
    voice.wakeWordBackend === 'custom_onnx'
      ? voice.wakeWordModelPath.trim() || defaultCustomWakeWordAssetPath('custom_onnx', configuredPhrase)
      : voice.wakeWordEmbeddingPath.trim()
        || defaultCustomWakeWordAssetPath(
          'custom_embedding',
          voice.customWakeWordId.trim() || configuredPhrase
        )
  const assetExists = existsSync(assetPath)
  const fallbackActive = fallbackEnabled && !assetExists

  return {
    requestedBackend: voice.wakeWordBackend,
    configuredPhrase,
    fallbackEnabled,
    fallbackKeyword,
    assetKind: voice.wakeWordBackend === 'custom_onnx' ? 'onnx' : 'embedding',
    assetPath,
    assetExists,
    fallbackActive,
    status: assetExists ? 'asset_ready' : 'missing_asset',
    statusLabel: assetExists
      ? `${voice.wakeWordBackend === 'custom_onnx' ? 'Custom ONNX' : 'Custom embedding'} asset found`
      : 'Custom asset missing',
    detail: assetExists
      ? 'Asset file is present. Runtime validation happens when the voice loop starts.'
      : fallbackEnabled
        ? `Missing asset file. Built-in fallback '${fallbackKeyword}' will be used until this asset is added.`
        : 'Missing asset file. Voice startup will fail until a valid custom asset is added.'
  }
}

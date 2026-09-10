import type { Settings } from '../types/ipc'
import { readRexConfig, writeRexConfig } from './configStore'
import { normalizeAiModelRouting, normalizeGuiAiProvider, toRuntimeAiProvider } from './aiSettings'
import { normalizeAutonomyMode } from './autonomySettings'
import { buildVoiceSettings, defaultCustomWakeWordAssetPath, wakeWordIdToPhrase } from './voiceSettings'

export interface MirrorResult {
  ok: boolean
  error?: string
}

/**
 * Mirror GUI settings into rex_config.json for sections that overlap.
 *
 * Returns a truthful {ok, error} result instead of swallowing failures -
 * callers (rex:setSettings) must surface a mirror failure to the user
 * rather than reporting a false "Saved" state (S4).
 */
export function mirrorToRexConfig(section: string, values: Settings): MirrorResult {
  try {
    const rexConfig = readRexConfig()

    if (section === 'ai') {
      const models = ((rexConfig.models ?? {}) as Record<string, unknown>)
      if (typeof values.temperature === 'number') models.llm_temperature = String(values.temperature)
      if (typeof values.maxTokens === 'number') models.llm_max_tokens = values.maxTokens
      if (typeof values.autonomyMode === 'string') {
        const autonomyMode = normalizeAutonomyMode(values.autonomyMode)
        if (!autonomyMode) return { ok: false, error: 'Invalid autonomy mode' }
        models.autonomy_mode = autonomyMode
      }
      const providerWasSupplied = typeof values.provider === 'string'
      const provider = normalizeGuiAiProvider(
        providerWasSupplied ? values.provider : models.llm_provider
      )
      if (providerWasSupplied) {
        models.llm_provider = toRuntimeAiProvider(provider)
        const llm = ((rexConfig.llm ?? {}) as Record<string, unknown>)
        delete llm.provider
        rexConfig.llm = llm
      }
      if (
        (provider === 'ollama' || provider === 'local')
        && typeof values.customModelId === 'string'
        && values.customModelId.trim()
      ) {
        models.llm_model = values.customModelId.trim()
      }
      if (provider === 'openai' && (providerWasSupplied || typeof values.model === 'string')) {
        if (typeof values.model !== 'string' || !values.model.trim()) {
          return { ok: false, error: 'OpenAI model is required' }
        }
        const openai = ((rexConfig.openai ?? {}) as Record<string, unknown>)
        openai.model = values.model.trim()
        rexConfig.openai = openai
      }
      if (typeof values.openaiBaseUrl === 'string') {
        const openai = ((rexConfig.openai ?? {}) as Record<string, unknown>)
        openai.base_url = values.openaiBaseUrl.trim() || null
        rexConfig.openai = openai
      }
      if (
        provider === 'openrouter'
        && (providerWasSupplied || typeof values.openrouterModel === 'string')
      ) {
        if (typeof values.openrouterModel !== 'string' || !values.openrouterModel.trim()) {
          return { ok: false, error: 'OpenRouter model is required' }
        }
        const openrouter = ((rexConfig.openrouter ?? {}) as Record<string, unknown>)
        openrouter.model = values.openrouterModel.trim()
        openrouter.base_url = 'https://openrouter.ai/api/v1'
        rexConfig.openrouter = openrouter
      }
      if (typeof values.ollamaBaseUrl === 'string' && values.ollamaBaseUrl.trim()) {
        const ollama = ((rexConfig.ollama ?? {}) as Record<string, unknown>)
        ollama.base_url = values.ollamaBaseUrl.trim()
        rexConfig.ollama = ollama
      }
      rexConfig.models = models
      rexConfig.model_routing = normalizeAiModelRouting(values.modelRouting)
      if (typeof values.personality === 'string' && values.personality) {
        rexConfig.personality = values.personality
      }
      writeRexConfig(rexConfig)
    }

    if (section === 'voice') {
      const voice = buildVoiceSettings(values)
      const models = ((rexConfig.models ?? {}) as Record<string, unknown>)
      if (typeof voice.ttsEngine === 'string') models.tts_provider = voice.ttsEngine
      if (typeof voice.ttsVoice === 'string') models.tts_voice = voice.ttsVoice
      if (typeof voice.speechRate === 'number') models.tts_speed = voice.speechRate
      if (typeof voice.sttModel === 'string') models.whisper_model = voice.sttModel
      if (typeof voice.sttDevice === 'string') models.whisper_device = voice.sttDevice
      if (typeof voice.sttLanguage === 'string') models.stt_language = voice.sttLanguage
      rexConfig.models = models
      const audio = ((rexConfig.audio ?? {}) as Record<string, unknown>)
      audio.input_device_index = voice.microphoneDeviceIndex
      audio.output_device_index = voice.speakerDeviceIndex
      rexConfig.audio = audio
      const wakeword = ((rexConfig.wakeword ?? {}) as Record<string, unknown>)
      delete wakeword.model
      wakeword.backend = voice.wakeWordBackend
      wakeword.fallback_to_builtin = true
      wakeword.fallback_keyword = wakeWordIdToPhrase(voice.wakeWord) || 'hey jarvis'
      if (voice.wakeWordBackend === 'openwakeword') {
        const builtInPhrase = wakeWordIdToPhrase(voice.wakeWord)
        wakeword.wakeword = builtInPhrase
        wakeword.keyword = builtInPhrase || null
        wakeword.model_path = null
        wakeword.embedding_path = null
      } else if (voice.wakeWordBackend === 'custom_onnx') {
        const customPhrase = voice.wakeWordPhrase.trim() || 'hey rex'
        wakeword.wakeword = customPhrase
        wakeword.keyword = customPhrase
        wakeword.model_path =
          voice.wakeWordModelPath.trim() || defaultCustomWakeWordAssetPath('custom_onnx', customPhrase)
        wakeword.embedding_path = null
      } else {
        const customPhrase = voice.wakeWordPhrase.trim() || 'hey rex'
        const embeddingId = voice.customWakeWordId.trim() || customPhrase
        wakeword.wakeword = customPhrase
        wakeword.keyword = customPhrase
        wakeword.model_path = null
        wakeword.embedding_path =
          voice.wakeWordEmbeddingPath.trim()
          || defaultCustomWakeWordAssetPath('custom_embedding', embeddingId)
      }
      rexConfig.wakeword = wakeword
      const runtime = ((rexConfig.runtime ?? {}) as Record<string, unknown>)
      runtime.background_voice_enabled = voice.backgroundVoiceEnabled === true
      rexConfig.runtime = runtime
      writeRexConfig(rexConfig)
    }

    if (section === 'general') {
      const ui = ((rexConfig.ui ?? {}) as Record<string, unknown>)
      if (typeof values.startMinimized === 'boolean') ui.start_minimized = values.startMinimized
      rexConfig.ui = ui
      if (typeof values.timezone === 'string' && values.timezone.trim()) {
        const location = ((rexConfig.location ?? {}) as Record<string, unknown>)
        location.default_timezone = values.timezone.trim()
        rexConfig.location = location
      }
      writeRexConfig(rexConfig)
    }

    if (section === 'integrations') {
      if (typeof values.emailProvider === 'string') {
        const email = ((rexConfig.email ?? {}) as Record<string, unknown>)
        email.provider = values.emailProvider
        rexConfig.email = email
      }
      if (typeof values.calendarProvider === 'string') {
        const calendar = ((rexConfig.calendar ?? {}) as Record<string, unknown>)
        calendar.provider = values.calendarProvider === 'gmail' ? 'google' : values.calendarProvider
        rexConfig.calendar = calendar
      }
      if (typeof values.haUrl === 'string' && values.haUrl.trim()) {
        const ha = ((rexConfig.home_assistant ?? {}) as Record<string, unknown>)
        ha.base_url = values.haUrl.trim()
        rexConfig.home_assistant = ha
      }
      if (typeof values.telegramChatId === 'string' && values.telegramChatId.trim()) {
        const telegram = ((rexConfig.telegram ?? {}) as Record<string, unknown>)
        telegram.chat_id = values.telegramChatId.trim()
        rexConfig.telegram = telegram
      }
      const openclaw = ((rexConfig.openclaw ?? {}) as Record<string, unknown>)
      if (typeof values.openclawGatewayUrl === 'string') {
        openclaw.gateway_url = values.openclawGatewayUrl.trim()
      }
      if (typeof values.openclawToolsEnabled === 'boolean') {
        openclaw.use_tools = values.openclawToolsEnabled
      }
      if (typeof values.openclawVoiceEnabled === 'boolean') {
        openclaw.use_voice_backend = values.openclawVoiceEnabled
      }
      rexConfig.openclaw = openclaw
      writeRexConfig(rexConfig)
    }

    if (section === 'system') {
      if (typeof values.toolTimeoutSeconds === 'number') {
        rexConfig.tool_timeout_seconds = values.toolTimeoutSeconds
      }
      if (typeof values.requireConfirmSystemChanges === 'boolean') {
        const windows = ((rexConfig.windows ?? {}) as Record<string, unknown>)
        windows.require_confirm_system_changes = values.requireConfirmSystemChanges
        rexConfig.windows = windows
      }
      if (typeof values.allowedFileRoots === 'string' && values.allowedFileRoots.trim()) {
        rexConfig.allowed_file_roots = values.allowedFileRoots.split(',').map((s: string) => s.trim()).filter(Boolean)
      }
      if (typeof values.debugLogging === 'boolean') {
        const runtime = ((rexConfig.runtime ?? {}) as Record<string, unknown>)
        runtime.log_level = values.debugLogging ? 'DEBUG' : 'INFO'
        rexConfig.runtime = runtime
      }
      writeRexConfig(rexConfig)
    }
    return { ok: true }
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) }
  }
}

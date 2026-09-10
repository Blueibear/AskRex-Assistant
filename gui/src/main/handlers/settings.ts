import { existsSync, readFileSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'
import { ipcMain } from 'electron'
import type {
  ModelDiscoveryProvider,
  ModelDiscoveryResponse,
  PreferenceSuggestion,
  Settings,
  WakeWordStatus
} from '../../types/ipc'
import { buildAiSettings, buildAiSettingsForSave } from '../aiSettings'
import {
  applyBackgroundVoicePreference,
  recoverBackgroundVoice,
} from '../backgroundRuntime'
import { migrateLegacyAutonomySettings, stripLegacyAutonomyMode } from '../autonomySettings'
import { getVaultReference, putVaultReference } from '../credentialReferences'
import {
  vaultDeleteSecret,
  vaultHasSecret,
  vaultSetSecret,
  type VaultContext
} from '../credentialVault'
import { readGuiSettings, readRexConfigStrict, writeGuiSettings, writeRexConfig } from '../configStore'
import { safeIpcErrorMessage, SafeValidationError } from '../ipcErrors'
import {
  loadIntegrationSettings,
  persistSettingsSection,
  removeEmailAccount
} from '../integrationSettingsStorage'
import { discoverAiModelsAtEndpoint } from '../modelDiscovery'
import { defaultSettingsMap } from '../settingsDefaults'
import { mirrorToRexConfig } from '../settingsMirror'
import type { ElectronSessionIdentity } from '../sessionIdentity'
import { buildVoiceSettings, buildWakeWordStatus } from '../voiceSettings'
import { callOutputRoutingBridge } from './outputRouting'

const ALLOWED_API_KEYS = [
  'OPENAI_API_KEY',
  'OPENROUTER_API_KEY',
  'ANTHROPIC_API_KEY',
  'OLLAMA_API_KEY',
  'ELEVENLABS_API_KEY',
  'SERPAPI_KEY',
  'SERPAPI_API_KEY',
  'BRAVE_API_KEY',
  'GOOGLE_API_KEY',
  'OPENWEATHERMAP_API_KEY',
  'REX_SPEAK_API_KEY',
  'OPENCLAW_GATEWAY_TOKEN'
] // pragma: allowlist secret

/** Derive a short integration name for vault metadata, e.g. OPENAI_API_KEY -> openai. */
function integrationNameForKey(key: string): string {
  return key.replace(/_API_KEY$|_KEY$/, '').toLowerCase()
}

function apiKeyContext(key: string): VaultContext {
  if (key === 'OPENCLAW_GATEWAY_TOKEN') {
    return { scope: 'household', integration: 'openclaw_gateway', account: null, slot: 'token' }
  }
  return {
    scope: 'household',
    integration: integrationNameForKey(key),
    account: null,
    slot: 'api_key'
  }
}

function objectSection(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function backgroundVoiceEnabled(config: Record<string, unknown>): boolean {
  const runtime = objectSection(config.runtime)
  return runtime.background_voice_enabled === true
}

function canonicalDeviceIndex(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

function audioDeviceIndices(config: Record<string, unknown>): {
  microphone: number | null
  speaker: number | null
} {
  const audio = objectSection(config.audio)
  return {
    microphone: canonicalDeviceIndex(audio.input_device_index),
    speaker: canonicalDeviceIndex(audio.output_device_index),
  }
}

function outputRoutingError(error: string | undefined): Error {
  return new Error(error ?? 'Output routing service is unavailable')
}

type SettingsResult = { ok: boolean; error?: string }

type AudioDeviceIndices = {
  microphone: number | null
  speaker: number | null
}

type PreviousVoiceState = {
  backgroundEnabled: boolean
  audioDevices: AudioDeviceIndices
}

type VoiceLifecycleState = {
  pendingBackgroundVoicePreference: boolean | null
  pendingVoiceRecovery: boolean
}

function readPreviousVoiceState(section: string): PreviousVoiceState | null {
  if (section !== 'voice') return null
  const config = readRexConfigStrict() as Record<string, unknown>
  return {
    backgroundEnabled: backgroundVoiceEnabled(config),
    audioDevices: audioDeviceIndices(config)
  }
}

function normalizeSettingsValues(section: string, values: Settings): Settings {
  if (section === 'ai') return buildAiSettingsForSave(values) as unknown as Settings
  if (section === 'voice') return buildVoiceSettings(values) as unknown as Settings
  return values
}

async function handleOutputRoutingSettings(
  session: ElectronSessionIdentity,
  section: string,
  values: Settings
): Promise<SettingsResult | null> {
  if (section === 'outputRouting') {
    const response = await callOutputRoutingBridge(session, {
      command: 'update_policy',
      policy: values
    })
    return { ok: response.ok, error: response.error }
  }
  if (section !== 'outputRoutingTest') return null
  const targetId = values.target_id
  if (typeof targetId !== 'string' || !targetId) {
    return { ok: false, error: 'A routing target is required' }
  }
  const response = await callOutputRoutingBridge(session, {
    command: 'test_playback',
    target_id: targetId
  })
  return { ok: response.ok, error: response.error }
}

function nextAudioDeviceIndices(values: Settings): AudioDeviceIndices {
  return {
    microphone: canonicalDeviceIndex(values.microphoneDeviceIndex),
    speaker: canonicalDeviceIndex(values.speakerDeviceIndex)
  }
}

function audioDevicesChanged(previous: AudioDeviceIndices, next: AudioDeviceIndices): boolean {
  return previous.microphone !== next.microphone || previous.speaker !== next.speaker
}

function reconcileVoiceLifecycle(
  session: ElectronSessionIdentity,
  state: VoiceLifecycleState,
  previous: PreviousVoiceState,
  values: Settings
): SettingsResult | null {
  const nextBackgroundVoice = values.backgroundVoiceEnabled === true
  if (
    state.pendingBackgroundVoicePreference !== null &&
    state.pendingBackgroundVoicePreference !== nextBackgroundVoice
  ) {
    state.pendingBackgroundVoicePreference = null
  }

  const backgroundChanged = nextBackgroundVoice !== previous.backgroundEnabled
  const shouldReconcileBackground =
    backgroundChanged || state.pendingBackgroundVoicePreference === nextBackgroundVoice
  if (shouldReconcileBackground) {
    const lifecycle = applyBackgroundVoicePreference(session, nextBackgroundVoice)
    if (!lifecycle.ok) {
      state.pendingBackgroundVoicePreference = nextBackgroundVoice
      return lifecycle
    }
    state.pendingBackgroundVoicePreference = null
    state.pendingVoiceRecovery = false
    return null
  }

  if (!nextBackgroundVoice) {
    state.pendingVoiceRecovery = false
    return null
  }

  const audioChanged = audioDevicesChanged(
    previous.audioDevices,
    nextAudioDeviceIndices(values)
  )
  if (!audioChanged && !state.pendingVoiceRecovery) return null

  const recovery = recoverBackgroundVoice()
  if (!recovery.ok) {
    state.pendingVoiceRecovery = true
    return recovery
  }
  state.pendingVoiceRecovery = false
  return null
}

async function handleSetSettings(
  session: ElectronSessionIdentity,
  state: VoiceLifecycleState,
  section: string,
  values: Settings
): Promise<SettingsResult> {
  const routed = await handleOutputRoutingSettings(session, section, values)
  if (routed !== null) return routed

  const previousVoice = readPreviousVoiceState(section)
  const normalizedValues = normalizeSettingsValues(section, values)
  const persisted = await persistSettingsSection(session, section, normalizedValues)
  if (!persisted.ok) return persisted
  if (previousVoice === null) return persisted

  return reconcileVoiceLifecycle(session, state, previousVoice, normalizedValues) ?? persisted
}

export function registerSettingsHandlers(session: ElectronSessionIdentity): void {
  const voiceLifecycleState: VoiceLifecycleState = {
    pendingBackgroundVoicePreference: null,
    pendingVoiceRecovery: false
  }

  ipcMain.handle('rex:getSettings', async (_event, section: string): Promise<Settings> => {
    if (section === 'outputRouting') {
      const response = await callOutputRoutingBridge(session, { command: 'get_policy' })
      if (!response.ok) throw outputRoutingError(response.error)
      return response as unknown as Settings
    }
    if (section === 'outputRoutingAccounts') {
      const response = await callOutputRoutingBridge(session, { command: 'list_media_accounts' })
      if (!response.ok) throw outputRoutingError(response.error)
      return response as unknown as Settings
    }

    const stored = section === 'ai' ? migrateLegacyAutonomySettings() : readGuiSettings()
    if (section === 'ai') {
      return buildAiSettings((stored[section] ?? {}) as Settings) as unknown as Settings
    }
    if (section === 'voice') {
      const voice = buildVoiceSettings((stored[section] ?? {}) as Settings)
      const config = readRexConfigStrict() as Record<string, unknown>
      voice.backgroundVoiceEnabled = backgroundVoiceEnabled(config)
      return voice as unknown as Settings
    }
    if (section === 'integrations') {
      return loadIntegrationSettings(session, stored as Record<string, Settings>)
    }
    return stored[section] ?? defaultSettingsMap[section] ?? {}
  })

  ipcMain.handle(
    'rex:discoverAiModels',
    async (_event, provider: ModelDiscoveryProvider | string): Promise<ModelDiscoveryResponse> => {
      if (provider !== 'ollama' && provider !== 'lmstudio') {
        return { ok: false, models: [], error: 'Unsupported model discovery provider' }
      }
      try {
        const config = readRexConfigStrict()
        const section = objectSection(provider === 'ollama' ? config.ollama : config.openai)
        const endpoint = typeof section.base_url === 'string' ? section.base_url : ''
        return discoverAiModelsAtEndpoint(provider, endpoint)
      } catch {
        return {
          ok: false,
          models: [],
          error: 'Model discovery configuration could not be read'
        }
      }
    }
  )

  ipcMain.handle('rex:getWakeWordStatus', (_event, values?: Settings): WakeWordStatus => {
    const stored = readGuiSettings()
    const source = values ?? ((stored.voice ?? {}) as Settings)
    return buildWakeWordStatus(source)
  })

  ipcMain.handle(
    'rex:setSettings',
    (_event, section: string, values: Settings): Promise<SettingsResult> =>
      handleSetSettings(session, voiceLifecycleState, section, values)
  )

  ipcMain.handle(
    'rex:removeEmailAccount',
    async (
      _event,
      idValue: string,
      confirmed: boolean
    ): Promise<{ ok: boolean; error?: string }> => removeEmailAccount(session, idValue, confirmed)
  )

  ipcMain.handle('rex:testVoice', () => {
    // Stub: in production this would invoke the TTS engine with a test phrase
    return { ok: true }
  })

  ipcMain.handle('rex:getPreferenceSuggestions', (): PreferenceSuggestion[] => {
    const prefsPath = join(homedir(), '.rex', 'preferences.json')
    let profile: Record<string, unknown> = {}
    try {
      if (existsSync(prefsPath)) {
        profile = JSON.parse(readFileSync(prefsPath, 'utf8')) as Record<string, unknown>
      }
    } catch {
      return []
    }

    const stored = readGuiSettings()
    const aiSettings = buildAiSettings(
      (stored['ai'] ?? defaultSettingsMap['ai'] ?? {}) as Settings
    )

    const suggestions: PreferenceSuggestion[] = []

    const preferredMode =
      typeof profile.preferred_autonomy_mode === 'string'
        ? profile.preferred_autonomy_mode
        : null
    if (preferredMode && preferredMode !== aiSettings.autonomyMode) {
      suggestions.push({
        field: 'autonomyMode',
        current_value: aiSettings.autonomyMode,
        suggested_value: preferredMode,
        reason: `You typically run Rex in "${preferredMode}" mode`
      })
    }

    const preferredModel =
      typeof profile.preferred_model === 'string' && profile.preferred_model
        ? profile.preferred_model
        : null
    if (preferredModel && preferredModel !== aiSettings.model) {
      suggestions.push({
        field: 'model',
        current_value: aiSettings.model,
        suggested_value: preferredModel,
        reason: `You most frequently use ${preferredModel}`
      })
    }

    const avgBudget = typeof profile.avg_budget_usd === 'number' ? profile.avg_budget_usd : 0
    if (avgBudget > 0 && aiSettings.budgetPerPlan === 0) {
      const suggested = Math.round(avgBudget * 2 * 100) / 100
      suggestions.push({
        field: 'budgetPerPlan',
        current_value: aiSettings.budgetPerPlan,
        suggested_value: suggested,
        reason: `Your average plan cost is $${avgBudget.toFixed(2)} - a $${suggested.toFixed(2)} budget would prevent overruns`
      })
    }

    return suggestions
  })

  ipcMain.handle(
    'rex:applyPreferenceSuggestion',
    (_event, field: string, value: string | number) => {
      const stored = readGuiSettings()
      const originalStored = JSON.parse(JSON.stringify(stored)) as Record<string, Settings>
      const aiSection = buildAiSettings(
        (stored['ai'] ?? defaultSettingsMap['ai'] ?? {}) as Settings
      ) as unknown as Record<string, unknown>
      aiSection[field] = value
      stored['ai'] = stripLegacyAutonomyMode(aiSection as Settings)
      writeGuiSettings(stored)
      const result = mirrorToRexConfig('ai', aiSection as Settings)
      if (result.ok) return { ok: true }
      try {
        writeGuiSettings(originalStored)
      } catch {
        // preserve mirror error
      }
      return { ok: false, error: result.error }
    }
  )

  ipcMain.handle(
    'rex:getApiKeys',
    async (): Promise<{
      openai_key_set: boolean
      openrouter_key_set: boolean
      error?: string
    }> => {
      try {
        const config = readRexConfigStrict()
        const hasKey = async (name: 'OPENAI_API_KEY' | 'OPENROUTER_API_KEY'): Promise<boolean> => {
          const context = apiKeyContext(name)
          const record = getVaultReference(config, name, context, session.userId)
          return record ? await vaultHasSecret(session, record.ref, context) : false
        }
        const [openaiKeySet, openrouterKeySet] = await Promise.all([
          hasKey('OPENAI_API_KEY'),
          hasKey('OPENROUTER_API_KEY')
        ])
        return { openai_key_set: openaiKeySet, openrouter_key_set: openrouterKeySet }
      } catch {
        return {
          openai_key_set: false,
          openrouter_key_set: false,
          error: 'Stored API-key state could not be verified'
        }
      }
    }
  )

  ipcMain.handle(
    'rex:setApiKey',
    async (_event, name: string, value: string): Promise<{ ok: boolean; error?: string }> => {
      if (!ALLOWED_API_KEYS.includes(name)) {
        return { ok: false, error: `Key "${name}" is not allowed` }
      }
      if (!value.trim()) return { ok: true }
      const context = apiKeyContext(name)
      let newRef: string | null = null
      let originalConfig: Record<string, unknown> | null = null
      let configWritten = false
      try {
        originalConfig = readRexConfigStrict()
        const nextConfig = JSON.parse(JSON.stringify(originalConfig)) as Record<string, unknown>
        const oldRecord = getVaultReference(originalConfig, name, context, session.userId)
        newRef = await vaultSetSecret(session, value, context)
        putVaultReference(nextConfig, name, newRef, context, session.userId)
        writeRexConfig(nextConfig)
        configWritten = true
        const readback = getVaultReference(
          readRexConfigStrict(),
          name,
          context,
          session.userId
        )
        if (readback?.ref !== newRef) {
          throw new SafeValidationError('Credential reference readback failed')
        }
        if (oldRecord) {
          await vaultDeleteSecret(session, oldRecord.ref, context).catch(() => false)
        }
        return { ok: true }
      } catch (err) {
        let restored = true
        if (configWritten && originalConfig) {
          try {
            writeRexConfig(originalConfig)
          } catch {
            restored = false
          }
        }
        if (newRef && restored) {
          await vaultDeleteSecret(session, newRef, context).catch(() => false)
        }
        return { ok: false, error: safeIpcErrorMessage(err, 'API key persistence failed') }
      }
    }
  )
}

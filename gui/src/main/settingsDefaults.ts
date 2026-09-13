import type {
  AiSettings,
  EmailAccount,
  GeneralSettings,
  IntegrationsSettings,
  Settings,
  SystemSettings,
  VoiceSettings
} from '../types/ipc'
import { normalizeAiModelRouting } from './aiSettings'

// ---------------------------------------------------------------------------
// Default settings per section
// ---------------------------------------------------------------------------

export const defaultSettingsMap: Record<string, Settings> = {
  general: {
    displayName: '',
    timezone: 'America/New_York',
    language: 'English',
    launchAtLogin: false,
    startMinimized: false
  } satisfies GeneralSettings,
  voice: {
    backgroundVoiceEnabled: false,
    microphoneDeviceIndex: null,
    speakerDeviceIndex: null,
    microphoneDeviceId: '',
    speakerDeviceId: '',
    ttsEngine: 'pyttsx3',
    ttsVoice: '',
    speechRate: 1.0,
    volume: 1.0,
    sttModel: 'base',
    sttLanguage: 'auto',
    sttDevice: 'auto',
    wakeWord: '',
    wakeWordBackend: 'openwakeword',
    customWakeWordId: '',
    wakeWordPhrase: 'hey rex',
    wakeWordModelPath: '',
    wakeWordEmbeddingPath: ''
  } satisfies VoiceSettings,
  ai: {
    model: 'gpt-4o',
    provider: 'openai',
    customModelId: '',
    openaiBaseUrl: '',
    ollamaBaseUrl: 'http://localhost:11434',
    openrouterModel: 'openai/gpt-4o',
    openrouterBaseUrl: 'https://openrouter.ai/api/v1',
    temperature: 0.7,
    maxTokens: 2048,
    systemPrompt: '',
    autonomyMode: 'manual',
    budgetPerPlan: 0,
    budgetPerStep: 0,
    modelRouting: normalizeAiModelRouting({}),
    personality: 'Friendly'
  } satisfies AiSettings as unknown as Settings,
  users: {
    names: {}
  },
  integrations: {
    emailProvider: 'gmail',
    emailClientId: '',
    emailClientSecret: '', // pragma: allowlist secret
    emailAccounts: [] as EmailAccount[],
    calendarProvider: 'gmail',
    calendarClientId: '',
    calendarClientSecret: '', // pragma: allowlist secret
    smsSid: '',
    smsAuthToken: '',
    smsFromNumber: '',
    haUrl: '',
    haToken: '',
    phoneSid: '',
    phoneAuthToken: '',
    phoneNumber: '',
    phoneTransferNumber: '',
    voicemailNotificationsEnabled: false,
    contactsFilePath: '',
    telegramBotToken: '',
    telegramChatId: '',
    openclawGatewayUrl: '',
    openclawToolsEnabled: false,
    openclawVoiceEnabled: false,
    openclawToken: '',
    credentialStatus: {}
  } satisfies IntegrationsSettings,
  system: {
    toolTimeoutSeconds: 10,
    requireConfirmSystemChanges: true,
    allowedFileRoots: '',
    debugLogging: false
  } satisfies SystemSettings
}

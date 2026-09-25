export interface StatusResponse {
  ok: boolean
  status?: string
}

export interface HomeAssistantConnectionResponse {
  ok: boolean
  error?: string
}

export interface HomeAssistantState {
  entity_id: string
  state: string
  friendly_name: string
  last_updated: string
}

export interface HomeAssistantStatesResponse extends HomeAssistantConnectionResponse {
  states?: HomeAssistantState[]
  not_configured?: boolean
}

export interface Settings {
  [key: string]: unknown
}

export interface SettingsResponse {
  ok: boolean
  settings?: Settings
}

export interface SetSettingsResponse {
  ok: boolean
  error?: string
}

export interface ContextSourceSetting {
  source_id: string
  source_type: string
  audience_scope: 'private' | 'household'
  context_enabled: boolean
  disclosure_policy: string
  mutable: boolean
}

export interface ContextUploadSetting {
  doc_id: string
  title: string
  audience_scope: 'private' | 'household'
  context_enabled: boolean
}

export interface ContextLocationSetting {
  location_assist: boolean
  shared_with: string[]
}

export interface ContextPrivacyResponse {
  ok: boolean
  sources?: ContextSourceSetting[]
  uploads?: ContextUploadSetting[]
  location?: ContextLocationSetting
  proactive_assistance?: boolean
  source?: ContextSourceSetting
  upload?: ContextUploadSetting
  error?: string
}

export type ModelDiscoveryProvider = 'ollama' | 'lmstudio'

export interface ModelDiscoveryResponse {
  ok: boolean
  models: string[]
  error?: string
}

export interface VoiceInfo {
  id: string
  name: string
  language: string
  gender: string | null
  engine?: string
}

export interface WakeWordInfo {
  id: string
  name: string
  engine: string
  has_sample?: boolean
  model_path?: string
}

export type WakeWordBackend = 'openwakeword' | 'custom_onnx' | 'custom_embedding'

export interface WakeWordStatus {
  requestedBackend: WakeWordBackend
  configuredPhrase: string
  fallbackEnabled: boolean
  fallbackKeyword: string
  assetKind: 'builtin' | 'onnx' | 'embedding'
  assetPath: string
  assetExists: boolean
  fallbackActive: boolean
  status: 'built_in' | 'asset_ready' | 'missing_asset'
  statusLabel: string
  detail: string
}

export interface WakeWordRuntimeStatus {
  reason: string
  configuredPhrase: string | null
  activePhrase: string | null
  configuredBackend: string | null
  activeBackend: string | null
  threshold: number | null
  fallbackActive: boolean
  fallbackPhrase: string | null
  detectorGeneration: number
  armed: boolean
  microphoneLabel: string | null
  portAudioDeviceIndex: number | null
}

export interface WakeWordAttemptEvidence {
  attemptCount: number
  latestConfidence: number | null
  maxConfidence: number | null
  threshold: number | null
  audioRms: number | null
  audioPeak: number | null
  rejectReason: string | null
  activePhrase: string | null
  activeBackend: string | null
  detectorGeneration: number
  accepted: boolean
  microphoneLabel: string | null
  portAudioDeviceIndex: number | null
}

export interface WakeWordRuntimeSnapshots {
  runtimeStatus: WakeWordRuntimeStatus | null
  attemptEvidence: WakeWordAttemptEvidence | null
}

export interface VoiceTranscriptEntry {
  text: string
  role: 'user' | 'rex'
  timestamp: number
}

export interface TaskRun {
  id: string
  taskId: string
  timestamp: string
  result: 'success' | 'failed'
  output: string[]
}

export interface Task {
  id: string
  name: string
  prompt: string
  schedule: string
  nextRun: string
  status: 'active' | 'paused' | 'error'
  lastRun?: { timestamp: string; result: 'success' | 'failed' }
}

export interface TaskInput {
  id?: string
  name: string
  prompt: string
  schedule: string
  active: boolean
}

export interface CalendarEvent {
  id: string
  title: string
  start: string // ISO date string
  end: string
  color?: string
  location?: string
  description?: string
  attendees?: string[]
  source?: 'rex' | 'synced'
}

export interface CalendarEventInput {
  title: string
  start: string // ISO date string
  end: string
  color?: string
  location?: string
  description?: string
}

export interface Reminder {
  id: string
  title: string
  notes?: string
  dueAt: string // ISO date string
  priority: 'low' | 'medium' | 'high'
  done: boolean
  repeat?: 'none' | 'daily' | 'weekly' | 'custom'
}

export interface ReminderInput {
  id?: string
  title: string
  notes?: string
  dueAt: string // ISO date string
  priority: 'low' | 'medium' | 'high'
  repeat: 'none' | 'daily' | 'weekly' | 'custom'
}

export interface Memory {
  id: string
  text: string
  category: string
  createdAt: string // ISO date string
  updatedAt: string // ISO date string
}

export interface MemoryUpdateInput {
  text: string
  category: string
}

export type ProcedureStatus = 'pending_approval' | 'active' | 'disabled' | 'revoked'

export interface ProcedureAuditEvent {
  timestamp: string
  event: string
  actorUserId: string
  reason: string | null
  evidenceRef: string | null
}

export interface ProcedureProvenance {
  actionId: string
  planId: string | null
  verificationId: string
  auditId: string
}

export interface Procedure {
  id: string
  name: string
  description: string
  ownerId: string
  scope: 'user' | 'household'
  status: ProcedureStatus
  capabilities: string[]
  requiredPermissions: string[]
  operation: 'read' | 'mutation'
  risk: 'safe' | 'sensitive' | 'prohibited'
  version: string
  dependencyFingerprint: string
  successCount: number
  failureCount: number
  lastValidatedAt: string
  expiresAt: string | null
  disabledReason: string | null
  approvalRequired: boolean
  approvedBy: string | null
  createdAt: string
  provenance: ProcedureProvenance
  auditHistory: ProcedureAuditEvent[]
}

export interface SystemSettings {
  toolTimeoutSeconds: number
  requireConfirmSystemChanges: boolean
  allowedFileRoots: string
  debugLogging: boolean
}

export interface GeneralSettings {
  displayName: string
  timezone: string
  language: string
  launchAtLogin: boolean
  startMinimized: boolean
}

export interface VoiceStartOptions {
  microphoneLabel?: string
}

export interface VoiceSettings {
  microphoneDeviceId: string
  speakerDeviceId: string
  ttsEngine: 'system' | 'openai' | 'elevenlabs' | 'xtts' | 'edge-tts' | 'pyttsx3'
  ttsVoice: string
  speechRate: number
  volume: number
  sttModel: string
  sttLanguage: string
  sttDevice: 'auto' | 'cpu' | 'cuda'
  wakeWord: string
  wakeWordBackend: WakeWordBackend
  customWakeWordId: string
  wakeWordPhrase: string
  wakeWordModelPath: string
  wakeWordEmbeddingPath: string
}

export interface AudioTargetInfo {
  id: string
  name: string
  provider: string
  kind: string
  room: string | null
  capabilities: string[]
  online: boolean
  health: string
}

export interface SpeakerGroupInfo {
  id: string
  name: string
  member_ids: string[]
  capabilities: string[]
}

export interface AudioTargetsResponse {
  ok: boolean
  targets?: AudioTargetInfo[]
  error?: string
}

export interface SpeakerGroupsResponse {
  ok: boolean
  groups?: SpeakerGroupInfo[]
  error?: string
}

export interface SpeakerGroupMutationResponse {
  ok: boolean
  group?: SpeakerGroupInfo
  error?: string
}

export interface VoiceEnrollment {
  user_id: string
  sample_count: number
  updated_at: string
  model_id: string
}

export interface AiModelRoutingSettings {
  default: string
  coding: string
  reasoning: string
  search: string
  vision: string
  fast: string
}

export interface AiSettings {
  model: string
  provider: 'openai' | 'openrouter' | 'ollama' | 'local'
  customModelId: string
  openaiBaseUrl: string
  ollamaBaseUrl: string
  openrouterModel: string
  openrouterBaseUrl: string
  temperature: number
  maxTokens: number
  systemPrompt: string
  autonomyMode: 'manual' | 'supervised' | 'full-auto'
  budgetPerPlan: number
  budgetPerStep: number
  modelRouting: AiModelRoutingSettings
  personality: string
}

export interface EmailAccount {
  id: string
  backend: 'imap' | 'gmail' | 'outlook'
  displayName: string
  // OAuth fields (gmail / outlook)
  clientId: string
  clientSecret: string
  // IMAP fields
  host: string
  port: number
  username: string
  password: string
  credentialRef?: string
  hasCredential?: boolean
  // State
  lastSynced?: string
}

export interface IntegrationsSettings {
  emailProvider: 'gmail' | 'outlook'
  emailClientId: string
  emailClientSecret: string
  emailAccounts: EmailAccount[]
  calendarProvider: 'gmail' | 'outlook'
  calendarClientId: string
  calendarClientSecret: string
  smsSid: string
  smsAuthToken: string
  smsFromNumber: string
  haUrl: string
  haToken: string
  // Phone / Twilio (US-PH-004)
  phoneSid: string
  phoneAuthToken: string
  phoneNumber: string
  phoneTransferNumber: string
  voicemailNotificationsEnabled: boolean
  contactsFilePath: string
  // Telegram
  telegramBotToken: string
  telegramChatId: string
  // OpenClaw
  openclawGatewayUrl: string
  openclawToolsEnabled: boolean
  openclawVoiceEnabled: boolean
  openclawToken: string
  credentialStatus: Record<string, { ref: string; hasCredential: boolean }>
}

export type IntegrationConnectionStatus =
  | 'unavailable'
  | 'unconfigured'
  | 'configured'
  | 'reachable'
  | 'authenticated'
  | 'degraded'
  | 'read_only'
  | 'write_capable'
  | 'write_tested'
  | 'verified'

export interface IntegrationStatus {
  state: IntegrationConnectionStatus
  testedAt?: string
  error?: string
}

export interface IntegrationInventoryItem extends IntegrationStatus {
  name: string
  key: string
  configured: boolean
  available: boolean
  read_capable: boolean
  write_capable: boolean
  configure_url?: string
  testable?: boolean
  detail: string
  next_action: string
}

export interface IntegrationInventoryResponse {
  ok: boolean
  integrations: IntegrationInventoryItem[]
  error?: string
}

export interface CapabilityInfo {
  name: string
  description: string
  category: string
  enabled: boolean
  state?: IntegrationConnectionStatus
  read_capable?: boolean
  write_capable?: boolean
}

export interface CapabilitiesResponse {
  ok: boolean
  capabilities: CapabilityInfo[]
  error?: string
}

export interface NotificationsSettings {
  quietHoursEnabled: boolean
  quietHoursStart: string
  quietHoursEnd: string
  digestModeEnabled: boolean
  digestDeliveryTime: string
  highPriorityThreshold: 'critical_only' | 'high_and_critical'
  autoEscalationDelay: number
  desktopNotificationsEnabled: boolean
  soundAlertsEnabled: boolean
}

export interface VersionInfo {
  rex: string
  electron: string
  node: string
}

export interface AppStatus {
  version: string
  python_version: string
  platform: string
}

export interface CommandHistoryEntry {
  id: number
  timestamp: string
  command: string
  result: string
  success: boolean
}

export interface ShoppingItem {
  id: string
  name: string
  quantity: number
  unit: string
  added_by: string
  checked: boolean
  added_at: string
  checked_at: string | null
}

export interface QuickAction {
  id: string
  label: string
  command: string
}

export type QuickActionRunStatus = 'attempted' | 'completed' | 'verified' | 'failed'

export interface QuickActionRunResponse {
  status: QuickActionRunStatus
  detail?: string
}

export interface Device {
  entity_id: string
  name: string
  type: string
}

export interface DevicesResponse {
  ok: boolean
  devices: Device[]
  error?: string
}

export type DeviceCommandStatus =
  | 'verified'
  | 'attempted_unverified'
  | 'confirmation_required'
  | 'denied'
  | 'failed'

export interface DeviceCommandResponse {
  status: DeviceCommandStatus
  detail?: string
  expected?: { state: string; attributes: Record<string, unknown> } | null
  actual?: Record<string, unknown> | null
  latencyMs?: number
  confirmationToken?: string
  requestId?: string
}

export interface FileExtractResult {
  ok: boolean
  isImage: boolean
  extractedText?: string
  error?: string
}

export interface PreferenceSuggestion {
  field: string
  current_value: string | number
  suggested_value: string | number
  reason: string
}

export type EmailPriority = 'low' | 'medium' | 'high' | 'critical'

export interface EmailMessage {
  id: string
  thread_id: string
  subject: string
  sender: string
  recipients: string[]
  body_text: string
  body_html?: string
  received_at: string // ISO date string
  labels: string[]
  is_read: boolean
  priority: EmailPriority
}

export type SMSDirection = 'inbound' | 'outbound'
export type SMSStatus = 'sent' | 'delivered' | 'failed' | 'stub'

export interface SMSMessage {
  id: string
  thread_id: string
  direction: SMSDirection
  body: string
  from_number: string
  to_number: string
  sent_at: string // ISO date string
  status: SMSStatus
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

export type NotificationPriority = 'low' | 'medium' | 'high' | 'critical'
export type NotificationChannel = 'desktop' | 'digest' | 'sms' | 'email'

export interface GuiNotification {
  id: string
  title: string
  body: string
  source: string
  priority: NotificationPriority
  channel: NotificationChannel
  digest_eligible: boolean
  quiet_hours_exempt: boolean
  created_at: string // ISO date string
  delivered_at?: string
  read_at?: string
  escalation_due_at?: string
  action_url?: string
  action_label?: string
}

export interface SMSThread {
  id: string
  contact_name: string
  contact_number: string
  messages: SMSMessage[]
  last_message_at: string // ISO date string
  unread_count: number
}

export interface TimeSlot {
  start: string // ISO date string
  end: string   // ISO date string
  confidence: number // 0–1
}

export interface FindMeetingSlotsParams {
  durationMinutes: number
  earliest: string  // ISO date string
  latest: string    // ISO date string
  timezone: string
}


export interface LogEntry {
  timestamp: string
  level: string
  logger: string
  message: string
  extra: Record<string, unknown>
  raw?: string
}

export interface LogsResponse {
  ok: boolean
  entries: LogEntry[]
  log_path?: string
  legacy_log_path?: string
  log_source?: string
  timestamp_basis?: string
  error?: string
}

export interface UsageBucket {
  requests: number
  tokens: number
}

export interface UsagePeriodSplit {
  local: UsageBucket
  cloud: UsageBucket
}

export interface UsageSummary {
  ok: boolean
  local: UsageBucket
  cloud: UsageBucket
  by_period: {
    today: UsagePeriodSplit
    week: UsagePeriodSplit
    month: UsagePeriodSplit
  }
  error?: string
}

export interface SetupStatusResponse {
  needs_setup: boolean
}

export interface SetupAudioDevice {
  index: number
  name: string
  max_input_channels: number
  max_output_channels: number
}

export interface SetupAudioDevicesResponse {
  ok: boolean
  devices: SetupAudioDevice[]
  error?: string
}

export interface SetupAudioTestResponse {
  ok: boolean
  error?: string
}

export interface SetupCompletePayload {
  username: string
  password: string
  llm_provider: string
  llm_api_key?: string
  tts_provider: string
  tts_voice_id: string
  microphone_device_index: number | null
  speaker_device_index: number | null
  local_device_id: string
  wake_word_id: string
  room_name: string
  background_voice_enabled: boolean
  ha_base_url: string
  ha_token: string
  defer_home_assistant?: boolean
}

export interface SetupCompleteResponse {
  ok: boolean
  user_id?: string
  error?: string
  setup_saved?: boolean
  runtime_ready?: boolean
  warning?: string
}

export interface PairingChallenge {
  type: 'askrex-pairing'
  version: number
  desktop_id: string
  challenge_id: string
  nonce: string
  code: string
  user_id: string
  scopes: string[]
  created_at: string
  expires_at: string
}

export interface PendingPairingRequest {
  request_id: string
  desktop_id: string
  user_id: string
  device_name: string
  platform: string
  key_thumbprint: string
  scopes: string[]
  submitted_at: string
  status: string
}

export interface PairedDevice {
  device_id: string
  desktop_id: string
  user_id: string
  device_name: string
  platform: string
  key_thumbprint: string
  created_at: string
  revoked_at: string | null
  grant_id: string | null
  grant_version: number | null
  scopes: string[]
  grant_expires_at: string | null
  grant_revoked_at: string | null
}

export interface PairingResponse<T = undefined> {
  ok: boolean
  error?: string
  challenge?: PairingChallenge
  requests?: PendingPairingRequest[]
  devices?: PairedDevice[]
  desktop_id?: string
  grant?: T
  revoked?: boolean
}

// ---------------------------------------------------------------------------
// User Profile
// ---------------------------------------------------------------------------

export type ProfileScopeLabel =
  | 'user-private'
  | 'shared'

export interface UserProfile {
  user_id: string
  name: string
  initials: string
  role: string
  permissions: string[]
  preferences: Record<string, unknown>
  voice_enrolled: boolean
  voice_model_id: string | null
  voice_sample_count: number
  voice_updated_at: string | null
  avatar_present: boolean
  avatar_mime_type: string | null
  avatar_data: string | null
  scope_labels: Record<string, ProfileScopeLabel>
}

export interface ProfileOperationResponse {
  ok: boolean
  error?: string
  profile?: UserProfile
}

// AbortSignal instances do not survive Electron's contextBridge isolation
// boundary with their EventTarget prototype methods intact (calling
// addEventListener/removeEventListener on a signal that crossed the bridge
// throws "is not a function"). Callers pass a plain callback-shaped handle
// instead, built from a real AbortSignal in the renderer's own realm, since
// function arguments are proxied correctly across the bridge.
export interface ChatStreamCancelHandle {
  isAborted: () => boolean
  onAbort: (cb: () => void) => void
  offAbort: (cb: () => void) => void
}

export type TurnStatusValue =
  | 'thinking'
  | 'checking'
  | 'acting'
  | 'verifying'
  | 'speaking'
  | 'done'
  | 'error'
  | 'cancelled'

export interface TurnStatusUpdate {
  turnId: string
  sequence: number
  status: TurnStatusValue
  terminal: boolean
}

export type ChatStreamStatus = 'model_failure'

export interface ChatRecoveryAction {
  kind: string
  label: string
  detail: string
  source: string
  target?: string
  targets?: string[]
  settings_route?: string
  required_permissions?: string[]
  requires_confirmation: boolean
}

export interface ChatRecoveryPlan {
  message: string
  actions: ChatRecoveryAction[]
  searched_sources: string[]
  blocked: boolean
}

export interface RexAPI {
  sendChat: (message: string) => Promise<string>
  sendChatStream: (
    message: string,
    onToken: (token: string) => void,
    cancel?: ChatStreamCancelHandle,
    onStatus?: (status: ChatStreamStatus) => void,
    onRecovery?: (recovery: ChatRecoveryPlan) => void
  ) => Promise<void>
  getStatus: () => Promise<StatusResponse>
  onStatusChange: (cb: (status: string) => void) => (() => void)
  onTurnStatus: (cb: (update: TurnStatusUpdate) => void) => (() => void)
  getSettings: (section: string) => Promise<Settings>
  getContextPrivacy: () => Promise<ContextPrivacyResponse>
  updateContextPrivacy: (
    command: string,
    payload: Record<string, unknown>
  ) => Promise<ContextPrivacyResponse>
  discoverAiModels: (provider: ModelDiscoveryProvider) => Promise<ModelDiscoveryResponse>
  setSettings: (section: string, values: Settings) => Promise<SetSettingsResponse>
  removeEmailAccount: (id: string, confirmed: boolean) => Promise<SetSettingsResponse>
  startVoice: (
    onStateChange: (state: string) => void,
    onTranscript: (entry: VoiceTranscriptEntry) => void,
    onError: (error: string) => void,
    onStatus?: (status: string, label: string) => void,
    options?: VoiceStartOptions
  ) => Promise<void>
  attachVoiceSession: (
    onStateChange: (state: string) => void,
    onTranscript: (entry: VoiceTranscriptEntry) => void,
    onError: (error: string) => void,
    onStatus?: (status: string, label: string) => void,
    onWakeWordRuntimeStatus?: (status: WakeWordRuntimeStatus) => void,
    onWakeWordAttemptEvidence?: (evidence: WakeWordAttemptEvidence) => void
  ) => (() => void)
  stopVoice: () => Promise<void>
  getWakeWordRuntimeSnapshots: () => Promise<WakeWordRuntimeSnapshots>
  getTasks: () => Promise<Task[]>
  saveTask: (task: TaskInput) => Promise<Task>
  deleteTask: (taskId: string) => Promise<void>
  setTaskEnabled: (taskId: string, enabled: boolean) => Promise<Task>
  getTaskHistory: (taskId: string) => Promise<TaskRun[]>
  getCalendarEvents: (start: string, end: string) => Promise<CalendarEvent[]>
  createCalendarEvent: (event: CalendarEventInput) => Promise<CalendarEvent>
  updateCalendarEvent: (event: CalendarEvent) => Promise<CalendarEvent>
  deleteCalendarEvent: (id: string) => Promise<void>
  getReminders: () => Promise<Reminder[]>
  completeReminder: (id: string) => Promise<void>
  saveReminder: (reminder: ReminderInput) => Promise<Reminder>
  deleteReminder: (id: string) => Promise<void>
  getMemories: () => Promise<Memory[]>
  addMemory: (data: MemoryUpdateInput) => Promise<Memory>
  updateMemory: (id: string, data: MemoryUpdateInput) => Promise<Memory>
  deleteMemory: (id: string) => Promise<void>
  getProcedures: () => Promise<Procedure[]>
  approveProcedure: (id: string) => Promise<Procedure>
  disableProcedure: (id: string) => Promise<Procedure>
  revokeProcedure: (id: string) => Promise<Procedure>
  deleteProcedure: (id: string) => Promise<void>
  getVersionInfo: () => Promise<VersionInfo>
  getAppStatus: () => Promise<AppStatus>
  getCommandHistory: (
    limit?: number
  ) => Promise<{ ok: boolean; history: CommandHistoryEntry[]; error?: string }>
  testVoice: (settings: VoiceSettings) => Promise<{ ok: boolean; error?: string }>
  testIntegration: (type: 'email' | 'calendar' | 'sms' | 'homeassistant' | 'phone' | 'openclaw') => Promise<{ ok: boolean; state?: IntegrationConnectionStatus; error?: string }>
  getIntegrations: () => Promise<IntegrationInventoryResponse>
  getCapabilities: () => Promise<CapabilitiesResponse>
  testHomeAssistant: (baseUrl: string, token: string) => Promise<HomeAssistantConnectionResponse>
  saveHomeAssistant: (baseUrl: string, token: string) => Promise<HomeAssistantConnectionResponse>
  getHomeAssistantStates: () => Promise<HomeAssistantStatesResponse>
  listQuickActions: () => Promise<{ ok: boolean; quick_actions: QuickAction[]; error?: string }>
  createQuickAction: (label: string, commandText: string) => Promise<{ ok: boolean; action?: QuickAction; error?: string }>
  deleteQuickAction: (id: string) => Promise<{ ok: boolean; deleted?: boolean; error?: string }>
  runQuickAction: (id: string) => Promise<QuickActionRunResponse>
  getDevices: () => Promise<DevicesResponse>
  sendDeviceCommand: (entityId: string, command: string, payload?: { value?: number }, confirmationToken?: string, requestId?: string) => Promise<DeviceCommandResponse>
  uploadContactsFile: () => Promise<{ ok: boolean; path?: string; error?: string }>
  pickFolder: () => Promise<{ ok: boolean; path?: string; error?: string }>
  testEmailAccount: (id: string) => Promise<{ ok: boolean; state?: IntegrationConnectionStatus; error?: string }>
  getPreferenceSuggestions: () => Promise<PreferenceSuggestion[]>
  applyPreferenceSuggestion: (field: string, value: string | number) => Promise<{ ok: boolean }>
  getEmailInbox: () => Promise<EmailMessage[]>
  generateEmailReply: (id: string) => Promise<string>
  findMeetingSlots: (params: FindMeetingSlotsParams) => Promise<TimeSlot[]>
  getSMSThreads: () => Promise<SMSThread[]>
  getSMSThread: (threadId: string) => Promise<SMSThread>
  sendSMS: (to: string, body: string) => Promise<SMSMessage>
  getNotifications: () => Promise<GuiNotification[]>
  markNotificationRead: (id: string) => Promise<void>
  dismissNotification: (id: string) => Promise<void>
  getUnreadNotificationCount: () => Promise<number>
  onNewNotification: (cb: (notification: GuiNotification) => void) => void
  listWakeWords: () => Promise<{ ok: boolean; wake_words: WakeWordInfo[]; error?: string; warning?: string }>
  getSetupWakeWordStatus: (wakeWordId: string) => Promise<WakeWordStatus>
  getWakeWordStatus: (settings?: VoiceSettings) => Promise<WakeWordStatus>
  previewWakeWordSample: (
    wakeWordId: string
  ) => Promise<{ ok: boolean; audio_base64?: string; has_sample?: boolean; error?: string }>
  trainWakeWord: (
    phrase: string,
    positiveSamples: number[][],
    negativeSamples: number[][]
  ) => Promise<{ ok: boolean; model_path?: string; phrase?: string; error?: string }>
  listVoices: (provider: string) => Promise<{ ok: boolean; voices: VoiceInfo[]; error?: string }>
  previewVoice: (
    provider: string,
    voiceId: string
  ) => Promise<{ ok: boolean; audio_base64?: string; error?: string }>
  synthesizeSpeech: (
    provider: string,
    voiceId: string,
    text: string
  ) => Promise<{ ok: boolean; audio_base64?: string; error?: string }>
  logVoiceTiming: (
    turnId: string,
    stage: string,
    durationMs: number
  ) => Promise<{ ok: boolean }>
  uploadCustomVoice: (
    filePath: string,
    voiceName: string
  ) => Promise<{ ok: boolean; voice_id?: string; voice_name?: string; duration?: number; error?: string }>
  getVoiceEnrollments: () => Promise<{
    ok: boolean
    active_user_id: string
    enrollments: VoiceEnrollment[]
    error?: string
  }>
  enrollVoice: (
    userId: string,
    samples: number[][]
  ) => Promise<{ ok: boolean; enrollment?: VoiceEnrollment; error?: string }>
  deleteVoiceEnrollment: (userId: string) => Promise<{ ok: boolean; deleted?: boolean; error?: string }>
  sendChatAudio: (
    audioBase64: string
  ) => Promise<{ ok: boolean; transcript?: string; error?: string }>
  getApiKeys: () => Promise<{ openai_key_set: boolean; openrouter_key_set: boolean; error?: string }>
  setApiKey: (name: string, value: string) => Promise<{ ok: boolean; error?: string }>
  getAudioTargets: () => Promise<AudioTargetsResponse>
  refreshAudioTargets: () => Promise<AudioTargetsResponse>
  listSpeakerGroups: () => Promise<SpeakerGroupsResponse>
  createSpeakerGroup: (name: string, memberIds: string[]) => Promise<SpeakerGroupMutationResponse>
  renameSpeakerGroup: (groupId: string, name: string) => Promise<SpeakerGroupMutationResponse>
  setSpeakerGroupMembers: (groupId: string, memberIds: string[]) => Promise<SpeakerGroupMutationResponse>
  deleteSpeakerGroup: (groupId: string) => Promise<{ ok: boolean; deleted?: boolean; error?: string }>
  restartRex: () => Promise<{ ok: boolean; error?: string }>
  resetToDefaults: () => Promise<{ ok: boolean; error?: string }>
  extractFileForChat: (params: {
    filename: string
    dataBase64: string
    mimeType: string
    sizeBytes: number
  }) => Promise<FileExtractResult>
  getShoppingItems: () => Promise<{ ok: boolean; items: ShoppingItem[]; error?: string }>
  addShoppingItem: (name: string, quantity: number, unit: string) => Promise<{ ok: boolean; item?: ShoppingItem; error?: string }>
  checkShoppingItem: (id: string) => Promise<{ ok: boolean; error?: string }>
  uncheckShoppingItem: (id: string) => Promise<{ ok: boolean; error?: string }>
  clearCheckedShoppingItems: () => Promise<{ ok: boolean; count?: number; error?: string }>
  getLogs: (limit?: number) => Promise<LogsResponse>
  startLogTail: () => Promise<{ ok: boolean; log_path?: string; error?: string }>
  stopLogTail: () => Promise<{ ok: boolean }>
  downloadLogs: () => Promise<{ ok: boolean; content?: string; filename?: string; log_path?: string; error?: string }>
  onLogEntry: (cb: (entry: LogEntry) => void) => void
  getUsage: () => Promise<UsageSummary>
  getSetupStatus: () => Promise<SetupStatusResponse>
  getSetupAudioDevices: () => Promise<SetupAudioDevicesResponse>
  testSetupAudioDevice: (kind: 'microphone' | 'speaker', deviceIndex: number) => Promise<SetupAudioTestResponse>
  completeSetup: (payload: SetupCompletePayload) => Promise<SetupCompleteResponse>
  createPairingChallenge: (scopes: string[]) => Promise<PairingResponse>
  listPendingPairings: () => Promise<PairingResponse>
  approvePairing: (requestId: string) => Promise<PairingResponse>
  denyPairing: (requestId: string) => Promise<PairingResponse>
  listPairedDevices: () => Promise<PairingResponse>
  revokePairedDevice: (deviceId: string) => Promise<PairingResponse>
  getProfile: () => Promise<ProfileOperationResponse>
  updateProfilePreferences: (preferences: Record<string, unknown>) => Promise<ProfileOperationResponse>
  setProfileAvatar: (mimeType: string, avatarBase64: string) => Promise<ProfileOperationResponse>
  removeProfileAvatar: () => Promise<ProfileOperationResponse>
}

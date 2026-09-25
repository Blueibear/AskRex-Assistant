import { contextBridge, ipcRenderer } from 'electron'
import type { IpcRendererEvent } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
import type {
  Settings,
  SetSettingsResponse,
  VoiceTranscriptEntry,
  VoiceInfo,
  VoiceEnrollment,
  Task,
  TaskInput,
  TaskRun,
  CalendarEvent,
  CalendarEventInput,
  Reminder,
  ReminderInput,
  Memory,
  MemoryUpdateInput,
  Procedure,
  VersionInfo,
  AppStatus,
  CommandHistoryEntry,
  VoiceSettings,
  PreferenceSuggestion,
  EmailMessage,
  FindMeetingSlotsParams,
  TimeSlot,
  SMSThread,
  SMSMessage,
  GuiNotification,
  AudioTargetsResponse,
  SpeakerGroupsResponse,
  SpeakerGroupMutationResponse,
  FileExtractResult,
  ShoppingItem,
  WakeWordInfo,
  WakeWordStatus,
  WakeWordRuntimeStatus,
  WakeWordAttemptEvidence,
  WakeWordRuntimeSnapshots,
  LogEntry,
  LogsResponse,
  UsageSummary,
  DevicesResponse,
  DeviceCommandResponse,
  QuickAction,
  QuickActionRunResponse,
  SetupStatusResponse,
  SetupAudioDevicesResponse,
  SetupAudioTestResponse,
  SetupCompletePayload,
  SetupCompleteResponse,
  IntegrationConnectionStatus,
  ChatStreamCancelHandle,
  ChatStreamStatus,
  ChatRecoveryPlan,
  TurnStatusUpdate,
  TurnStatusValue,
  VoiceStartOptions,
  PairingResponse,
  ProfileOperationResponse,
  ModelDiscoveryProvider,
  ModelDiscoveryResponse,
  ContextPrivacyResponse
} from '../types/ipc'

function makeSendChatStream(
  message: string,
  onToken: (token: string) => void,
  cancel?: ChatStreamCancelHandle,
  onStatus?: (status: ChatStreamStatus) => void,
  onRecovery?: (recovery: ChatRecoveryPlan) => void
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const streamId = `${Date.now()}-${Math.random()}`

    function tokenHandler(_e: unknown, data: { streamId: string; token: string }): void {
      if (data.streamId === streamId) onToken(data.token)
    }
    function statusHandler(
      _e: unknown,
      data: { streamId: string; status: ChatStreamStatus }
    ): void {
      if (data.streamId === streamId) onStatus?.(data.status)
    }
    function recoveryHandler(
      _e: unknown,
      data: { streamId: string; recovery: ChatRecoveryPlan }
    ): void {
      if (data.streamId === streamId) onRecovery?.(data.recovery)
    }
    function doneHandler(_e: unknown, data: { streamId: string }): void {
      if (data.streamId === streamId) {
        cleanup()
        resolve()
      }
    }
    function errorHandler(_e: unknown, data: { streamId: string; error: string }): void {
      if (data.streamId === streamId) {
        cleanup()
        reject(new Error(data.error))
      }
    }

    function cleanup(): void {
      ipcRenderer.removeListener('rex:chatToken', tokenHandler)
      ipcRenderer.removeListener('rex:chatStatus', statusHandler)
      ipcRenderer.removeListener('rex:chatRecovery', recoveryHandler)
      ipcRenderer.removeListener('rex:chatDone', doneHandler)
      ipcRenderer.removeListener('rex:chatError', errorHandler)
      if (typeof cancel?.offAbort === 'function') cancel.offAbort(abortHandler)
    }

    function abortHandler(): void {
      cleanup()
      void ipcRenderer.invoke('rex:cancelChatStream', streamId)
      reject(new DOMException('Chat response cancelled', 'AbortError'))
    }

    ipcRenderer.on('rex:chatToken', tokenHandler)
    ipcRenderer.on('rex:chatStatus', statusHandler)
    ipcRenderer.on('rex:chatRecovery', recoveryHandler)
    ipcRenderer.on('rex:chatDone', doneHandler)
    ipcRenderer.on('rex:chatError', errorHandler)
    // Defensive: a malformed cancel handle (e.g. a raw AbortSignal-shaped
    // object degraded by the contextBridge boundary, missing these methods)
    // must degrade to "no cancellation available" rather than crash the turn.
    if (typeof cancel?.isAborted === 'function' && cancel.isAborted()) {
      abortHandler()
      return
    }
    if (typeof cancel?.onAbort === 'function') cancel.onAbort(abortHandler)

    ipcRenderer.invoke('rex:startChatStream', { message, streamId }).catch((err: unknown) => {
      cleanup()
      reject(err)
    })
  })
}

// Module-level cleanup reference for active voice session.
let voiceCleanup: (() => void) | null = null

function attachVoiceSession(
  onStateChange: (state: string) => void,
  onTranscript: (entry: VoiceTranscriptEntry) => void,
  onError: (error: string) => void,
  onStatus?: (status: string, label: string) => void,
  onWakeWordRuntimeStatus?: (status: WakeWordRuntimeStatus) => void,
  onWakeWordAttemptEvidence?: (evidence: WakeWordAttemptEvidence) => void
): (() => void) {
  if (voiceCleanup) {
    voiceCleanup()
    voiceCleanup = null
  }

  function stateHandler(_e: unknown, data: { state: string }): void {
    onStateChange(data.state)
  }
  function transcriptHandler(_e: unknown, data: VoiceTranscriptEntry): void {
    onTranscript(data)
  }
  function errorHandler(_e: unknown, data: { error: string }): void {
    onError(data.error)
  }
  function statusHandler(_e: unknown, data: { status: string; label?: string }): void {
    onStatus?.(data.status, data.label ?? data.status)
  }
  function wakeWordRuntimeStatusHandler(_e: unknown, data: WakeWordRuntimeStatus): void {
    onWakeWordRuntimeStatus?.(data)
  }
  function wakeWordAttemptEvidenceHandler(_e: unknown, data: WakeWordAttemptEvidence): void {
    onWakeWordAttemptEvidence?.(data)
  }

  function cleanup(): void {
    ipcRenderer.removeListener('rex:voiceState', stateHandler)
    ipcRenderer.removeListener('rex:voiceTranscript', transcriptHandler)
    ipcRenderer.removeListener('rex:voiceError', errorHandler)
    ipcRenderer.removeListener('rex:voiceStatus', statusHandler)
    ipcRenderer.removeListener('rex:wakeWordRuntimeStatus', wakeWordRuntimeStatusHandler)
    ipcRenderer.removeListener('rex:wakeWordAttemptEvidence', wakeWordAttemptEvidenceHandler)
    if (voiceCleanup === cleanup) {
      voiceCleanup = null
    }
  }

  voiceCleanup = cleanup

  ipcRenderer.on('rex:voiceState', stateHandler)
  ipcRenderer.on('rex:voiceTranscript', transcriptHandler)
  ipcRenderer.on('rex:voiceError', errorHandler)
  ipcRenderer.on('rex:voiceStatus', statusHandler)
  ipcRenderer.on('rex:wakeWordRuntimeStatus', wakeWordRuntimeStatusHandler)
  ipcRenderer.on('rex:wakeWordAttemptEvidence', wakeWordAttemptEvidenceHandler)

  return cleanup
}

function makeStartVoice(
  onStateChange: (state: string) => void,
  onTranscript: (entry: VoiceTranscriptEntry) => void,
  onError: (error: string) => void,
  onStatus?: (status: string, label: string) => void,
  options?: VoiceStartOptions
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const cleanup =
      voiceCleanup === null
        ? attachVoiceSession(onStateChange, onTranscript, onError, onStatus)
        : null

    ipcRenderer
      .invoke('rex:startVoice', options ?? {})
      .then((result: { ok: boolean; error?: string }) => {
        if (result.ok) {
          resolve()
        } else {
          cleanup?.()
          reject(new Error(result.error ?? 'Failed to start voice'))
        }
      })
      .catch((err: unknown) => {
        cleanup?.()
        reject(err)
      })
  })
}

function stopVoice(): Promise<void> {
  if (voiceCleanup) {
    voiceCleanup()
    voiceCleanup = null
  }
  return ipcRenderer.invoke('rex:stopVoice').then(() => undefined)
}

const rexAPI = {
  sendChat: (message: string) => ipcRenderer.invoke('rex:sendChat', message),
  sendChatStream: makeSendChatStream,
  getStatus: () => ipcRenderer.invoke('rex:getStatus'),
  onStatusChange: (cb: (status: string) => void): (() => void) => {
    const handler = (_event: IpcRendererEvent, payload: { state?: string }): void => {
      cb(payload.state ?? 'idle')
    }
    ipcRenderer.on('rex:voiceState', handler)
    return () => ipcRenderer.removeListener('rex:voiceState', handler)
  },
  onTurnStatus: (cb: (update: TurnStatusUpdate) => void): (() => void) => {
    const handler = (
      _event: IpcRendererEvent,
      payload: {
        turn_id: string
        sequence: number
        status: TurnStatusValue
        terminal: boolean
      }
    ): void => {
      cb({
        turnId: payload.turn_id,
        sequence: payload.sequence,
        status: payload.status,
        terminal: payload.terminal
      })
    }
    ipcRenderer.on('rex:turnStatus', handler)
    return () => ipcRenderer.removeListener('rex:turnStatus', handler)
  },
  getSettings: (section: string): Promise<Settings> =>
    ipcRenderer.invoke('rex:getSettings', section),
  getContextPrivacy: (): Promise<ContextPrivacyResponse> =>
    ipcRenderer.invoke('rex:getContextPrivacy'),
  updateContextPrivacy: (
    command: string,
    payload: Record<string, unknown>
  ): Promise<ContextPrivacyResponse> => ipcRenderer.invoke('rex:updateContextPrivacy', command, payload),
  discoverAiModels: (provider: ModelDiscoveryProvider): Promise<ModelDiscoveryResponse> =>
    ipcRenderer.invoke('rex:discoverAiModels', provider),
  setSettings: (section: string, values: Settings): Promise<SetSettingsResponse> =>
    ipcRenderer.invoke('rex:setSettings', section, values),
  removeEmailAccount: (id: string, confirmed: boolean): Promise<SetSettingsResponse> =>
    ipcRenderer.invoke('rex:removeEmailAccount', id, confirmed),
  attachVoiceSession,
  startVoice: makeStartVoice,
  stopVoice,
  getWakeWordRuntimeSnapshots: (): Promise<WakeWordRuntimeSnapshots> =>
    ipcRenderer.invoke('rex:getWakeWordRuntimeSnapshots'),
  getTasks: (): Promise<Task[]> => ipcRenderer.invoke('rex:getTasks'),
  saveTask: (task: TaskInput): Promise<Task> => ipcRenderer.invoke('rex:saveTask', task),
  deleteTask: (taskId: string): Promise<void> => ipcRenderer.invoke('rex:deleteTask', taskId),
  setTaskEnabled: (taskId: string, enabled: boolean): Promise<Task> =>
    ipcRenderer.invoke('rex:setTaskEnabled', taskId, enabled),
  getTaskHistory: (taskId: string): Promise<TaskRun[]> =>
    ipcRenderer.invoke('rex:getTaskHistory', taskId),
  getCalendarEvents: (start: string, end: string): Promise<CalendarEvent[]> =>
    ipcRenderer.invoke('rex:getCalendarEvents', start, end),
  createCalendarEvent: (event: CalendarEventInput): Promise<CalendarEvent> =>
    ipcRenderer.invoke('rex:createCalendarEvent', event),
  updateCalendarEvent: (event: CalendarEvent): Promise<CalendarEvent> =>
    ipcRenderer.invoke('rex:updateCalendarEvent', event),
  deleteCalendarEvent: (id: string): Promise<void> =>
    ipcRenderer.invoke('rex:deleteCalendarEvent', id),
  getReminders: (): Promise<Reminder[]> => ipcRenderer.invoke('rex:getReminders'),
  completeReminder: (id: string): Promise<void> =>
    ipcRenderer.invoke('rex:completeReminder', id).then(() => undefined),
  saveReminder: (reminder: ReminderInput): Promise<Reminder> =>
    ipcRenderer.invoke('rex:saveReminder', reminder),
  deleteReminder: (id: string): Promise<void> =>
    ipcRenderer.invoke('rex:deleteReminder', id).then(() => undefined),
  getMemories: (): Promise<Memory[]> => ipcRenderer.invoke('rex:getMemories'),
  addMemory: (data: MemoryUpdateInput): Promise<Memory> =>
    ipcRenderer.invoke('rex:addMemory', data),
  updateMemory: (id: string, data: MemoryUpdateInput): Promise<Memory> =>
    ipcRenderer.invoke('rex:updateMemory', id, data),
  deleteMemory: (id: string): Promise<void> =>
    ipcRenderer.invoke('rex:deleteMemory', id).then(() => undefined),
  getProcedures: (): Promise<Procedure[]> => ipcRenderer.invoke('rex:getProcedures'),
  approveProcedure: (id: string): Promise<Procedure> =>
    ipcRenderer.invoke('rex:approveProcedure', id),
  disableProcedure: (id: string): Promise<Procedure> =>
    ipcRenderer.invoke('rex:disableProcedure', id),
  revokeProcedure: (id: string): Promise<Procedure> =>
    ipcRenderer.invoke('rex:revokeProcedure', id),
  deleteProcedure: (id: string): Promise<void> =>
    ipcRenderer.invoke('rex:deleteProcedure', id).then(() => undefined),
  getVersionInfo: (): Promise<VersionInfo> => ipcRenderer.invoke('rex:getVersionInfo'),
  getAppStatus: (): Promise<AppStatus> => ipcRenderer.invoke('rex:getAppStatus'),
  getCommandHistory: (
    limit = 50
  ): Promise<{ ok: boolean; history: CommandHistoryEntry[]; error?: string }> =>
    ipcRenderer.invoke('rex:getCommandHistory', limit),
  testVoice: (settings: VoiceSettings): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:testVoice', settings),
  testIntegration: (type: 'email' | 'calendar' | 'sms' | 'homeassistant' | 'phone' | 'openclaw'): Promise<{ ok: boolean; state?: IntegrationConnectionStatus; error?: string }> =>
    ipcRenderer.invoke('rex:testIntegration', type),
  getIntegrations: () => ipcRenderer.invoke('rex:getIntegrations'),
  getCapabilities: () => ipcRenderer.invoke('rex:getCapabilities'),
  testHomeAssistant: (baseUrl: string, token: string): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:testHomeAssistant', baseUrl, token),
  saveHomeAssistant: (baseUrl: string, token: string): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:saveHomeAssistant', baseUrl, token),
  getHomeAssistantStates: (): Promise<{
    ok: boolean
    states?: Array<{ entity_id: string; state: string; friendly_name: string; last_updated: string }>
    not_configured?: boolean
    error?: string
  }> => ipcRenderer.invoke('rex:getHomeAssistantStates'),
  uploadContactsFile: (): Promise<{ ok: boolean; path?: string; error?: string }> =>
    ipcRenderer.invoke('rex:uploadContactsFile'),
  pickFolder: (): Promise<{ ok: boolean; path?: string; error?: string }> =>
    ipcRenderer.invoke('rex:pickFolder'),
  testEmailAccount: (id: string): Promise<{ ok: boolean; state?: IntegrationConnectionStatus; error?: string }> =>
    ipcRenderer.invoke('rex:testEmailAccount', id),
  getPreferenceSuggestions: (): Promise<PreferenceSuggestion[]> =>
    ipcRenderer.invoke('rex:getPreferenceSuggestions'),
  applyPreferenceSuggestion: (field: string, value: string | number): Promise<{ ok: boolean }> =>
    ipcRenderer.invoke('rex:applyPreferenceSuggestion', field, value),
  getEmailInbox: (): Promise<EmailMessage[]> => ipcRenderer.invoke('rex:getEmailInbox'),
  generateEmailReply: (id: string): Promise<string> =>
    ipcRenderer.invoke('rex:generateEmailReply', id),
  findMeetingSlots: (params: FindMeetingSlotsParams): Promise<TimeSlot[]> =>
    ipcRenderer.invoke('rex:findMeetingSlots', params),
  getSMSThreads: (): Promise<SMSThread[]> => ipcRenderer.invoke('rex:getSMSThreads'),
  getSMSThread: (threadId: string): Promise<SMSThread> =>
    ipcRenderer.invoke('rex:getSMSThread', threadId),
  sendSMS: (to: string, body: string): Promise<SMSMessage> =>
    ipcRenderer.invoke('rex:sendSMS', to, body),
  getNotifications: (): Promise<GuiNotification[]> =>
    ipcRenderer.invoke('rex:getNotifications'),
  markNotificationRead: (id: string): Promise<void> =>
    ipcRenderer.invoke('rex:markNotificationRead', id).then(() => undefined),
  dismissNotification: (id: string): Promise<void> =>
    ipcRenderer.invoke('rex:dismissNotification', id).then(() => undefined),
  getUnreadNotificationCount: (): Promise<number> =>
    ipcRenderer.invoke('rex:getUnreadNotificationCount'),
  onNewNotification: (cb: (notification: GuiNotification) => void): void => {
    ipcRenderer.on('rex:newNotification', (_event, notification: GuiNotification) =>
      cb(notification)
    )
  },
  listVoices: (provider: string): Promise<{ ok: boolean; voices: VoiceInfo[]; error?: string }> =>
    ipcRenderer.invoke('rex:listVoices', provider),
  previewVoice: (
    provider: string,
    voiceId: string
  ): Promise<{ ok: boolean; audio_base64?: string; error?: string }> =>
    ipcRenderer.invoke('rex:previewVoice', provider, voiceId),
  synthesizeSpeech: (
    provider: string,
    voiceId: string,
    text: string
  ): Promise<{ ok: boolean; audio_base64?: string; error?: string }> =>
    ipcRenderer.invoke('rex:synthesizeSpeech', provider, voiceId, text),
  logVoiceTiming: (turnId: string, stage: string, durationMs: number): Promise<{ ok: boolean }> =>
    ipcRenderer.invoke('rex:logVoiceTiming', turnId, stage, durationMs),
  getVoiceEnrollments: (): Promise<{
    ok: boolean
    active_user_id: string
    enrollments: VoiceEnrollment[]
    error?: string
  }> => ipcRenderer.invoke('rex:getVoiceEnrollments'),
  enrollVoice: (
    userId: string,
    samples: number[][]
  ): Promise<{ ok: boolean; enrollment?: VoiceEnrollment; error?: string }> =>
    ipcRenderer.invoke('rex:enrollVoice', userId, samples),
  deleteVoiceEnrollment: (
    userId: string
  ): Promise<{ ok: boolean; deleted?: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:deleteVoiceEnrollment', userId),
  sendChatAudio: (
    audioBase64: string
  ): Promise<{ ok: boolean; transcript?: string; error?: string }> =>
    ipcRenderer.invoke('rex:sendChatAudio', audioBase64),
  getApiKeys: (): Promise<{ openai_key_set: boolean; openrouter_key_set: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:getApiKeys'),
  setApiKey: (name: string, value: string): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:setApiKey', name, value),
  getAudioTargets: (): Promise<AudioTargetsResponse> =>
    ipcRenderer.invoke('rex:getAudioTargets'),
  refreshAudioTargets: (): Promise<AudioTargetsResponse> =>
    ipcRenderer.invoke('rex:refreshAudioTargets'),
  listSpeakerGroups: (): Promise<SpeakerGroupsResponse> =>
    ipcRenderer.invoke('rex:listSpeakerGroups'),
  createSpeakerGroup: (name: string, memberIds: string[]): Promise<SpeakerGroupMutationResponse> =>
    ipcRenderer.invoke('rex:createSpeakerGroup', name, memberIds),
  renameSpeakerGroup: (groupId: string, name: string): Promise<SpeakerGroupMutationResponse> =>
    ipcRenderer.invoke('rex:renameSpeakerGroup', groupId, name),
  setSpeakerGroupMembers: (groupId: string, memberIds: string[]): Promise<SpeakerGroupMutationResponse> =>
    ipcRenderer.invoke('rex:setSpeakerGroupMembers', groupId, memberIds),
  deleteSpeakerGroup: (groupId: string): Promise<{ ok: boolean; deleted?: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:deleteSpeakerGroup', groupId),
  restartRex: (): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:restartRex'),
  resetToDefaults: (): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:resetToDefaults'),
  extractFileForChat: (params: {
    filename: string
    dataBase64: string
    mimeType: string
    sizeBytes: number
  }): Promise<FileExtractResult> => ipcRenderer.invoke('rex:extractFileForChat', params),
  getShoppingItems: (): Promise<{ ok: boolean; items: ShoppingItem[]; error?: string }> =>
    ipcRenderer.invoke('rex:getShoppingItems'),
  addShoppingItem: (name: string, quantity: number, unit: string): Promise<{ ok: boolean; item?: ShoppingItem; error?: string }> =>
    ipcRenderer.invoke('rex:addShoppingItem', name, quantity, unit),
  checkShoppingItem: (id: string): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:checkShoppingItem', id),
  uncheckShoppingItem: (id: string): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:uncheckShoppingItem', id),
  clearCheckedShoppingItems: (): Promise<{ ok: boolean; count?: number; error?: string }> =>
    ipcRenderer.invoke('rex:clearCheckedShoppingItems'),
  listWakeWords: (): Promise<{ ok: boolean; wake_words: WakeWordInfo[]; error?: string; warning?: string }> =>
    ipcRenderer.invoke('rex:listWakeWords'),
  getSetupWakeWordStatus: (wakeWordId: string): Promise<WakeWordStatus> =>
    ipcRenderer.invoke('rex:getSetupWakeWordStatus', wakeWordId),
  getWakeWordStatus: (settings?: VoiceSettings): Promise<WakeWordStatus> =>
    ipcRenderer.invoke('rex:getWakeWordStatus', settings),
  previewWakeWordSample: (
    wakeWordId: string
  ): Promise<{ ok: boolean; audio_base64?: string; has_sample?: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:previewWakeWordSample', wakeWordId),
  uploadCustomVoice: (
    filePath: string,
    voiceName: string
  ): Promise<{ ok: boolean; voice_id?: string; voice_name?: string; duration?: number; error?: string }> =>
    ipcRenderer.invoke('rex:uploadCustomVoice', filePath, voiceName),
  trainWakeWord: (
    phrase: string,
    positiveSamples: number[][],
    negativeSamples: number[][]
  ): Promise<{ ok: boolean; model_path?: string; phrase?: string; error?: string }> =>
    ipcRenderer.invoke('rex:trainWakeWord', phrase, positiveSamples, negativeSamples),
  getLogs: (limit?: number): Promise<LogsResponse> =>
    ipcRenderer.invoke('rex:getLogs', limit),
  startLogTail: (): Promise<{ ok: boolean; log_path?: string; error?: string }> =>
    ipcRenderer.invoke('rex:startLogTail'),
  stopLogTail: (): Promise<{ ok: boolean }> =>
    ipcRenderer.invoke('rex:stopLogTail'),
  downloadLogs: (): Promise<{ ok: boolean; content?: string; filename?: string; log_path?: string; error?: string }> =>
    ipcRenderer.invoke('rex:downloadLogs'),
  onLogEntry: (cb: (entry: LogEntry) => void): void => {
    ipcRenderer.on('rex:logEntry', (_event, entry: LogEntry) => cb(entry))
  },
  getUsage: (): Promise<UsageSummary> => ipcRenderer.invoke('rex:getUsage'),
  listQuickActions: (): Promise<{ ok: boolean; quick_actions: QuickAction[]; error?: string }> =>
    ipcRenderer.invoke('rex:listQuickActions'),
  createQuickAction: (label: string, commandText: string): Promise<{ ok: boolean; action?: QuickAction; error?: string }> =>
    ipcRenderer.invoke('rex:createQuickAction', label, commandText),
  deleteQuickAction: (id: string): Promise<{ ok: boolean; deleted?: boolean; error?: string }> =>
    ipcRenderer.invoke('rex:deleteQuickAction', id),
  runQuickAction: (id: string): Promise<QuickActionRunResponse> =>
    ipcRenderer.invoke('rex:runQuickAction', id),
  getDevices: (): Promise<DevicesResponse> => ipcRenderer.invoke('rex:getDevices'),
  sendDeviceCommand: (
    entityId: string,
    command: string,
    payload?: { value?: number },
    confirmationToken?: string,
    requestId?: string
  ): Promise<DeviceCommandResponse> =>
    ipcRenderer.invoke('rex:sendDeviceCommand', entityId, command, payload, confirmationToken, requestId),
  getSetupStatus: (): Promise<SetupStatusResponse> => ipcRenderer.invoke('rex:getSetupStatus'),
  getSetupAudioDevices: (): Promise<SetupAudioDevicesResponse> =>
    ipcRenderer.invoke('rex:getSetupAudioDevices'),
  testSetupAudioDevice: (
    kind: 'microphone' | 'speaker',
    deviceIndex: number
  ): Promise<SetupAudioTestResponse> =>
    ipcRenderer.invoke('rex:testSetupAudioDevice', kind, deviceIndex),
  completeSetup: (payload: SetupCompletePayload): Promise<SetupCompleteResponse> =>
    ipcRenderer.invoke('rex:completeSetup', payload),
  createPairingChallenge: (scopes: string[]): Promise<PairingResponse> =>
    ipcRenderer.invoke('rex:createPairingChallenge', scopes),
  listPendingPairings: (): Promise<PairingResponse> =>
    ipcRenderer.invoke('rex:listPendingPairings'),
  approvePairing: (requestId: string): Promise<PairingResponse> =>
    ipcRenderer.invoke('rex:approvePairing', requestId),
  denyPairing: (requestId: string): Promise<PairingResponse> =>
    ipcRenderer.invoke('rex:denyPairing', requestId),
  listPairedDevices: (): Promise<PairingResponse> =>
    ipcRenderer.invoke('rex:listPairedDevices'),
  revokePairedDevice: (deviceId: string): Promise<PairingResponse> =>
    ipcRenderer.invoke('rex:revokePairedDevice', deviceId),
  getProfile: (): Promise<ProfileOperationResponse> =>
    ipcRenderer.invoke('rex:getProfile'),
  updateProfilePreferences: (preferences: Record<string, unknown>): Promise<ProfileOperationResponse> =>
    ipcRenderer.invoke('rex:updateProfilePreferences', preferences),
  setProfileAvatar: (mimeType: string, avatarBase64: string): Promise<ProfileOperationResponse> =>
    ipcRenderer.invoke('rex:setProfileAvatar', mimeType, avatarBase64),
  removeProfileAvatar: (): Promise<ProfileOperationResponse> =>
    ipcRenderer.invoke('rex:removeProfileAvatar')
}

if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('rex', rexAPI)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-expect-error -- fallback assignment when context isolation is disabled
  window.electron = electronAPI
  // @ts-expect-error -- fallback assignment when context isolation is disabled
  window.rex = rexAPI
}

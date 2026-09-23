import type { SetupCompletePayload, TurnStatusUpdate, VoiceTranscriptEntry } from '../types/ipc'

export interface SetupFormData {
  username: string
  password: string
  llmProvider: string
  llmApiKey: string
  openaiBaseUrl?: string
  ttsProvider: string
  ttsVoiceId?: string
  microphoneDeviceIndex?: number | null
  speakerDeviceIndex?: number | null
  localDeviceId?: string
  wakeWordId?: string
  roomName?: string
  backgroundVoiceEnabled?: boolean
  haBaseUrl: string
  haToken: string
}

export interface SetupSubmissionOptions {
  deferHomeAssistant?: boolean
}

export type VoiceVerificationStage = 'wake' | 'capture' | 'stt' | 'turn' | 'tts' | 'playback'
export type VoiceVerificationStatus = 'idle' | 'running' | 'verified' | 'failed' | 'cancelled'

export interface VoiceVerificationState {
  status: VoiceVerificationStatus
  currentStage: VoiceVerificationStage | null
  passedStages: VoiceVerificationStage[]
  error?: string
}

export type VoiceVerificationEvent =
  | { type: 'started' }
  | { type: 'voice_state'; state: string }
  | { type: 'voice_status'; status: string }
  | { type: 'transcript'; entry: VoiceTranscriptEntry }
  | { type: 'turn_status'; update: TurnStatusUpdate }
  | { type: 'voice_error'; error: string }
  | { type: 'cancelled' }
  | { type: 'playback_confirmed' }
  | { type: 'playback_rejected' }

export function createVoiceVerificationState(): VoiceVerificationState {
  return {
    status: 'idle',
    currentStage: null,
    passedStages: []
  }
}

function passStage(
  state: VoiceVerificationState,
  stage: VoiceVerificationStage,
  nextStage: VoiceVerificationStage | null,
  status: VoiceVerificationStatus = 'running'
): VoiceVerificationState {
  if (state.currentStage !== stage) return state
  return {
    status,
    currentStage: nextStage,
    passedStages: [...state.passedStages, stage]
  }
}

function failVerification(state: VoiceVerificationState, error: string): VoiceVerificationState {
  return {
    ...state,
    status: 'failed',
    error
  }
}

function reduceVoiceStateEvent(
  state: VoiceVerificationState,
  event: Extract<VoiceVerificationEvent, { type: 'voice_state' }>
): VoiceVerificationState {
  if (event.state === 'listening') return passStage(state, 'wake', 'capture')
  if (event.state === 'processing') return passStage(state, 'capture', 'stt')
  return state
}

function reduceTranscriptEvent(
  state: VoiceVerificationState,
  event: Extract<VoiceVerificationEvent, { type: 'transcript' }>
): VoiceVerificationState {
  if (event.entry.role !== 'user') return state
  return passStage(state, 'stt', 'turn')
}

function reduceTurnStatusEvent(
  state: VoiceVerificationState,
  event: Extract<VoiceVerificationEvent, { type: 'turn_status' }>
): VoiceVerificationState {
  if (!event.update.terminal || state.currentStage !== 'turn') return state
  if (event.update.status === 'done') return passStage(state, 'turn', 'tts')
  if (event.update.status === 'error') return failVerification(state, 'Rex turn failed.')
  if (event.update.status === 'cancelled') return failVerification(state, 'Rex turn was cancelled.')
  return state
}

function reducePlaybackEvent(
  state: VoiceVerificationState,
  event: Extract<
    VoiceVerificationEvent,
    { type: 'voice_status' | 'playback_confirmed' | 'playback_rejected' }
  >
): VoiceVerificationState {
  if (event.type === 'voice_status') {
    if (event.status !== 'voice_playback_complete') return state
    return passStage(state, 'tts', 'playback')
  }
  if (event.type === 'playback_confirmed') {
    return passStage(state, 'playback', null, 'verified')
  }
  if (state.currentStage !== 'playback') return state
  return failVerification(state, 'Audible playback was not confirmed.')
}

function reduceRunningVoiceVerification(
  state: VoiceVerificationState,
  event: Exclude<VoiceVerificationEvent, { type: 'started' }>
): VoiceVerificationState {
  if (event.type === 'voice_error') return failVerification(state, event.error)
  if (event.type === 'cancelled') return { ...state, status: 'cancelled' }
  if (event.type === 'voice_state') return reduceVoiceStateEvent(state, event)
  if (event.type === 'transcript') return reduceTranscriptEvent(state, event)
  if (event.type === 'turn_status') return reduceTurnStatusEvent(state, event)
  return reducePlaybackEvent(state, event)
}

export function reduceVoiceVerification(
  state: VoiceVerificationState,
  event: VoiceVerificationEvent
): VoiceVerificationState {
  if (event.type === 'started') {
    return {
      status: 'running',
      currentStage: 'wake',
      passedStages: []
    }
  }
  if (state.status !== 'running') return state
  return reduceRunningVoiceVerification(state, event)
}

export function buildSetupSubmission(
  data: SetupFormData,
  options?: SetupSubmissionOptions
): SetupCompletePayload {
  const deferHA = options?.deferHomeAssistant ?? false

  const submission: SetupCompletePayload = {
    username: data.username,
    password: data.password,
    llm_provider: data.llmProvider,
    tts_provider: data.ttsProvider,
    tts_voice_id: data.ttsVoiceId ?? '',
    microphone_device_index: data.microphoneDeviceIndex ?? null,
    speaker_device_index: data.speakerDeviceIndex ?? null,
    local_device_id: data.localDeviceId ?? 'local_voice',
    wake_word_id: data.wakeWordId ?? 'hey_rex',
    room_name: data.roomName ?? '',
    background_voice_enabled: data.backgroundVoiceEnabled ?? false,
    ha_base_url: deferHA ? '' : data.haBaseUrl,
    ha_token: deferHA ? '' : data.haToken
  }

  if (data.llmApiKey) {
    submission.llm_api_key = data.llmApiKey
  }

  if (data.llmProvider === 'lmstudio' && data.openaiBaseUrl?.trim()) {
    submission.openai_base_url = data.openaiBaseUrl.trim()
  }

  if (deferHA || options?.deferHomeAssistant === false) {
    submission.defer_home_assistant = deferHA
  }

  return submission
}

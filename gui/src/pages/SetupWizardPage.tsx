import React, { useCallback, useEffect, useRef, useState } from 'react'
import type { SetupAudioDevice, VoiceInfo, WakeWordInfo, WakeWordStatus } from '../types/ipc'
import {
  buildSetupSubmission,
  createVoiceVerificationState,
  reduceVoiceVerification,
  type VoiceVerificationEvent,
  type VoiceVerificationStage,
  type VoiceVerificationState
} from './setupWizardModel'
import { ModelDiscoveryResults, type ModelDiscoveryStatus } from './settings/ModelDiscoveryResults'
import { ModelDiscoveryRequestGate } from '../types/modelDiscoveryRequestGate'

interface SetupData {
  username: string
  password: string
  llmProvider: string
  llmApiKey: string
  openaiBaseUrl: string
  openaiModel: string
  ttsProvider: string
  ttsVoiceId: string
  microphoneDeviceIndex: number | null
  speakerDeviceIndex: number | null
  localDeviceId: string
  wakeWordId: string
  roomName: string
  backgroundVoiceEnabled: boolean
  haBaseUrl: string
  haToken: string
}

type SetupValue = string | boolean | number | null

interface StepProps {
  data: SetupData
  onChange: (field: keyof SetupData, value: SetupValue) => void
}

interface LlmStepProps extends StepProps {
  modelDiscoveryStatus: ModelDiscoveryStatus
  discoveredModels: string[]
  modelDiscoveryError: string
  onDiscoverModels: () => void
}

interface AudioStepProps {
  data: SetupData
  devices: SetupAudioDevice[]
  loading: boolean
  inventoryError: string
  testing: boolean
  testPassed: boolean
  testError: string
  onTest: () => void
  onDeviceChange: (deviceIndex: number | null) => void
}

interface VoiceStepProps extends StepProps {
  voices: VoiceInfo[]
  loading: boolean
  inventoryError: string
  previewing: boolean
  previewPlayed: boolean
  previewError: string
  onPreview: () => void
  onProviderChange: (provider: string) => void
  onVoiceChange: (voiceId: string) => void
}

interface WakeWordStepProps extends StepProps {
  wakeWords: WakeWordInfo[]
  loading: boolean
  inventoryError: string
  previewing: boolean
  samplePlayed: boolean
  previewError: string
  status: WakeWordStatus | null
  statusLoading: boolean
  statusError: string
  onPreview: () => void
  onWakeWordChange: (wakeWordId: string) => void
}

interface VerifyVoiceStepProps {
  verification: VoiceVerificationState
  wakeWordName: string
  onStart: () => void
  onCancel: () => void
  onPlaybackConfirmed: () => void
  onPlaybackRejected: () => void
  onContinueWithoutVoice: () => void
  onContinue: () => void
}

function voiceApiProvider(ttsProvider: string): string {
  if (ttsProvider === 'edge') return 'edge-tts'
  if (ttsProvider === 'xtts') return 'xtts'
  return 'pyttsx3'
}

const inputClass =
  'w-full px-3 py-2 rounded-lg border border-border bg-surface text-text-primary text-sm focus:outline-none focus:ring-2 focus:ring-accent/50'

function StepAccount({ data, onChange }: StepProps): React.ReactElement {
  return (
    <div className="space-y-4">
      <p className="text-text-secondary text-sm">
        Create the primary Rex profile. It is stored locally and owns this first-run setup.
      </p>
      <div>
        <label className="block text-sm font-medium text-text-primary mb-1">Username</label>
        <input
          type="text"
          autoComplete="username"
          value={data.username}
          onChange={(event) => onChange('username', event.target.value)}
          className={inputClass}
          placeholder="e.g. james"
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-text-primary mb-1">Password</label>
        <input
          type="password"
          autoComplete="new-password"
          value={data.password}
          onChange={(event) => onChange('password', event.target.value)}
          className={inputClass}
          placeholder="Choose a strong password"
        />
      </div>
    </div>
  )
}

function StepLLM({
  data,
  onChange,
  modelDiscoveryStatus,
  discoveredModels,
  modelDiscoveryError,
  onDiscoverModels
}: LlmStepProps): React.ReactElement {
  const needsKey = ['openai', 'openrouter', 'anthropic'].includes(data.llmProvider)
  const isLmStudio = data.llmProvider === 'lmstudio'
  return (
    <div className="space-y-4">
      <p className="text-text-secondary text-sm">
        Choose the supported AI provider Rex should use. You can change this later in Settings.
      </p>
      <div>
        <label className="block text-sm font-medium text-text-primary mb-1">AI Provider</label>
        <select
          value={data.llmProvider}
          onChange={(event) => onChange('llmProvider', event.target.value)}
          className={inputClass}
        >
          <option value="local">Local (Transformers / Ollama)</option>
          <option value="openai">OpenAI</option>
          <option value="openrouter">OpenRouter</option>
          <option value="anthropic">Anthropic</option>
          <option value="lmstudio">LM Studio (OpenAI-compatible)</option>
          <option value="ollama">Ollama (custom URL)</option>
        </select>
      </div>
      {isLmStudio && (
        <div>
          <label className="block text-sm font-medium text-text-primary mb-1">
            LM Studio Base URL
          </label>
          <input
            type="text"
            value={data.openaiBaseUrl}
            onChange={(event) => onChange('openaiBaseUrl', event.target.value)}
            className={inputClass}
            placeholder="http://127.0.0.1:1234/v1"
          />
          <p className="text-text-muted text-xs mt-1">
            Rex talks to LM Studio through its OpenAI-compatible local server. Start LM Studio's
            local server and enter its address here, e.g. http://127.0.0.1:1234/v1.
          </p>
        </div>
      )}
      {isLmStudio && (
        <div>
          <label className="block text-sm font-medium text-text-primary mb-1">
            LM Studio Model
          </label>
          <input
            type="text"
            value={data.openaiModel}
            onChange={(event) => onChange('openaiModel', event.target.value)}
            className={inputClass}
            placeholder="e.g. openai/gpt-oss-20b"
          />
          <p className="text-text-muted text-xs mt-1">
            Rex needs the exact model ID LM Studio is currently serving. Discover it from the
            base URL above, or type it exactly as shown in LM Studio's server tab.
          </p>
          <button
            type="button"
            onClick={onDiscoverModels}
            disabled={!data.openaiBaseUrl.trim() || modelDiscoveryStatus === 'loading'}
            className="mt-2 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm font-medium text-text-primary transition-colors hover:border-accent/60 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Discover LM Studio Models
          </button>
          <ModelDiscoveryResults
            providerLabel="LM Studio"
            status={modelDiscoveryStatus}
            models={discoveredModels}
            selectedModel={data.openaiModel}
            error={modelDiscoveryError}
            onSelect={(model) => onChange('openaiModel', model)}
          />
        </div>
      )}
      {needsKey && (
        <div>
          <label className="block text-sm font-medium text-text-primary mb-1">API Key</label>
          <input
            type="password"
            autoComplete="off"
            value={data.llmApiKey}
            onChange={(event) => onChange('llmApiKey', event.target.value)}
            className={inputClass}
            placeholder={data.llmProvider === 'anthropic' ? 'sk-ant-...' : 'Enter API key'}
          />
          <p className="text-text-muted text-xs mt-1">Stored in the Windows credential vault.</p>
        </div>
      )}
    </div>
  )
}

function StepVoice({
  data,
  voices,
  loading,
  inventoryError,
  previewing,
  previewPlayed,
  previewError,
  onPreview,
  onProviderChange,
  onVoiceChange
}: VoiceStepProps): React.ReactElement {
  return (
    <div className="space-y-4">
      <p className="text-text-secondary text-sm">
        Choose Rex voice and a speaking provider. Preview confirms this voice can synthesize and
        play here, but it does not verify the complete wake-to-response path.
      </p>
      <div>
        <label className="block text-sm font-medium text-text-primary mb-1">TTS Provider</label>
        <select
          value={data.ttsProvider}
          onChange={(event) => onProviderChange(event.target.value)}
          className={inputClass}
        >
          <option value="edge">Edge TTS</option>
          <option value="pyttsx3">pyttsx3 (offline system voice)</option>
          <option value="xtts">Coqui XTTS</option>
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium text-text-primary mb-1">Select a Rex voice</label>
        <select
          value={data.ttsVoiceId}
          onChange={(event) => onVoiceChange(event.target.value)}
          className={inputClass}
          disabled={loading || voices.length === 0}
        >
          <option value="">Choose a Rex voice</option>
          {voices.map((voice) => (
            <option key={voice.id} value={voice.id}>
              {voice.name}
            </option>
          ))}
        </select>
      </div>
      {inventoryError && <p className="text-red-400 text-sm">{inventoryError}</p>}
      <button
        type="button"
        onClick={onPreview}
        disabled={previewing || loading || !data.ttsVoiceId}
        className="px-3 py-2 rounded-lg bg-surface-raised text-text-primary text-sm disabled:opacity-50"
      >
        {previewing ? 'Playing preview…' : 'Preview Rex voice'}
      </button>
      {previewPlayed && <p className="text-green-400 text-sm">Voice preview played</p>}
      {previewError && <p className="text-red-400 text-sm">{previewError}</p>}
      <p className="text-text-muted text-xs">Voice not yet verified by this preview.</p>
    </div>
  )
}

function StepMicrophone({
  data,
  devices,
  loading,
  inventoryError,
  testing,
  testPassed,
  testError,
  onTest,
  onDeviceChange
}: AudioStepProps): React.ReactElement {
  return (
    <div className="space-y-4">
      <p className="text-text-secondary text-sm">
        Test microphone access and choose the local input Rex should use for wake-word capture.
      </p>
      <div>
        <label className="block text-sm font-medium text-text-primary mb-1">Microphone</label>
        <select
          value={data.microphoneDeviceIndex ?? ''}
          onChange={(event) =>
            onDeviceChange(event.target.value === '' ? null : Number(event.target.value))
          }
          className={inputClass}
          disabled={loading}
        >
          <option value="">Choose a microphone</option>
          {devices.map((device) => (
            <option key={device.index} value={device.index}>
              {device.name}
            </option>
          ))}
        </select>
      </div>
      {inventoryError && <p className="text-red-400 text-sm">{inventoryError}</p>}
      <button
        type="button"
        onClick={onTest}
        disabled={testing || data.microphoneDeviceIndex === null}
        className="px-3 py-2 rounded-lg bg-surface-raised text-text-primary text-sm disabled:opacity-50"
      >
        {testing ? 'Testing microphone…' : 'Test microphone'}
      </button>
      {testPassed && <p className="text-green-400 text-sm">Microphone test passed</p>}
      {testError && <p className="text-red-400 text-sm">{testError}</p>}
      <p className="text-text-muted text-xs">
        This functional test does not save the device or mark the complete voice path verified.
      </p>
    </div>
  )
}

function StepSpeaker({
  data,
  devices,
  loading,
  inventoryError,
  testing,
  testPassed,
  testError,
  onTest,
  onDeviceChange
}: AudioStepProps): React.ReactElement {
  return (
    <div className="space-y-4">
      <p className="text-text-secondary text-sm">
        Test speaker output and choose where this local Rex installation should answer.
      </p>
      <div>
        <label className="block text-sm font-medium text-text-primary mb-1">Speaker / audio target</label>
        <select
          value={data.speakerDeviceIndex ?? ''}
          onChange={(event) =>
            onDeviceChange(event.target.value === '' ? null : Number(event.target.value))
          }
          className={inputClass}
          disabled={loading}
        >
          <option value="">Choose a speaker</option>
          {devices.map((device) => (
            <option key={device.index} value={device.index}>
              {device.name}
            </option>
          ))}
        </select>
      </div>
      {inventoryError && <p className="text-red-400 text-sm">{inventoryError}</p>}
      <button
        type="button"
        onClick={onTest}
        disabled={testing || data.speakerDeviceIndex === null}
        className="px-3 py-2 rounded-lg bg-surface-raised text-text-primary text-sm disabled:opacity-50"
      >
        {testing ? 'Testing speaker…' : 'Test speaker'}
      </button>
      {testPassed && <p className="text-green-400 text-sm">Speaker test passed</p>}
      {testError && <p className="text-red-400 text-sm">{testError}</p>}
      <p className="text-text-muted text-xs">
        Saving a target or passing this device probe does not prove audible end-to-end playback.
      </p>
    </div>
  )
}

function StepWakeWord({
  data,
  onChange,
  wakeWords,
  loading,
  inventoryError,
  previewing,
  samplePlayed,
  previewError,
  status,
  statusLoading,
  statusError,
  onPreview,
  onWakeWordChange
}: WakeWordStepProps): React.ReactElement {
  return (
    <div className="space-y-4">
      <p className="text-text-secondary text-sm">
        Choose the wake word Rex listens for. A sample and asset check help confirm setup, but
        actual wake detection and calibration must still succeed before voice verification.
      </p>
      <div>
        <label className="block text-sm font-medium text-text-primary mb-1">Choose a wake word</label>
        <select
          value={data.wakeWordId}
          onChange={(event) => {
            onWakeWordChange(event.target.value)
            onChange('wakeWordId', event.target.value)
          }}
          className={inputClass}
          disabled={loading || wakeWords.length === 0}
        >
          <option value="">Choose a wake word</option>
          {wakeWords.map((wakeWord) => (
            <option key={wakeWord.id} value={wakeWord.id}>
              {wakeWord.name}
            </option>
          ))}
        </select>
      </div>
      {inventoryError && <p className="text-red-400 text-sm">{inventoryError}</p>}
      <button
        type="button"
        onClick={onPreview}
        disabled={previewing || loading || !data.wakeWordId}
        className="px-3 py-2 rounded-lg bg-surface-raised text-text-primary text-sm disabled:opacity-50"
      >
        {previewing ? 'Playing wake-word sample…' : 'Preview wake-word sample'}
      </button>
      {samplePlayed && <p className="text-green-400 text-sm">Wake-word sample played</p>}
      {previewError && <p className="text-red-400 text-sm">{previewError}</p>}
      <p className="text-text-muted text-xs">
        Actual wake detection is still required before voice setup can be verified.
      </p>
      {statusLoading && <p className="text-text-muted text-xs">Checking wake-word asset…</p>}
      {statusError && <p className="text-red-400 text-sm">{statusError}</p>}
      {status && status.status !== 'missing_asset' && (
        <p className="text-green-400 text-sm">Wake-word asset ready</p>
      )}
      {status?.status === 'missing_asset' && (
        <p className="text-amber-300 text-sm">Wake-word asset needs attention: {status.detail}</p>
      )}
      <p className="text-text-muted text-xs">Voice not yet verified by wake-word setup alone.</p>
    </div>
  )
}

function StepRoom({ data, onChange }: StepProps): React.ReactElement {
  return (
    <div className="space-y-4">
      <p className="text-text-secondary text-sm">
        Assign this microphone and speaker to a Room so Rex can keep request origin and response
        routing unambiguous.
      </p>
      <div>
        <label className="block text-sm font-medium text-text-primary mb-1">Room</label>
        <input
          value={data.roomName}
          onChange={(event) => onChange('roomName', event.target.value)}
          className={inputClass}
          placeholder="Office"
        />
      </div>
    </div>
  )
}

function StepBackgroundVoice({ data, onChange }: StepProps): React.ReactElement {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-text-primary">Background voice</h3>
      <p className="text-text-secondary text-sm">
        If enabled, Rex continues listening when the AskRex window is closed so the wake word can
        summon it. You can pause or disable listening later from Rex controls.
      </p>
      <label className="flex items-start gap-3 text-sm text-text-primary">
        <input
          type="checkbox"
          checked={data.backgroundVoiceEnabled}
          onChange={(event) => onChange('backgroundVoiceEnabled', event.target.checked)}
          className="mt-1"
        />
        <span>Enable background voice</span>
      </label>
      <p className="text-text-muted text-xs">
        Background listening remains off unless you explicitly enable it here.
      </p>
    </div>
  )
}

function StepHomeAssistant({ data, onChange }: StepProps): React.ReactElement {
  return (
    <div className="space-y-4">
      <p className="text-text-secondary text-sm">
        Home Assistant is optional. Connect it now for smart-home control, or do this later.
      </p>
      <div>
        <label className="block text-sm font-medium text-text-primary mb-1">Home Assistant URL</label>
        <input
          type="url"
          value={data.haBaseUrl}
          onChange={(event) => onChange('haBaseUrl', event.target.value)}
          className={inputClass}
          placeholder="http://homeassistant.local:8123"
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-text-primary mb-1">
          Long-Lived Access Token
        </label>
        <input
          type="password"
          autoComplete="off"
          value={data.haToken}
          onChange={(event) => onChange('haToken', event.target.value)}
          className={inputClass}
          placeholder="eyJ..."
        />
      </div>
    </div>
  )
}

const VOICE_VERIFICATION_STAGES: Array<{ id: VoiceVerificationStage; label: string }> = [
  { id: 'wake', label: 'Wake word detection' },
  { id: 'capture', label: 'Microphone capture' },
  { id: 'stt', label: 'Speech recognition' },
  { id: 'turn', label: 'Canonical Rex turn' },
  { id: 'tts', label: 'Speech synthesis' },
  { id: 'playback', label: 'Audible playback' }
]

function StepVerifyVoice({
  verification,
  wakeWordName,
  onStart,
  onCancel,
  onPlaybackConfirmed,
  onPlaybackRejected,
  onContinueWithoutVoice,
  onContinue
}: VerifyVoiceStepProps): React.ReactElement {
  const passedStages = new Set(verification.passedStages)
  const canStart =
    verification.status === 'idle' ||
    verification.status === 'failed' ||
    verification.status === 'cancelled'
  const awaitingPlaybackConfirmation =
    verification.status === 'running' && verification.currentStage === 'playback'

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold text-text-primary">Voice setup saved</h3>
      {verification.status === 'verified' ? (
        <p className="text-green-400 text-sm font-medium">Voice verified</p>
      ) : verification.status === 'failed' ? (
        <p className="text-red-400 text-sm font-medium">
          Voice verification failed{verification.error ? `: ${verification.error}` : '.'}
        </p>
      ) : verification.status === 'cancelled' ? (
        <p className="text-amber-300 text-sm font-medium">Voice verification cancelled</p>
      ) : verification.status === 'running' ? (
        <p className="text-accent text-sm font-medium">Voice verification in progress</p>
      ) : (
        <p className="text-amber-300 text-sm font-medium">Voice not yet verified</p>
      )}
      <p className="text-text-secondary text-sm">
        Verification uses the normal wake-word voice path. Say “{wakeWordName || 'your wake word'}”
        and then ask Rex to say a short reply. Saving settings alone never marks this path verified.
      </p>

      <div className="space-y-2">
        {VOICE_VERIFICATION_STAGES.map((stage) => {
          const passed = passedStages.has(stage.id)
          const current = verification.currentStage === stage.id
          const failed = current && verification.status === 'failed'
          const stateLabel = passed ? 'Passed' : failed ? 'Failed' : current ? 'In progress' : 'Waiting'
          return (
            <div
              key={stage.id}
              className="flex items-center justify-between rounded-lg bg-surface-raised px-3 py-2 text-sm"
            >
              <span className="text-text-primary">{stage.label}</span>
              <span
                className={
                  passed
                    ? 'text-green-400'
                    : failed
                      ? 'text-red-400'
                      : current
                        ? 'text-accent'
                        : 'text-text-muted'
                }
              >
                {stateLabel}
              </span>
            </div>
          )
        })}
      </div>

      {canStart && (
        <button
          type="button"
          onClick={onStart}
          className="w-full px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90"
        >
          {verification.status === 'idle' ? 'Start voice verification' : 'Retry voice verification'}
        </button>
      )}

      {awaitingPlaybackConfirmation && (
        <div className="space-y-3 rounded-lg border border-border p-3">
          <p className="text-sm font-medium text-text-primary">Did you hear Rex's reply?</p>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={onPlaybackConfirmed}
              className="px-3 py-2 rounded-lg bg-accent text-white text-sm"
            >
              Yes, I heard Rex
            </button>
            <button
              type="button"
              onClick={onPlaybackRejected}
              className="px-3 py-2 rounded-lg bg-surface-raised text-text-primary text-sm"
            >
              No, I didn't hear it
            </button>
          </div>
        </div>
      )}

      {verification.status === 'running' && (
        <button
          type="button"
          onClick={onCancel}
          className="w-full px-4 py-2 rounded-lg bg-surface-raised text-text-primary text-sm"
        >
          Cancel verification
        </button>
      )}

      {verification.status === 'verified' ? (
        <button
          type="button"
          onClick={onContinue}
          className="w-full px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90"
        >
          Continue
        </button>
      ) : (
        <button
          type="button"
          onClick={onContinueWithoutVoice}
          className="w-full text-sm text-text-secondary hover:text-text-primary"
        >
          Continue without voice
        </button>
      )}
    </div>
  )
}

function StepDone({
  voiceVerified,
  runtimeWarning
}: {
  voiceVerified: boolean
  runtimeWarning: string
}): React.ReactElement {
  return (
    <div className="space-y-4 text-center">
      <h3 className="text-lg font-semibold text-text-primary">Setup saved</h3>
      {runtimeWarning ? (
        <>
          <p className="text-amber-300 text-sm font-medium">{runtimeWarning}</p>
          <p className="text-text-secondary text-sm">Close and reopen AskRex to continue.</p>
        </>
      ) : (
        <p className="text-text-secondary text-sm">
          {voiceVerified
            ? 'Your account and configuration are saved, and this setup session completed the full screenless voice verification path.'
            : 'Your account and configuration are saved. Voice was not verified during setup; you can test it later from Voice.'}
        </p>
      )}
    </div>
  )
}

const STEPS = [
  'Account',
  'AI',
  'Rex Voice',
  'Microphone',
  'Speaker',
  'Wake word',
  'Room',
  'Background voice',
  'Home Assistant',
  'Verify voice',
  'Done'
]
const HOME_ASSISTANT_STEP = 8
const VERIFY_STEP = 9
const DONE_STEP = 10

interface SetupWizardPageProps {
  onComplete: () => void
}

export function SetupWizardPage({ onComplete }: SetupWizardPageProps): React.ReactElement {
  const [step, setStep] = useState(0)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [setupPersisted, setSetupPersisted] = useState(false)
  const [setupRuntimeWarning, setSetupRuntimeWarning] = useState('')
  const [audioDevices, setAudioDevices] = useState<SetupAudioDevice[]>([])
  const [audioDevicesLoading, setAudioDevicesLoading] = useState(true)
  const [audioDevicesError, setAudioDevicesError] = useState('')
  const [microphoneTesting, setMicrophoneTesting] = useState(false)
  const [microphoneTestPassed, setMicrophoneTestPassed] = useState(false)
  const [microphoneTestError, setMicrophoneTestError] = useState('')
  const [speakerTesting, setSpeakerTesting] = useState(false)
  const [speakerTestPassed, setSpeakerTestPassed] = useState(false)
  const [speakerTestError, setSpeakerTestError] = useState('')
  const [voices, setVoices] = useState<VoiceInfo[]>([])
  const [voicesLoading, setVoicesLoading] = useState(true)
  const [voiceInventoryError, setVoiceInventoryError] = useState('')
  const [voicePreviewing, setVoicePreviewing] = useState(false)
  const [voicePreviewPlayed, setVoicePreviewPlayed] = useState(false)
  const [voicePreviewError, setVoicePreviewError] = useState('')
  const [wakeWords, setWakeWords] = useState<WakeWordInfo[]>([])
  const [wakeWordsLoading, setWakeWordsLoading] = useState(true)
  const [wakeWordInventoryError, setWakeWordInventoryError] = useState('')
  const [wakeWordPreviewing, setWakeWordPreviewing] = useState(false)
  const [wakeWordSamplePlayed, setWakeWordSamplePlayed] = useState(false)
  const [wakeWordPreviewError, setWakeWordPreviewError] = useState('')
  const [wakeWordStatus, setWakeWordStatus] = useState<WakeWordStatus | null>(null)
  const [wakeWordStatusLoading, setWakeWordStatusLoading] = useState(true)
  const [wakeWordStatusError, setWakeWordStatusError] = useState('')
  const [voiceVerification, setVoiceVerification] = useState(createVoiceVerificationState())
  const verificationVoiceActiveRef = useRef(false)
  const turnStatusCleanupRef = useRef<(() => void) | null>(null)
  const [lmStudioModelDiscoveryStatus, setLmStudioModelDiscoveryStatus] =
    useState<ModelDiscoveryStatus>('idle')
  const [lmStudioDiscoveredModels, setLmStudioDiscoveredModels] = useState<string[]>([])
  const [lmStudioModelDiscoveryError, setLmStudioModelDiscoveryError] = useState('')
  const lmStudioDiscoveryRequestGateRef = useRef(new ModelDiscoveryRequestGate())
  const [data, setData] = useState<SetupData>({
    username: '',
    password: '',
    llmProvider: 'local',
    llmApiKey: '',
    openaiBaseUrl: 'http://127.0.0.1:1234/v1',
    openaiModel: '',
    ttsProvider: 'edge',
    ttsVoiceId: 'en-US-AriaNeural',
    microphoneDeviceIndex: null,
    speakerDeviceIndex: null,
    localDeviceId: 'local_voice',
    wakeWordId: 'hey_rex',
    roomName: '',
    backgroundVoiceEnabled: false,
    haBaseUrl: '',
    haToken: ''
  })

  const applyVoiceVerificationEvent = useCallback((event: VoiceVerificationEvent): void => {
    setVoiceVerification((previous) => reduceVoiceVerification(previous, event))
  }, [])

  const cleanupTurnStatus = useCallback((): void => {
    turnStatusCleanupRef.current?.()
    turnStatusCleanupRef.current = null
  }, [])

  const stopVerificationSession = useCallback(async (): Promise<void> => {
    cleanupTurnStatus()
    if (!verificationVoiceActiveRef.current) return
    verificationVoiceActiveRef.current = false
    try {
      await window.rex.stopVoice()
    } catch {
      // Verification cleanup is best-effort; the state machine already reports the real outcome.
    }
  }, [cleanupTurnStatus])

  useEffect(() => {
    return () => {
      void stopVerificationSession()
    }
  }, [stopVerificationSession])

  useEffect(() => {
    let cancelled = false

    const loadAudioDevices = async (): Promise<void> => {
      try {
        const result = await window.rex.getSetupAudioDevices()
        if (cancelled) return
        if (!result.ok) {
          setAudioDevicesError(result.error ?? 'Unable to enumerate audio devices.')
          setAudioDevices([])
          return
        }
        setAudioDevices(result.devices)
        setAudioDevicesError('')
      } catch {
        if (!cancelled) {
          setAudioDevicesError('Unable to enumerate audio devices.')
          setAudioDevices([])
        }
      } finally {
        if (!cancelled) setAudioDevicesLoading(false)
      }
    }

    void loadAudioDevices()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const provider = data.ttsProvider
    setVoicesLoading(true)
    setVoiceInventoryError('')
    setVoicePreviewPlayed(false)
    setVoicePreviewError('')

    window.rex.listVoices(voiceApiProvider(data.ttsProvider))
      .then((result) => {
        if (cancelled) return
        if (!result.ok) {
          setVoices([])
          setVoiceInventoryError(result.error ?? 'Unable to load voices for this provider.')
          return
        }
        const available = result.voices ?? []
        setVoices(available)
        setData((previous) => {
          if (previous.ttsProvider !== provider) return previous
          if (available.some((voice) => voice.id === previous.ttsVoiceId)) return previous
          return { ...previous, ttsVoiceId: available[0]?.id ?? '' }
        })
      })
      .catch(() => {
        if (!cancelled) {
          setVoices([])
          setVoiceInventoryError('Unable to load voices for this provider.')
        }
      })
      .finally(() => {
        if (!cancelled) setVoicesLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [data.ttsProvider])

  useEffect(() => {
    let cancelled = false
    setWakeWordsLoading(true)
    setWakeWordInventoryError('')

    window.rex.listWakeWords()
      .then((result) => {
        if (cancelled) return
        if (!result.ok) {
          setWakeWords([])
          setWakeWordInventoryError(result.error ?? 'Unable to load wake words.')
          return
        }
        const available = result.wake_words ?? []
        setWakeWords(available)
        setData((previous) => {
          if (available.some((wakeWord) => wakeWord.id === previous.wakeWordId)) return previous
          return { ...previous, wakeWordId: available[0]?.id ?? '' }
        })
      })
      .catch(() => {
        if (!cancelled) {
          setWakeWords([])
          setWakeWordInventoryError('Unable to load wake words.')
        }
      })
      .finally(() => {
        if (!cancelled) setWakeWordsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    if (wakeWordsLoading || !data.wakeWordId) {
      setWakeWordStatus(null)
      setWakeWordStatusError('')
      setWakeWordStatusLoading(wakeWordsLoading)
      return () => {
        cancelled = true
      }
    }

    setWakeWordStatusLoading(true)
    setWakeWordStatusError('')

    window.rex.getSetupWakeWordStatus(data.wakeWordId)
      .then((status) => {
        if (!cancelled) setWakeWordStatus(status)
      })
      .catch(() => {
        if (!cancelled) {
          setWakeWordStatus(null)
          setWakeWordStatusError('Unable to check wake-word asset readiness.')
        }
      })
      .finally(() => {
        if (!cancelled) setWakeWordStatusLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [data.wakeWordId, wakeWordsLoading])

  const microphoneDevices = audioDevices.filter((device) => device.max_input_channels > 0)
  const speakerDevices = audioDevices.filter((device) => device.max_output_channels > 0)

  const resetLmStudioModelDiscovery = (): void => {
    lmStudioDiscoveryRequestGateRef.current.invalidate()
    setLmStudioModelDiscoveryStatus('idle')
    setLmStudioDiscoveredModels([])
    setLmStudioModelDiscoveryError('')
  }

  const handleChange = (field: keyof SetupData, value: SetupValue): void => {
    if (field === 'openaiBaseUrl' || field === 'llmProvider') {
      resetLmStudioModelDiscovery()
    }
    setData((previous) => ({ ...previous, [field]: value }))
    setError('')
  }

  const handleDiscoverLmStudioModels = async (): Promise<void> => {
    const endpoint = data.openaiBaseUrl.trim()
    if (!endpoint) return
    const requestId = lmStudioDiscoveryRequestGateRef.current.begin()
    setLmStudioModelDiscoveryStatus('loading')
    setLmStudioDiscoveredModels([])
    setLmStudioModelDiscoveryError('')
    try {
      const result = await window.rex.discoverSetupAiModels('lmstudio', endpoint)
      if (!lmStudioDiscoveryRequestGateRef.current.isCurrent(requestId)) return
      if (result.ok) {
        setLmStudioDiscoveredModels(result.models)
        setLmStudioModelDiscoveryStatus('success')
        return
      }
      setLmStudioModelDiscoveryError(result.error ?? 'Model discovery failed')
      setLmStudioModelDiscoveryStatus('error')
    } catch {
      if (!lmStudioDiscoveryRequestGateRef.current.isCurrent(requestId)) return
      setLmStudioModelDiscoveryError('Model discovery could not be completed')
      setLmStudioModelDiscoveryStatus('error')
    }
  }

  const handleMicrophoneDeviceChange = (deviceIndex: number | null): void => {
    handleChange('microphoneDeviceIndex', deviceIndex)
    setMicrophoneTestPassed(false)
    setMicrophoneTestError('')
  }

  const handleSpeakerDeviceChange = (deviceIndex: number | null): void => {
    handleChange('speakerDeviceIndex', deviceIndex)
    setSpeakerTestPassed(false)
    setSpeakerTestError('')
  }

  const handleMicrophoneTest = async (): Promise<void> => {
    if (data.microphoneDeviceIndex === null) {
      setMicrophoneTestError('Choose a microphone before testing it.')
      return
    }
    setMicrophoneTesting(true)
    setMicrophoneTestPassed(false)
    setMicrophoneTestError('')
    try {
      const result = await window.rex.testSetupAudioDevice('microphone', data.microphoneDeviceIndex)
      if (!result.ok) {
        setMicrophoneTestError(result.error ?? 'Microphone test failed.')
        return
      }
      setMicrophoneTestPassed(true)
    } catch {
      setMicrophoneTestError('Microphone test failed.')
    } finally {
      setMicrophoneTesting(false)
    }
  }

  const handleSpeakerTest = async (): Promise<void> => {
    if (data.speakerDeviceIndex === null) {
      setSpeakerTestError('Choose a speaker before testing it.')
      return
    }
    setSpeakerTesting(true)
    setSpeakerTestPassed(false)
    setSpeakerTestError('')
    try {
      const result = await window.rex.testSetupAudioDevice('speaker', data.speakerDeviceIndex)
      if (!result.ok) {
        setSpeakerTestError(result.error ?? 'Speaker test failed.')
        return
      }
      setSpeakerTestPassed(true)
    } catch {
      setSpeakerTestError('Speaker test failed.')
    } finally {
      setSpeakerTesting(false)
    }
  }

  const handleVoiceProviderChange = (provider: string): void => {
    handleChange('ttsProvider', provider)
    setVoicePreviewPlayed(false)
    setVoicePreviewError('')
  }

  const handleVoiceChange = (voiceId: string): void => {
    handleChange('ttsVoiceId', voiceId)
    setVoicePreviewPlayed(false)
    setVoicePreviewError('')
  }

  const handleWakeWordChange = (): void => {
    setWakeWordSamplePlayed(false)
    setWakeWordPreviewError('')
  }

  const playAudioBase64 = async (audioBase64: string): Promise<void> => {
    const binary = atob(audioBase64)
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index)
    }
    const context = new AudioContext()
    try {
      const buffer = await context.decodeAudioData(bytes.buffer)
      const source = context.createBufferSource()
      source.buffer = buffer
      source.connect(context.destination)
      await new Promise<void>((resolve) => {
        source.onended = () => resolve()
        source.start(0)
      })
    } finally {
      await context.close()
    }
  }

  const handleVoicePreview = async (): Promise<void> => {
    if (!data.ttsVoiceId) {
      setVoicePreviewError('Choose a Rex voice before previewing it.')
      return
    }
    setVoicePreviewing(true)
    setVoicePreviewPlayed(false)
    setVoicePreviewError('')
    try {
      const result = await window.rex.previewVoice(voiceApiProvider(data.ttsProvider), data.ttsVoiceId)
      if (!result.ok || !result.audio_base64) {
        setVoicePreviewError(result.error ?? 'Voice preview failed.')
        return
      }
      await playAudioBase64(result.audio_base64)
      setVoicePreviewPlayed(true)
    } catch {
      setVoicePreviewError('Voice preview failed.')
    } finally {
      setVoicePreviewing(false)
    }
  }

  const handleWakeWordPreview = async (): Promise<void> => {
    if (!data.wakeWordId) {
      setWakeWordPreviewError('Choose a wake word before previewing it.')
      return
    }
    setWakeWordPreviewing(true)
    setWakeWordSamplePlayed(false)
    setWakeWordPreviewError('')
    try {
      const result = await window.rex.previewWakeWordSample(data.wakeWordId)
      if (!result.ok || !result.audio_base64) {
        setWakeWordPreviewError(result.error ?? 'No wake-word sample is available for this choice.')
        return
      }
      await playAudioBase64(result.audio_base64)
      setWakeWordSamplePlayed(true)
    } catch {
      setWakeWordPreviewError('Wake-word sample preview failed.')
    } finally {
      setWakeWordPreviewing(false)
    }
  }

  const handleStartVoiceVerification = async (): Promise<void> => {
    await stopVerificationSession()
    applyVoiceVerificationEvent({ type: 'started' })
    turnStatusCleanupRef.current = window.rex.onTurnStatus((update) => {
      applyVoiceVerificationEvent({ type: 'turn_status', update })
    })
    verificationVoiceActiveRef.current = true

    const microphoneLabel = microphoneDevices
      .find((device) => device.index === data.microphoneDeviceIndex)
      ?.name.trim()

    try {
      await window.rex.startVoice(
        (state) => applyVoiceVerificationEvent({ type: 'voice_state', state }),
        (entry) => applyVoiceVerificationEvent({ type: 'transcript', entry }),
        (voiceError) => {
          applyVoiceVerificationEvent({ type: 'voice_error', error: voiceError })
          void stopVerificationSession()
        },
        (status) => {
          applyVoiceVerificationEvent({ type: 'voice_status', status })
          if (status === 'voice_playback_complete') {
            void stopVerificationSession()
          }
        },
        microphoneLabel ? { microphoneLabel } : undefined
      )
    } catch (verificationError) {
      const message =
        verificationError instanceof Error ? verificationError.message : 'Voice verification failed to start.'
      applyVoiceVerificationEvent({ type: 'voice_error', error: message })
      await stopVerificationSession()
    }
  }

  const handleCancelVoiceVerification = (): void => {
    applyVoiceVerificationEvent({ type: 'cancelled' })
    void stopVerificationSession()
  }

  const handlePlaybackConfirmed = (): void => {
    applyVoiceVerificationEvent({ type: 'playback_confirmed' })
  }

  const handlePlaybackRejected = (): void => {
    applyVoiceVerificationEvent({ type: 'playback_rejected' })
  }

  const handleContinueWithoutVoice = (): void => {
    if (voiceVerification.status === 'running') {
      applyVoiceVerificationEvent({ type: 'cancelled' })
    }
    void stopVerificationSession()
    setStep(DONE_STEP)
  }

  const handleVerifiedContinue = (): void => {
    if (voiceVerification.status === 'verified') {
      setStep(DONE_STEP)
    }
  }

  const handleBack = (): void => {
    if (setupPersisted) return
    if (step === VERIFY_STEP && voiceVerification.status === 'running') {
      applyVoiceVerificationEvent({ type: 'cancelled' })
      void stopVerificationSession()
    }
    setStep((current) => Math.max(0, current - 1))
  }

  const validate = (): string => {
    if (step === 0) {
      if (!data.username.trim()) return 'Username is required.'
      if (data.password.length < 8) return 'Password must be at least 8 characters.'
    }
    if (step === 1 && data.llmProvider === 'lmstudio') {
      if (!data.openaiBaseUrl.trim()) return 'LM Studio base URL is required.'
      if (!data.openaiModel.trim()) {
        return 'Choose or enter the LM Studio model to use.'
      }
    }
    if (step === 5 && !data.wakeWordId) return 'Choose a wake word.'
    if (step === 6 && !data.roomName.trim()) return 'Room is required.'
    return ''
  }

  const handleNext = (): void => {
    const validationError = validate()
    if (validationError) {
      setError(validationError)
      return
    }
    if (step === HOME_ASSISTANT_STEP) {
      void handleSubmit()
    } else if (step !== VERIFY_STEP) {
      setStep((current) => current + 1)
    }
  }

  const handleSubmit = async (deferHomeAssistant = false): Promise<void> => {
    setSubmitting(true)
    setError('')
    setSetupRuntimeWarning('')
    try {
      const submission = buildSetupSubmission(data, { deferHomeAssistant })
      const result = await window.rex.completeSetup(submission)
      if (!result.ok) {
        setError(result.error ?? 'Setup failed.')
        setSubmitting(false)
        return
      }
      setSetupPersisted(true)
      if (result.runtime_ready === false) {
        setSetupRuntimeWarning(
          result.warning ?? 'Setup was saved, but Rex could not finish starting.'
        )
        setSubmitting(false)
        setStep(DONE_STEP)
        return
      }
      setSubmitting(false)
      setStep(VERIFY_STEP)
    } catch {
      setError('Setup failed. Please try again.')
      setSubmitting(false)
    }
  }

  const selectedWakeWordName =
    wakeWords.find((wakeWord) => wakeWord.id === data.wakeWordId)?.name ?? data.wakeWordId

  const renderStep = (): React.ReactElement => {
    switch (step) {
      case 0:
        return <StepAccount data={data} onChange={handleChange} />
      case 1:
        return (
          <StepLLM
            data={data}
            onChange={handleChange}
            modelDiscoveryStatus={lmStudioModelDiscoveryStatus}
            discoveredModels={lmStudioDiscoveredModels}
            modelDiscoveryError={lmStudioModelDiscoveryError}
            onDiscoverModels={() => void handleDiscoverLmStudioModels()}
          />
        )
      case 2:
        return (
          <StepVoice
            data={data}
            onChange={handleChange}
            voices={voices}
            loading={voicesLoading}
            inventoryError={voiceInventoryError}
            previewing={voicePreviewing}
            previewPlayed={voicePreviewPlayed}
            previewError={voicePreviewError}
            onPreview={() => void handleVoicePreview()}
            onProviderChange={handleVoiceProviderChange}
            onVoiceChange={handleVoiceChange}
          />
        )
      case 3:
        return (
          <StepMicrophone
            data={data}
            devices={microphoneDevices}
            loading={audioDevicesLoading}
            inventoryError={audioDevicesError}
            testing={microphoneTesting}
            testPassed={microphoneTestPassed}
            testError={microphoneTestError}
            onTest={() => void handleMicrophoneTest()}
            onDeviceChange={handleMicrophoneDeviceChange}
          />
        )
      case 4:
        return (
          <StepSpeaker
            data={data}
            devices={speakerDevices}
            loading={audioDevicesLoading}
            inventoryError={audioDevicesError}
            testing={speakerTesting}
            testPassed={speakerTestPassed}
            testError={speakerTestError}
            onTest={() => void handleSpeakerTest()}
            onDeviceChange={handleSpeakerDeviceChange}
          />
        )
      case 5:
        return (
          <StepWakeWord
            data={data}
            onChange={handleChange}
            wakeWords={wakeWords}
            loading={wakeWordsLoading}
            inventoryError={wakeWordInventoryError}
            previewing={wakeWordPreviewing}
            samplePlayed={wakeWordSamplePlayed}
            previewError={wakeWordPreviewError}
            status={wakeWordStatus}
            statusLoading={wakeWordStatusLoading}
            statusError={wakeWordStatusError}
            onPreview={() => void handleWakeWordPreview()}
            onWakeWordChange={handleWakeWordChange}
          />
        )
      case 6:
        return <StepRoom data={data} onChange={handleChange} />
      case 7:
        return <StepBackgroundVoice data={data} onChange={handleChange} />
      case 8:
        return <StepHomeAssistant data={data} onChange={handleChange} />
      case 9:
        return (
          <StepVerifyVoice
            verification={voiceVerification}
            wakeWordName={selectedWakeWordName}
            onStart={() => void handleStartVoiceVerification()}
            onCancel={handleCancelVoiceVerification}
            onPlaybackConfirmed={handlePlaybackConfirmed}
            onPlaybackRejected={handlePlaybackRejected}
            onContinueWithoutVoice={handleContinueWithoutVoice}
            onContinue={handleVerifiedContinue}
          />
        )
      default:
        return (
          <StepDone
            voiceVerified={voiceVerification.status === 'verified'}
            runtimeWarning={setupRuntimeWarning}
          />
        )
    }
  }

  const isDone = step === DONE_STEP

  return (
    <div className="flex items-center justify-center min-h-screen bg-bg p-4">
      <div className="w-full max-w-md">
        <div className="flex items-center gap-1 mb-8">
          {STEPS.slice(0, -1).map((label, index) => (
            <div
              key={label}
              className={`flex-1 h-1 rounded-full transition-colors ${
                index <= step ? 'bg-accent' : 'bg-surface-raised'
              }`}
            />
          ))}
        </div>

        <div className="bg-surface border border-border rounded-2xl p-8 space-y-6">
          <div>
            <span className="text-xs font-semibold uppercase tracking-wider text-text-muted">
              Step {step + 1} of {STEPS.length}
            </span>
            <h2 className="text-xl font-semibold text-text-primary">
              {isDone ? (setupRuntimeWarning ? 'Setup saved' : 'All done!') : `Set up ${STEPS[step]}`}
            </h2>
          </div>

          {renderStep()}
          {error && <p className="text-red-400 text-sm">{error}</p>}

          <div className="flex items-center justify-between pt-2">
            {!isDone && !setupPersisted && step > 0 ? (
              <button
                type="button"
                onClick={handleBack}
                className="text-sm text-text-secondary hover:text-text-primary transition-colors"
              >
                ← Back
              </button>
            ) : (
              <span />
            )}

            {isDone ? (
              setupRuntimeWarning ? (
                <span />
              ) : (
                <button
                  type="button"
                  onClick={onComplete}
                  className="px-6 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 transition-colors"
                >
                  Open Dashboard
                </button>
              )
            ) : step === VERIFY_STEP ? (
              <span />
            ) : (
              <div className="flex items-center gap-2">
                {step === HOME_ASSISTANT_STEP && (
                  <button
                    type="button"
                    onClick={() => {
                      void handleSubmit(true)
                    }}
                    disabled={submitting}
                    className="text-sm text-text-secondary hover:text-text-primary transition-colors"
                  >
                    Do this later
                  </button>
                )}
                <button
                  type="button"
                  onClick={handleNext}
                  disabled={submitting}
                  className="px-6 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 transition-colors disabled:opacity-50"
                >
                  {submitting ? 'Saving…' : step === HOME_ASSISTANT_STEP ? 'Save setup' : 'Next →'}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

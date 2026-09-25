import React, { useState, useCallback, useRef, useEffect } from 'react'
import { VoiceToggle } from '../components/voice/VoiceToggle'
import type { VoiceState } from '../components/voice/VoiceToggle'
import { WaveformVisualizer } from '../components/voice/WaveformVisualizer'
import { EmptyState } from '../components/ui/EmptyState'
import { Tooltip } from '../components/ui/Tooltip'
import type {
  ChatStreamCancelHandle,
  VoiceSettings,
  VoiceTranscriptEntry,
  WakeWordAttemptEvidence,
  WakeWordRuntimeStatus,
} from '../types/ipc'
import { voiceProvider } from '../types/voiceProvider'
import {
  getWakeWordDiagnosticMessages,
  shouldRestoreWakeWordRuntimeSnapshot,
} from '../types/wakeWordDiagnostics'

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTime(ms: number): string {
  const d = new Date(ms)
  const h = d.getHours().toString().padStart(2, '0')
  const m = d.getMinutes().toString().padStart(2, '0')
  const s = d.getSeconds().toString().padStart(2, '0')
  return `${h}:${m}:${s}`
}

const knownVoiceStates: VoiceState[] = [
  'starting',
  'idle',
  'wake_listening',
  'listening',
  'followup_listening',
  'processing',
  'speaking',
  'cooldown',
  'error',
]
const internalToolSyntaxPattern = /\bTOOL_(?:REQUEST|RESULT)\s*:/i

function sanitizeTranscriptEntry(entry: VoiceTranscriptEntry): VoiceTranscriptEntry | null {
  if (!internalToolSyntaxPattern.test(entry.text)) return entry
  if (entry.role === 'user') return null
  return {
    ...entry,
    text: 'I could not complete that tool request.',
  }
}

function sanitizeDisplayText(text: string): string {
  return internalToolSyntaxPattern.test(text)
    ? 'I could not complete that tool request.'
    : text
}

function isKnownVoiceState(state: string): state is VoiceState {
  return knownVoiceStates.includes(state as VoiceState)
}

function isWakeSessionActive(state: VoiceState): boolean {
  return state !== 'idle'
}

function getVoiceStatusLabel(state: VoiceState): string | null {
  switch (state) {
    case 'starting':
      return 'Starting voice mode'
    case 'wake_listening':
      return 'Ready and listening for wake word'
    case 'listening':
      return 'Listening to your request'
    case 'followup_listening':
      return 'Listening for your answer'
    case 'processing':
      return 'Processing request'
    case 'speaking':
      return 'Speaking response'
    case 'cooldown':
      return 'Resetting microphone'
    case 'error':
      return 'Voice failed'
    case 'idle':
      return null
  }
}

// ── Wake word status indicator ────────────────────────────────────────────────

interface WakeWordStatusBadgeProps {
  isActive: boolean
  state: VoiceState
}

const statusConfig: Record<string, { label: string; color: string }> = {
  inactive: { label: 'Inactive', color: 'bg-surface-raised text-text-muted' },
  starting: { label: 'Starting voice mode', color: 'bg-accent/20 text-accent' },
  idle: { label: 'Idle', color: 'bg-surface-raised text-text-muted' },
  listening_wake: { label: 'Listening for wake word', color: 'bg-red-600/20 text-red-400' },
  detected: { label: 'Wake word detected', color: 'bg-red-600/20 text-red-400' },
  followup: { label: 'Listening for your answer', color: 'bg-red-600/20 text-red-400' },
  processing: { label: 'Processing', color: 'bg-accent/20 text-accent' },
  speaking: { label: 'Speaking', color: 'bg-green-600/20 text-green-400' },
  cooldown: { label: 'Resetting microphone', color: 'bg-accent/20 text-accent' },
}

const WakeWordStatusBadge: React.FC<WakeWordStatusBadgeProps> = ({ isActive, state }) => {
  let key: string
  if (!isActive) {
    key = 'inactive'
  } else if (state === 'starting') {
    key = 'starting'
  } else if (state === 'wake_listening') {
    key = 'listening_wake'
  } else if (state === 'idle') {
    key = 'idle'
  } else if (state === 'listening') {
    key = 'detected'
  } else if (state === 'followup_listening') {
    key = 'followup'
  } else if (state === 'processing') {
    key = 'processing'
  } else if (state === 'cooldown') {
    key = 'cooldown'
  } else {
    key = 'speaking'
  }
  const cfg = statusConfig[key] ?? statusConfig['inactive']!

  return (
    <div className="flex items-center gap-2">
      <span
        role="status"
        aria-live="polite"
        aria-label={`Voice status: ${cfg.label}`}
        className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium select-none ${cfg.color}`}
      >
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            key === 'detected'
              ? 'bg-red-400 animate-pulse'
              : key === 'starting'
                ? 'bg-accent animate-pulse'
              : key === 'listening_wake' || key === 'followup'
                ? 'bg-red-400 animate-pulse'
                : key === 'processing'
                  ? 'bg-accent animate-spin'
                  : key === 'cooldown'
                    ? 'bg-accent animate-pulse'
                  : key === 'speaking'
                    ? 'bg-green-400 animate-pulse'
                    : 'bg-text-muted'
          }`}
          aria-hidden="true"
        />
        {cfg.label}
      </span>
    </div>
  )
}

// ── Microphone device selector ────────────────────────────────────────────────

interface MicrophoneSelectorProps {
  devices: MediaDeviceInfo[]
  selectedId: string
  onChange: (id: string) => void
  onRequestPermission: () => void
}

const MicrophoneSelector: React.FC<MicrophoneSelectorProps> = ({
  devices,
  selectedId,
  onChange,
  onRequestPermission,
}) => {
  const hasLabels = devices.some((d) => d.label !== '')
  const hasDevices = devices.length > 0

  return (
    <div className="flex flex-col gap-1 w-full max-w-xs">
      <label
        htmlFor="mic-selector"
        className="text-xs text-text-muted uppercase tracking-wide"
      >
        Microphone
      </label>
      {!hasLabels && hasDevices ? (
        <div className="flex items-center gap-2">
          <select
            id="mic-selector"
            disabled
            className="flex-1 rounded-md bg-surface-raised border border-white/10 text-text-muted text-sm px-3 py-1.5 opacity-50"
          >
            <option>Permission required for device names</option>
          </select>
          <Tooltip text="Grant microphone access to see device names" position="right">
            <button
              type="button"
              onClick={onRequestPermission}
              className="text-xs px-2 py-1 rounded bg-accent/20 text-accent hover:bg-accent/30 transition-colors"
            >
              Allow
            </button>
          </Tooltip>
        </div>
      ) : (
        <select
          id="mic-selector"
          value={selectedId}
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md bg-surface-raised border border-white/10 text-text-primary text-sm px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-accent"
        >
          <option value="">System default</option>
          {devices.map((d) => (
            <option key={d.deviceId} value={d.deviceId}>
              {d.label || `Microphone ${d.deviceId.slice(0, 8)}`}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

// ── Push-to-talk button ───────────────────────────────────────────────────────

const MicIcon: React.FC = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="currentColor"
    className="w-5 h-5"
    aria-hidden="true"
  >
    <path d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4zm6.364 9.636a1 1 0 0 1 1 1A7.364 7.364 0 0 1 13 18.93V21h2a1 1 0 1 1 0 2H9a1 1 0 1 1 0-2h2v-2.07A7.364 7.364 0 0 1 4.636 11.636a1 1 0 0 1 2 0A5.364 5.364 0 0 0 12 17a5.364 5.364 0 0 0 5.364-5.364 1 1 0 0 1 1-1z" />
  </svg>
)

interface PushToTalkButtonProps {
  selectedMicId: string
  onTranscript: (entry: VoiceTranscriptEntry) => void | Promise<void>
  onMicDevicesUpdated: (devices: MediaDeviceInfo[]) => void
  onInterrupt: () => void
  onTiming: (turnId: string, stage: string, durationMs: number) => void
}

const PushToTalkButton: React.FC<PushToTalkButtonProps> = ({
  selectedMicId,
  onTranscript,
  onMicDevicesUpdated,
  onInterrupt,
  onTiming,
}) => {
  const [recording, setRecording] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<BlobPart[]>([])
  const activePointerIdRef = useRef<number | null>(null)
  const stopRequestedRef = useRef(false)
  const recordingStartedRef = useRef(0)
  const recordingTurnRef = useRef('')

  const startRecording = useCallback(async () => {
    setError(null)
    onInterrupt()
    try {
      const constraints: MediaStreamConstraints = {
        audio: selectedMicId ? { deviceId: { exact: selectedMicId } } : true,
        video: false,
      }
      const stream = await navigator.mediaDevices.getUserMedia(constraints)

      // Re-enumerate now that permission is granted — labels will be populated.
      const all = await navigator.mediaDevices.enumerateDevices()
      onMicDevicesUpdated(all.filter((d) => d.kind === 'audioinput'))

      chunksRef.current = []
      const recorder = new MediaRecorder(stream)

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      recorder.onstop = async () => {
        recorderRef.current = null
        stopRequestedRef.current = false
        stream.getTracks().forEach((t) => t.stop())
        setRecording(false)
        onTiming(
          recordingTurnRef.current,
          'recording',
          performance.now() - recordingStartedRef.current,
        )
        if (chunksRef.current.length === 0) {
          setBusy(false)
          return
        }
        setBusy(true)
        try {
          const blob = new Blob(chunksRef.current, {
            type: recorder.mimeType || 'audio/webm',
          })
          const ab = await blob.arrayBuffer()
          const bytes = new Uint8Array(ab)
          // Convert binary to base64 in 32 KB chunks to avoid stack overflow.
          const CHUNK = 0x8000
          let binary = ''
          for (let i = 0; i < bytes.length; i += CHUNK) {
            binary += String.fromCharCode.apply(
              null,
              bytes.subarray(i, i + CHUNK) as unknown as number[],
            )
          }
          const b64 = btoa(binary)
          const sttStarted = performance.now()
          const result = await window.rex.sendChatAudio(b64)
          onTiming(recordingTurnRef.current, 'stt', performance.now() - sttStarted)
          if (result.ok && result.transcript) {
            await onTranscript({
              text: result.transcript,
              role: 'user',
              timestamp: Date.now(),
            })
          } else if (!result.ok) {
            setError(result.error ?? 'Transcription failed')
          }
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err))
        } finally {
          setBusy(false)
        }
      }

      recorder.start()
      recordingTurnRef.current = `hold-${Date.now()}`
      recordingStartedRef.current = performance.now()
      recorderRef.current = recorder
      setRecording(true)
      if (stopRequestedRef.current && recorder.state !== 'inactive') {
        recorder.stop()
      }
    } catch (err) {
      stopRequestedRef.current = false
      setError(err instanceof Error ? err.message : 'Microphone access denied')
    }
  }, [selectedMicId, onTranscript, onMicDevicesUpdated, onInterrupt, onTiming])

  const stopRecording = useCallback(() => {
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop()
    } else {
      setRecording(false)
      recorderRef.current = null
    }
  }, [])

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      try {
        e.currentTarget.setPointerCapture(e.pointerId)
        activePointerIdRef.current = e.pointerId
      } catch {
        activePointerIdRef.current = null
      }
      if (!recording && !busy) {
        stopRequestedRef.current = false
        void startRecording()
      }
    },
    [recording, busy, startRecording],
  )

  const handlePointerUp = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    const pointerId = activePointerIdRef.current
    if (pointerId !== null) {
      try {
        e.currentTarget.releasePointerCapture(pointerId)
      } catch {
        // Pointer capture may already be released by the browser.
      }
      activePointerIdRef.current = null
    }
    if (recording) {
      stopRecording()
    } else {
      stopRequestedRef.current = true
    }
  }, [recording, stopRecording])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>) => {
      if (e.key === ' ' && !e.repeat) {
        e.preventDefault()
        if (!recording && !busy) {
          stopRequestedRef.current = false
          void startRecording()
        }
      }
    },
    [recording, busy, startRecording],
  )

  const handleKeyUp = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>) => {
      if (e.key === ' ' && recording) {
        e.preventDefault()
        stopRecording()
      } else if (e.key === ' ' && !recording) {
        stopRequestedRef.current = true
      }
    },
    [recording, stopRecording],
  )

  let label: string
  if (busy) label = 'Processing\u2026'
  else if (recording) label = 'Release to send'
  else label = 'Hold to talk'

  return (
    <div className="flex flex-col items-center gap-1">
      <button
        type="button"
        aria-label={label}
        aria-pressed={recording}
        disabled={busy}
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onKeyDown={handleKeyDown}
        onKeyUp={handleKeyUp}
        className={[
          'flex items-center gap-2 px-5 py-2.5 rounded-full text-sm font-medium transition-all duration-150 select-none focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-surface',
          recording
            ? 'bg-red-600 text-white focus-visible:ring-red-400 scale-95'
            : busy
              ? 'bg-surface-raised text-text-muted cursor-not-allowed'
              : 'bg-surface-raised text-text-primary hover:bg-surface-raised/80 focus-visible:ring-accent',
        ].join(' ')}
      >
        <MicIcon />
        {label}
        {recording && (
          <span
            className="w-2 h-2 rounded-full bg-white animate-pulse"
            aria-hidden="true"
          />
        )}
      </button>
      {error && (
        <span className="text-xs text-danger mt-1">{error}</span>
      )}
    </div>
  )
}

// ── Clear button with two-click confirmation ──────────────────────────────────

interface ClearButtonProps {
  onClear: () => void
}

const ClearButton: React.FC<ClearButtonProps> = ({ onClear }) => {
  const [pendingClear, setPendingClear] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleClick = useCallback(() => {
    if (pendingClear) {
      // Second click — confirm clear
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
      setPendingClear(false)
      onClear()
    } else {
      // First click — arm confirmation
      setPendingClear(true)
      timerRef.current = setTimeout(() => {
        setPendingClear(false)
        timerRef.current = null
      }, 2000)
    }
  }, [pendingClear, onClear])

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current)
    }
  }, [])

  return (
    <Tooltip
      text={pendingClear ? 'Click again to confirm' : 'Clear history'}
      position="left"
    >
      <button
        onClick={handleClick}
        aria-label="Clear transcript history"
        className={[
          'text-xs px-2 py-1 rounded transition-colors',
          pendingClear
            ? 'bg-danger/20 text-danger'
            : 'text-text-muted hover:text-text-primary hover:bg-surface-raised'
        ].join(' ')}
      >
        {pendingClear ? 'Confirm?' : 'Clear'}
      </button>
    </Tooltip>
  )
}

// ── Transcript list ───────────────────────────────────────────────────────────

interface TranscriptListProps {
  entries: VoiceTranscriptEntry[]
  voiceState: VoiceState
  onClear: () => void
  onReplay: (text: string) => void
}

const TranscriptList: React.FC<TranscriptListProps> = ({
  entries,
  voiceState,
  onClear,
  onReplay,
}) => {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries, voiceState])

  const showPartial = voiceState === 'listening' || voiceState === 'followup_listening'
  const partialLabel = voiceState === 'followup_listening' ? 'Listening for your answer' : 'Listening'

  return (
    <div className="w-full max-w-xl mt-6 flex flex-col gap-0">
      {/* Header with clear button */}
      <div className="flex items-center justify-between mb-2 px-1">
        <span className="text-xs text-text-muted uppercase tracking-wide">
          Transcript
        </span>
        <ClearButton onClear={onClear} />
      </div>

      {/* Scrollable entries */}
      <div className="flex flex-col gap-2 overflow-y-auto max-h-64 px-1">
        {entries.map((entry, idx) => {
          const isUser = entry.role === 'user'
          const isPendingRex = !isUser && entry.text.trim() === ''
          return (
            <div
              key={idx}
              className={`flex flex-col ${isUser ? 'items-end' : 'items-start'}`}
            >
              <div
                className={`max-w-xs px-3 py-2 rounded-xl text-sm leading-relaxed ${
                  isUser
                    ? 'bg-accent text-white rounded-br-none'
                    : 'bg-surface-raised text-text-primary rounded-bl-none'
                }`}
              >
                {isPendingRex ? (
                  <span role="status" aria-live="polite" className="inline-flex items-center gap-1 text-text-secondary">
                    Rex is thinking
                    <span className="inline-block w-1 h-1 rounded-full bg-accent animate-pulse" aria-hidden="true" />
                  </span>
                ) : (
                  <span>{entry.text}</span>
                )}
              </div>
              {!isUser && entry.text.trim() && (
                <button
                  type="button"
                  onClick={() => onReplay(entry.text)}
                  className="mt-1 text-xs text-text-muted hover:text-text-primary"
                  aria-label="Replay this response"
                >
                  Replay
                </button>
              )}
              <span className="text-xs text-text-muted mt-0.5 px-1">
                {isUser ? 'You' : 'Rex'} · {formatTime(entry.timestamp)}
              </span>
            </div>
          )
        })}

        {/* Partial / in-progress row (italic + blinking cursor) */}
        {showPartial && (
          <div className="flex flex-col items-end">
            <div className="max-w-xs px-3 py-2 rounded-xl text-sm leading-relaxed bg-accent/40 text-white rounded-br-none italic opacity-80">
              {partialLabel}
              <span className="inline-block w-0.5 h-3.5 ml-0.5 align-middle bg-white animate-pulse rounded-sm" />
            </div>
            <span className="text-xs text-text-muted mt-0.5 px-1">You</span>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  )
}

// ── MicOff icon for error EmptyState ─────────────────────────────────────────

const MicOffIcon: React.FC = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="currentColor"
    className="w-10 h-10"
    aria-hidden="true"
  >
    <path d="M19 11a1 1 0 0 1 2 0 9 9 0 0 1-8 8.94V22h2a1 1 0 1 1 0 2H9a1 1 0 1 1 0-2h2v-2.06A9 9 0 0 1 3 11a1 1 0 1 1 2 0 7 7 0 0 0 11.43 5.41l1.42 1.41A8.96 8.96 0 0 1 13 19.94V22h-2v-2.06A9.02 9.02 0 0 1 3 11zm-5.16 3.42-1.42-1.42A2 2 0 0 1 10 11V5a2 2 0 0 1 4 0v5.59l-1.42-1.42A.5.5 0 0 0 12 9V5a1 1 0 0 0-2 0v5a1 1 0 0 0 .29.71l.55.55zM3.29 2.29l18 18-1.42 1.42-18-18 1.42-1.42z" />
  </svg>
)

// ── Main page ─────────────────────────────────────────────────────────────────

export function VoicePage(): React.ReactElement {
  const [voiceState, setVoiceState] = useState<VoiceState>('idle')
  const [isActive, setIsActive] = useState(false)
  const [startingWakeMode, setStartingWakeMode] = useState(false)
  const [voiceStatusLabel, setVoiceStatusLabel] = useState<string | null>(null)
  const [transcripts, setTranscripts] = useState<VoiceTranscriptEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [micDevices, setMicDevices] = useState<MediaDeviceInfo[]>([])
  const [selectedMicId, setSelectedMicId] = useState<string>('')
  const [holdToTalkError, setHoldToTalkError] = useState<string | null>(null)
  const [deviceNotice, setDeviceNotice] = useState<string | null>(null)
  const [wakeRuntimeStatus, setWakeRuntimeStatus] = useState<WakeWordRuntimeStatus | null>(null)
  const [wakeAttemptEvidence, setWakeAttemptEvidence] = useState<WakeWordAttemptEvidence | null>(null)
  const wakeRuntimeRevisionRef = useRef(0)
  const activeAudioRef = useRef<HTMLAudioElement | null>(null)
  const activeTurnRef = useRef<AbortController | null>(null)

  const logTiming = useCallback((turnId: string, stage: string, durationMs: number) => {
    void window.rex.logVoiceTiming(turnId, stage, durationMs)
  }, [])

  const stopActiveResponse = useCallback(() => {
    activeTurnRef.current?.abort()
    activeTurnRef.current = null
    const audio = activeAudioRef.current
    activeAudioRef.current = null
    if (audio) {
      audio.pause()
      audio.currentTime = 0
    }
    setVoiceState('idle')
  }, [])

  const speakResponse = useCallback(async (
    text: string,
    turnId: string,
    signal: AbortSignal,
  ): Promise<void> => {
    const settings = await window.rex.getSettings('voice') as unknown as VoiceSettings
    const synthesisStarted = performance.now()
    const result = await window.rex.synthesizeSpeech(
      voiceProvider(settings.ttsEngine),
      settings.ttsVoice ?? '',
      text,
    )
    logTiming(turnId, 'tts_synthesis', performance.now() - synthesisStarted)
    if (signal.aborted) throw new DOMException('Speech cancelled', 'AbortError')
    if (!result.ok || !result.audio_base64) {
      throw new Error(`Speech synthesis failed: ${result.error ?? 'no audio returned'}`)
    }

    setVoiceState('speaking')
    const playbackStarted = performance.now()
    const mime = result.audio_base64.startsWith('UklGR') ? 'audio/wav' : 'audio/mpeg'
    const audio = new Audio(`data:${mime};base64,${result.audio_base64}`)
    activeAudioRef.current = audio
    const sinkAudio = audio as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> }
    if (settings.speakerDeviceId && typeof sinkAudio.setSinkId === 'function') {
      await sinkAudio.setSinkId(settings.speakerDeviceId)
    }
    await new Promise<void>((resolve, reject) => {
      const cancel = (): void => {
        audio.pause()
        reject(new DOMException('Speech cancelled', 'AbortError'))
      }
      signal.addEventListener('abort', cancel, { once: true })
      audio.onended = () => {
        signal.removeEventListener('abort', cancel)
        resolve()
      }
      audio.onerror = () => {
        signal.removeEventListener('abort', cancel)
        reject(new Error('Speech playback failed on the selected output device'))
      }
      void audio.play().catch(reject)
    })
    activeAudioRef.current = null
    logTiming(turnId, 'tts_playback', performance.now() - playbackStarted)
  }, [logTiming])

  // Enumerate microphone devices on mount (labels may be empty until permission granted).
  useEffect(() => {
    const loadDevices = async (): Promise<void> => {
      try {
        const devices = await navigator.mediaDevices.enumerateDevices()
        const inputs = devices.filter((d) => d.kind === 'audioinput')
        setMicDevices(inputs)
        if (selectedMicId && !inputs.some((device) => device.deviceId === selectedMicId)) {
          setSelectedMicId('')
          setDeviceNotice('Selected microphone disconnected; switched to system default.')
        }
      } catch {
        // mediaDevices not available (e.g. non-secure context) — silently skip
      }
    }
    void loadDevices()
    const handleDeviceChange = (): void => {
      void loadDevices()
    }
    navigator.mediaDevices.addEventListener('devicechange', handleDeviceChange)
    return () => {
      navigator.mediaDevices.removeEventListener('devicechange', handleDeviceChange)
    }
  }, [selectedMicId])

  const handleMicDevicesUpdated = useCallback((devices: MediaDeviceInfo[]) => {
    setMicDevices(devices)
  }, [])

  const handlePushToTalkTranscript = useCallback(async (entry: VoiceTranscriptEntry) => {
    const safeEntry = sanitizeTranscriptEntry(entry)
    if (safeEntry === null) return
    setTranscripts((prev) => [...prev, safeEntry])

    const message = safeEntry.text.trim()
    if (!message) return

    const replyTimestamp = Date.now() + 1
    let replyText = ''
    const updateReply = (text: string): void => {
      const safeText = sanitizeDisplayText(text)
      setTranscripts((prev) =>
        prev.map((item) =>
          item.role === 'rex' && item.timestamp === replyTimestamp
            ? { ...item, text: safeText }
            : item,
        ),
      )
    }

    setHoldToTalkError(null)
    stopActiveResponse()
    const controller = new AbortController()
    activeTurnRef.current = controller
    const turnId = `hold-response-${Date.now()}`
    setVoiceState('processing')
    setTranscripts((prev) => [
      ...prev,
      { role: 'rex', text: '', timestamp: replyTimestamp },
    ])

    try {
      const responseStarted = performance.now()
      // AbortSignal doesn't survive the preload contextBridge boundary with its
      // EventTarget methods intact, so pass a plain-callback handle instead
      // (built here, in the renderer's own realm, where controller.signal is real).
      const cancelHandle: ChatStreamCancelHandle = {
        isAborted: () => controller.signal.aborted,
        onAbort: (cb) => controller.signal.addEventListener('abort', cb, { once: true }),
        offAbort: (cb) => controller.signal.removeEventListener('abort', cb),
      }
      await window.rex.sendChatStream(message, (token) => {
        replyText += token
        updateReply(replyText)
      }, cancelHandle)
      logTiming(turnId, 'assistant_response', performance.now() - responseStarted)
      if (!replyText.trim()) {
        updateReply('I did not receive a reply from the model.')
      } else {
        await speakResponse(replyText, turnId, controller.signal)
      }
    } catch (err) {
      const messageText = err instanceof Error ? err.message : String(err)
      if (err instanceof DOMException && err.name === 'AbortError') {
        if (!replyText.trim()) updateReply('Response cancelled.')
      } else {
        setHoldToTalkError(messageText)
        if (!replyText.trim()) updateReply(`I could not get a reply: ${messageText}`)
      }
    } finally {
      if (activeTurnRef.current === controller) {
        activeTurnRef.current = null
        setVoiceState('idle')
      }
    }
  }, [logTiming, speakResponse, stopActiveResponse])

  const handleReplay = useCallback((text: string) => {
    stopActiveResponse()
    setHoldToTalkError(null)
    const controller = new AbortController()
    activeTurnRef.current = controller
    const turnId = `hold-replay-${Date.now()}`
    void speakResponse(text, turnId, controller.signal)
      .catch((err: unknown) => {
        if (!(err instanceof DOMException && err.name === 'AbortError')) {
          setHoldToTalkError(err instanceof Error ? err.message : String(err))
        }
      })
      .finally(() => {
        if (activeTurnRef.current === controller) {
          activeTurnRef.current = null
          setVoiceState('idle')
        }
      })
  }, [speakResponse, stopActiveResponse])

  useEffect(() => () => stopActiveResponse(), [stopActiveResponse])

  const handleClearTranscripts = useCallback(() => {
    setTranscripts([])
  }, [])

  const handleVoiceState = useCallback((state: string) => {
    if (!isKnownVoiceState(state)) return

    setVoiceState(state)
    setIsActive(isWakeSessionActive(state))
    setStartingWakeMode(state === 'starting')

    const label = getVoiceStatusLabel(state)
    setVoiceStatusLabel(label)
  }, [])

  const handleVoiceTranscript = useCallback((entry: VoiceTranscriptEntry) => {
    const safeEntry = sanitizeTranscriptEntry(entry)
    if (safeEntry === null) return
    setTranscripts((prev) => [...prev, safeEntry])
  }, [])

  const handleVoiceError = useCallback((err: string) => {
    wakeRuntimeRevisionRef.current += 1
    setError(err)
    setVoiceState('error')
    setIsActive(false)
    setStartingWakeMode(false)
    setVoiceStatusLabel('Voice failed')
  }, [])

  const handleVoiceStatus = useCallback((_status: string, label: string) => {
    setVoiceStatusLabel(label)
  }, [])

  const handleWakeWordRuntimeStatus = useCallback((status: WakeWordRuntimeStatus) => {
    wakeRuntimeRevisionRef.current += 1
    setWakeRuntimeStatus(status)
    setWakeAttemptEvidence((previous) =>
      previous && previous.detectorGeneration === status.detectorGeneration ? previous : null,
    )
  }, [])

  const handleWakeWordAttemptEvidence = useCallback((evidence: WakeWordAttemptEvidence) => {
    setWakeAttemptEvidence(evidence)
  }, [])

  useEffect(() => {
    const cleanup = window.rex.attachVoiceSession(
      handleVoiceState,
      handleVoiceTranscript,
      handleVoiceError,
      handleVoiceStatus,
      handleWakeWordRuntimeStatus,
      handleWakeWordAttemptEvidence,
    )

    let cancelled = false
    void window.rex
      .getStatus()
      .then((result) => {
        if (cancelled) return
        const status = result.status ?? 'idle'
        if (isKnownVoiceState(status)) {
          handleVoiceState(status)
        } else {
          handleVoiceState('idle')
        }
      })
      .catch(() => {
        if (!cancelled) {
          handleVoiceState('idle')
        }
      })

    // Restore wake-word diagnostics owned by the trusted main process so the
    // panel survives an unmount/remount while the same voice process runs. A
    // live event that arrives first wins (functional update keeps it).
    const snapshotRuntimeRevision = wakeRuntimeRevisionRef.current
    void window.rex
      .getWakeWordRuntimeSnapshots()
      .then((snapshots) => {
        if (
          cancelled ||
          !shouldRestoreWakeWordRuntimeSnapshot(
            snapshotRuntimeRevision,
            wakeRuntimeRevisionRef.current,
          )
        ) {
          return
        }
        if (snapshots.runtimeStatus) {
          setWakeRuntimeStatus((previous) => previous ?? snapshots.runtimeStatus)
        }
        if (snapshots.attemptEvidence) {
          setWakeAttemptEvidence((previous) => previous ?? snapshots.attemptEvidence)
        }
      })
      .catch(() => {
        // Diagnostics are best-effort; absence just means an empty panel.
      })

    return () => {
      cancelled = true
      cleanup()
    }
  }, [
    handleVoiceState,
    handleVoiceTranscript,
    handleVoiceError,
    handleVoiceStatus,
    handleWakeWordRuntimeStatus,
    handleWakeWordAttemptEvidence,
  ])

  const handleToggle = useCallback(async () => {
    if (startingWakeMode) return
    if (!isActive) {
      wakeRuntimeRevisionRef.current += 1
      setError(null)
      setTranscripts([])
      setWakeRuntimeStatus(null)
      setWakeAttemptEvidence(null)
      setStartingWakeMode(true)
      setVoiceState('starting')
      setVoiceStatusLabel('Starting bridge process')
      try {
        const microphoneLabel = selectedMicId
          ? micDevices.find((device) => device.deviceId === selectedMicId)?.label.trim()
          : undefined
        await window.rex.startVoice(
          handleVoiceState,
          handleVoiceTranscript,
          handleVoiceError,
          handleVoiceStatus,
          microphoneLabel ? { microphoneLabel } : undefined
        )
        setIsActive(true)
        setVoiceState('wake_listening')
        setVoiceStatusLabel('Ready and listening for wake word')
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        setVoiceState('error')
      } finally {
        setStartingWakeMode(false)
      }
    } else {
      wakeRuntimeRevisionRef.current += 1
      await window.rex.stopVoice()
      setIsActive(false)
      setVoiceState('idle')
      setVoiceStatusLabel(null)
      setWakeRuntimeStatus(null)
      setWakeAttemptEvidence(null)
    }
  }, [
    isActive,
    startingWakeMode,
    selectedMicId,
    micDevices,
    handleVoiceState,
    handleVoiceTranscript,
    handleVoiceError,
    handleVoiceStatus,
  ])

  // Request mic permission (needed to populate device labels).
  const handleRequestMicPermission = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false })
      stream.getTracks().forEach((t) => t.stop())
      const devices = await navigator.mediaDevices.enumerateDevices()
      setMicDevices(devices.filter((d) => d.kind === 'audioinput'))
    } catch {
      // User denied — stay with current device list
    }
  }, [])

  useEffect(() => {
    const onToggle = (): void => {
      void handleToggle()
    }
    window.addEventListener('rex:toggle-voice', onToggle)
    return () => window.removeEventListener('rex:toggle-voice', onToggle)
  }, [handleToggle])

  if (error) {
    return (
      <div role="alert" aria-live="assertive" className="flex flex-col items-center justify-center h-full gap-4">
        <EmptyState
          icon={<MicOffIcon />}
          heading="Voice backend unavailable"
          subtext={error}
          action={{
            label: 'Try again',
            onClick: () => {
              setError(null)
            }
          }}
        />
      </div>
    )
  }

  const showTranscriptPanel =
    transcripts.length > 0 || voiceState === 'listening' || voiceState === 'followup_listening'
  const wakeDiagnosticMessages = getWakeWordDiagnosticMessages(
    wakeRuntimeStatus,
    wakeAttemptEvidence,
  )

  return (
    <div className="flex flex-col items-center justify-start h-full pt-8 gap-6 overflow-y-auto pb-8">
      {/* Wake word status indicator */}
      <WakeWordStatusBadge isActive={isActive} state={voiceState} />

      {/* Audio waveform visualization */}
      <WaveformVisualizer state={voiceState} width={320} height={80} />

      {/* Main voice loop toggle (wake-word mode) */}
      <VoiceToggle
        state={voiceState}
        isActive={isActive || startingWakeMode}
        busy={startingWakeMode}
        onToggle={() => void handleToggle()}
      />

      {wakeRuntimeStatus && (
        <div className="w-full max-w-md rounded-lg border border-white/10 bg-surface-raised/40 px-4 py-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <span className="text-text-muted">Say:</span>{' '}
              <strong className="text-text-primary">
                {wakeRuntimeStatus.activePhrase ?? 'waiting for detector metadata'}
              </strong>
            </div>
            <span className="text-xs text-text-muted">
              {wakeRuntimeStatus.armed ? 'Detector armed' : 'Detector not armed'}
            </span>
          </div>
          <div className="mt-2 grid grid-cols-1 gap-1 text-xs text-text-muted sm:grid-cols-2">
            <span>Backend: {wakeRuntimeStatus.activeBackend ?? 'unknown'}</span>
            <span>Threshold: {wakeRuntimeStatus.threshold?.toFixed(3) ?? 'unknown'}</span>
            <span>Microphone: {wakeRuntimeStatus.microphoneLabel ?? 'system default'}</span>
            <span>Detector generation: {wakeRuntimeStatus.detectorGeneration}</span>
          </div>
          {wakeRuntimeStatus.fallbackActive && (
            <p className="mt-2 text-xs text-warning" role="status">
              Wake-word fallback is active. The live phrase above is authoritative.
            </p>
          )}
          <details className="mt-3">
            <summary className="cursor-pointer text-xs font-medium text-text-secondary">
              Wake-word diagnostics
            </summary>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-text-muted">
              {wakeDiagnosticMessages.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
            {wakeAttemptEvidence && (
              <p className="mt-2 text-xs text-text-muted">
                Best confidence: {wakeAttemptEvidence.maxConfidence?.toFixed(3) ?? 'unknown'};{' '}
                latest: {wakeAttemptEvidence.latestConfidence?.toFixed(3) ?? 'unknown'}.
              </p>
            )}
          </details>
        </div>
      )}

      {startingWakeMode && (
        <div className="max-w-xs text-center text-xs text-text-muted" role="status" aria-live="polite">
          <span className="inline-flex items-center gap-2">
            <span className="w-3 h-3 rounded-full border-2 border-accent/30 border-t-accent animate-spin" aria-hidden="true" />
            {voiceStatusLabel ?? 'Starting voice mode'}
          </span>
          <p className="mt-2">
            Rex will show an error here if the microphone, wake-word model, or voice dependencies cannot start.
          </p>
        </div>
      )}

      {isActive && voiceState === 'idle' && (
        <p className="max-w-xs text-center text-xs text-danger" role="status" aria-live="polite">
          Voice bridge is idle, so wake listening is not armed. Stop and start wake word mode again.
        </p>
      )}

      {isActive && voiceState === 'wake_listening' && wakeRuntimeStatus === null && (
        <p className="max-w-xs text-center text-xs text-text-muted" role="status">
          Wake listening is active, but the detector has not reported its live phrase yet.
        </p>
      )}

      {isActive && voiceState === 'followup_listening' && (
        <p className="max-w-xs text-center text-xs text-text-muted" role="status" aria-live="polite">
          Rex is waiting for your answer. You can reply now without saying the wake word again.
        </p>
      )}

      {isActive && voiceState === 'cooldown' && (
        <p className="max-w-xs text-center text-xs text-text-muted" role="status" aria-live="polite">
          Resetting the detector before listening again.
        </p>
      )}

      {isActive && voiceState === 'speaking' && voiceStatusLabel && (
        <p className="max-w-xs text-center text-xs text-text-muted" role="status" aria-live="polite">
          {voiceStatusLabel}
        </p>
      )}

      {/* Separator */}
      <div className="w-full max-w-xs border-t border-white/10 pt-4 flex flex-col items-center gap-4">
        {/* Microphone device selector */}
        <MicrophoneSelector
          devices={micDevices}
          selectedId={selectedMicId}
          onChange={setSelectedMicId}
          onRequestPermission={() => void handleRequestMicPermission()}
        />

        {deviceNotice && (
          <p className="text-xs text-warning text-center" role="status">
            {deviceNotice}
          </p>
        )}

        {/* Push-to-talk button (one-shot STT without wake word) */}
        <PushToTalkButton
          selectedMicId={selectedMicId}
          onTranscript={handlePushToTalkTranscript}
          onMicDevicesUpdated={handleMicDevicesUpdated}
          onInterrupt={stopActiveResponse}
          onTiming={logTiming}
        />
        <p className="max-w-xs text-center text-xs text-text-muted">
          Hold to talk is the reliable immediate path while wake-word mode is beta or being diagnosed.
        </p>

        {(voiceState === 'processing' || voiceState === 'speaking') && (
          <button
            type="button"
            onClick={stopActiveResponse}
            className="text-xs px-3 py-1.5 rounded bg-danger/20 text-danger hover:bg-danger/30"
          >
            Cancel response
          </button>
        )}

        {holdToTalkError && (
          <p className="text-xs text-danger text-center" role="alert">
            Hold-to-Talk: {holdToTalkError}
          </p>
        )}
      </div>

      {/* Transcript panel */}
      {showTranscriptPanel && (
        <TranscriptList
          entries={transcripts}
          voiceState={voiceState}
          onClear={handleClearTranscripts}
          onReplay={handleReplay}
        />
      )}
    </div>
  )
}

import React, { useCallback } from 'react'

export type VoiceState =
  | 'starting'
  | 'idle'
  | 'wake_listening'
  | 'listening'
  | 'followup_listening'
  | 'processing'
  | 'speaking'
  | 'cooldown'
  | 'error'

export interface VoiceToggleProps {
  state: VoiceState
  isActive: boolean
  busy?: boolean
  onToggle: () => void
}

const stateLabel: Record<VoiceState, string> = {
  starting: 'Starting wake word',
  idle: 'Start wake word',
  wake_listening: 'Listening for wake word',
  listening: 'Listening\u2026',
  followup_listening: 'Listening for reply',
  processing: 'Thinking\u2026',
  speaking: 'Speaking\u2026',
  cooldown: 'Resetting mic\u2026',
  error: 'Voice failed',
}

// SVG icons as inline components to avoid external deps

const MicIcon: React.FC = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="currentColor"
    className="w-8 h-8"
    aria-hidden="true"
  >
    <path d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4zm6.364 9.636a1 1 0 0 1 1 1A7.364 7.364 0 0 1 13 18.93V21h2a1 1 0 1 1 0 2H9a1 1 0 1 1 0-2h2v-2.07A7.364 7.364 0 0 1 4.636 11.636a1 1 0 0 1 2 0A5.364 5.364 0 0 0 12 17a5.364 5.364 0 0 0 5.364-5.364 1 1 0 0 1 1-1z" />
  </svg>
)

const WaveformIcon: React.FC = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="currentColor"
    className="w-8 h-8"
    aria-hidden="true"
  >
    <rect x="2" y="9" width="2" height="6" rx="1" />
    <rect x="6" y="6" width="2" height="12" rx="1" />
    <rect x="10" y="3" width="2" height="18" rx="1" />
    <rect x="14" y="6" width="2" height="12" rx="1" />
    <rect x="18" y="9" width="2" height="6" rx="1" />
  </svg>
)

function getButtonStyle(state: VoiceState): string {
  const base =
    'relative flex items-center justify-center rounded-full w-24 h-24 focus:outline-none focus-visible:ring-4 focus-visible:ring-offset-2 focus-visible:ring-offset-surface transition-all duration-200 select-none'

  switch (state) {
    case 'starting':
      return `${base} bg-surface-raised text-accent focus-visible:ring-accent`
    case 'idle':
      return `${base} bg-surface-raised text-text-muted hover:bg-surface-raised/80 focus-visible:ring-text-muted`
    case 'wake_listening':
      return `${base} bg-red-600 text-white focus-visible:ring-red-400`
    case 'listening':
      return `${base} bg-red-600 text-white focus-visible:ring-red-400`
    case 'followup_listening':
      return `${base} bg-red-600 text-white focus-visible:ring-red-400`
    case 'processing':
      return `${base} bg-accent text-white focus-visible:ring-accent`
    case 'speaking':
      return `${base} bg-green-600 text-white focus-visible:ring-green-400`
    case 'cooldown':
      return `${base} bg-surface-raised text-accent focus-visible:ring-accent`
    case 'error':
      return `${base} bg-danger/20 text-danger focus-visible:ring-danger`
  }
}

export const VoiceToggle: React.FC<VoiceToggleProps> = ({ state, isActive, busy = false, onToggle }) => {
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>) => {
      if (e.key === ' ') {
        e.preventDefault()
        onToggle()
      }
    },
    [onToggle],
  )

  const label = busy
    ? 'Starting wake word'
    : (state === 'wake_listening' || state === 'idle') && isActive
      ? 'Stop wake word'
      : stateLabel[state]

  return (
    <div className="flex flex-col items-center gap-3">
      <button
        type="button"
        aria-label={label}
        aria-pressed={isActive}
        disabled={busy}
        className={getButtonStyle(state)}
        onClick={onToggle}
        onKeyDown={handleKeyDown}
      >
        {/* Pulsing ring for listening state */}
        {(state === 'wake_listening' || state === 'listening' || state === 'followup_listening') && (
          <span
            className="absolute inset-0 rounded-full animate-ping bg-red-500 opacity-40"
            aria-hidden="true"
          />
        )}

        {/* Spinning ring for startup, processing, and cooldown states */}
        {(state === 'starting' || state === 'processing' || state === 'cooldown') && (
          <span
            className="absolute inset-0 rounded-full border-4 border-transparent border-t-white animate-spin"
            aria-hidden="true"
          />
        )}

        {/* Icon */}
        <span className="relative z-10">
          {state === 'speaking' ? <WaveformIcon /> : <MicIcon />}
        </span>
      </button>

      <span className="text-sm text-text-muted select-none">{label}</span>
    </div>
  )
}

export default VoiceToggle

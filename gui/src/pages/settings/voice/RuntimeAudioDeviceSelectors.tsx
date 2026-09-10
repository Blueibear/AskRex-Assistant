import type { SetupAudioDevice, VoiceSettings } from '../../../types/ipc'
import { SavedIndicator } from '../shared'

interface RuntimeAudioDeviceSelectorsProps {
  form: VoiceSettings
  devices: SetupAudioDevice[]
  savedField: keyof VoiceSettings | null
  onChange: <K extends keyof VoiceSettings>(field: K, value: VoiceSettings[K]) => void
}

export function RuntimeAudioDeviceSelectors({
  form,
  devices,
  savedField,
  onChange,
}: RuntimeAudioDeviceSelectorsProps): React.ReactElement {
  const microphones = devices.filter((device) => device.max_input_channels > 0)
  const speakers = devices.filter((device) => device.max_output_channels > 0)

  return (
    <>
      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="microphoneDeviceIndex" className="text-sm font-medium text-text-primary">
            Microphone
          </label>
          <SavedIndicator visible={savedField === 'microphoneDeviceIndex'} />
        </div>
        <select
          id="microphoneDeviceIndex"
          value={form.microphoneDeviceIndex ?? ''}
          onChange={(event) =>
            onChange(
              'microphoneDeviceIndex',
              event.target.value === '' ? null : Number(event.target.value),
            )
          }
          className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
        >
          <option value="">System default</option>
          {microphones.map((device) => (
            <option key={device.index} value={device.index}>
              {device.name}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-text-secondary">
          Changing this restarts only the Voice Agent so Rex can recover from a disconnected microphone.
        </p>
      </div>

      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="speakerDeviceIndex" className="text-sm font-medium text-text-primary">
            Speaker
          </label>
          <SavedIndicator visible={savedField === 'speakerDeviceIndex'} />
        </div>
        <select
          id="speakerDeviceIndex"
          value={form.speakerDeviceIndex ?? ''}
          onChange={(event) =>
            onChange(
              'speakerDeviceIndex',
              event.target.value === '' ? null : Number(event.target.value),
            )
          }
          className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
        >
          <option value="">System default</option>
          {speakers.map((device) => (
            <option key={device.index} value={device.index}>
              {device.name}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-text-secondary">
          Changing this restarts only the Voice Agent so playback recovery does not interrupt Rex Core.
        </p>
      </div>
    </>
  )
}

import React from 'react'
import type { VoiceSettings, WakeWordStatus } from '../../../types/ipc'
import { PageLoadingFallback } from '../../../components/ui/PageLoadingFallback'
import { Tooltip } from '../../../components/ui/Tooltip'
import { SavedIndicator } from '../shared'
import { RuntimeAudioDeviceSelectors } from './RuntimeAudioDeviceSelectors'
import { useVoiceSettingsController } from './useVoiceSettingsController'
import { ENROLLMENT_SAMPLE_TARGET, WW_POSITIVE_TARGET, WW_NEGATIVE_TARGET, defaultCustomWakeWordAssetPath } from './voiceHelpers'

function WakeWordStatusPanel({ status }: { status: WakeWordStatus | null }): React.ReactElement | null {
  if (!status) return null

  const badgeClasses =
    status.status === 'asset_ready'
      ? 'bg-success/15 text-success border-success/30'
      : status.status === 'missing_asset'
        ? 'bg-warning/15 text-warning border-warning/30'
        : 'bg-surface text-text-secondary border-border'

  return (
    <div className="rounded-lg border border-border bg-bg px-3 py-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-medium text-text-primary">Wake Asset Status</div>
        <span className={`inline-flex items-center rounded-md border px-2 py-1 text-xs font-medium ${badgeClasses}`}>
          {status.statusLabel}
        </span>
      </div>
      <div className="mt-2 space-y-1 text-xs text-text-secondary">
        <div>
          Backend: <span className="text-text-primary">{status.requestedBackend}</span>
        </div>
        <div>
          Phrase: <span className="text-text-primary">{status.configuredPhrase}</span>
        </div>
        {status.assetKind !== 'builtin' && (
          <>
            <div>
              Expected asset path: <span className="break-all text-text-primary">{status.assetPath}</span>
            </div>
            <div>
              Asset file: <span className="text-text-primary">{status.assetExists ? 'present' : 'missing'}</span>
            </div>
            <div>
              Built-in fallback: <span className="text-text-primary">{status.fallbackActive ? `active (${status.fallbackKeyword})` : status.fallbackEnabled ? `configured (${status.fallbackKeyword})` : 'disabled'}</span>
            </div>
          </>
        )}
        <div>{status.detail}</div>
      </div>
    </div>
  )
}


export function VoiceSettingsSection(): React.ReactElement {
  const {
    form,
    setForm,
    loading,
    runtimeAudioDevices,
    mics,
    savedField,
    testing,
    testResult,
    voices,
    voicesLoading,
    previewing,
    wakeWordStatus,
    previewingWakeWord,
    showWwTrainer,
    setShowWwTrainer,
    wwTrainPhrase,
    setWwTrainPhrase,
    wwTraining,
    wwTrainStep,
    setWwTrainStep,
    wwPositiveSamples,
    wwNegativeSamples,
    wwTrainMessage,
    setWwTrainMessage,
    wwTrainError,
    setWwTrainError,
    wwTrainCountdown,
    activeUserId,
    enrollments,
    enrollmentCountdown,
    capturedSamples,
    enrollmentMessage,
    enrollmentError,
    enrolling,
    deletingUserId,
    uploadFile,
    uploadFileDuration,
    uploadVoiceName,
    setUploadVoiceName,
    uploading,
    uploadResult,
    builtInWakeWordOptions,
    customWakeWords,
    saveField,
    handleFieldChange,
    handleTestVoice,
    handlePreviewVoice,
    handlePreviewWakeWord,
    handleStartWwTraining,
    handleUploadFileChange,
    handleUploadCustomVoice,
    handleStartEnrollment,
    handleDeleteEnrollment
  } = useVoiceSettingsController()

  if (loading) {
    return <PageLoadingFallback lines={6} />
  }

  return (
    <div className="p-6 max-w-lg">
      <h2 className="text-lg font-semibold text-text-primary mb-6">Voice</h2>

      <div className="mb-6 rounded-xl border border-border bg-surface-raised/40 p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <label htmlFor="backgroundVoiceEnabled" className="text-sm font-medium text-text-primary">
              Start wake-word listening automatically when I sign in
            </label>
            <p className="mt-1 text-xs text-text-secondary">
              You can pause listening at any time from the tray. Text and mobile access stay available.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <SavedIndicator visible={savedField === 'backgroundVoiceEnabled'} />
            <input
              id="backgroundVoiceEnabled"
              type="checkbox"
              checked={form.backgroundVoiceEnabled}
              onChange={(e) => handleFieldChange('backgroundVoiceEnabled', e.target.checked)}
              className="mt-1 h-4 w-4 rounded border-border text-accent accent-accent cursor-pointer"
            />
          </div>
        </div>
      </div>

      <RuntimeAudioDeviceSelectors
        form={form}
        devices={runtimeAudioDevices}
        savedField={savedField}
        onChange={handleFieldChange}
      />

      <div className="mb-6 rounded-xl border border-border bg-surface-raised/40 p-4">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="microphoneDeviceId" className="text-sm font-medium text-text-primary">
            Browser capture microphone
          </label>
          <SavedIndicator visible={savedField === 'microphoneDeviceId'} />
        </div>
        <p className="mb-2 text-xs text-text-secondary">
          Used only for wake-word training and voice enrollment recordings.
        </p>
        <select
          id="microphoneDeviceId"
          value={form.microphoneDeviceId}
          onChange={(e) => handleFieldChange('microphoneDeviceId', e.target.value)}
          className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
        >
          <option value="">System default (browser capture)</option>
          {mics.map((device) => (
            <option key={device.deviceId} value={device.deviceId}>{device.label}</option>
          ))}
        </select>
      </div>

      {/* STT section */}
      <div className="mb-6 rounded-xl border border-border bg-surface-raised/40 p-4">
        <h3 className="mb-4 text-sm font-semibold text-text-primary">Speech-to-Text (Whisper)</h3>
        <div className="mb-4">
          <div className="flex items-center justify-between mb-1.5">
            <label htmlFor="sttModel" className="text-sm font-medium text-text-primary">
              Model Size
            </label>
            <SavedIndicator visible={savedField === 'sttModel'} />
          </div>
          <select
            id="sttModel"
            value={form.sttModel}
            onChange={(e) => handleFieldChange('sttModel', e.target.value)}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
          >
            <option value="tiny">tiny — fastest, lowest accuracy</option>
            <option value="base">base — good balance</option>
            <option value="small">small</option>
            <option value="medium">medium</option>
            <option value="large">large</option>
            <option value="large-v2">large-v2</option>
            <option value="large-v3">large-v3 — best accuracy</option>
          </select>
        </div>
        <div className="mb-4">
          <div className="flex items-center justify-between mb-1.5">
            <label htmlFor="sttLanguage" className="text-sm font-medium text-text-primary">
              Language
            </label>
            <SavedIndicator visible={savedField === 'sttLanguage'} />
          </div>
          <select
            id="sttLanguage"
            value={form.sttLanguage}
            onChange={(e) => handleFieldChange('sttLanguage', e.target.value)}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
          >
            <option value="auto">Auto-detect</option>
            <option value="en">English</option>
            <option value="es">Spanish</option>
            <option value="fr">French</option>
            <option value="de">German</option>
            <option value="it">Italian</option>
            <option value="pt">Portuguese</option>
            <option value="zh">Chinese</option>
            <option value="ja">Japanese</option>
            <option value="ko">Korean</option>
            <option value="ar">Arabic</option>
            <option value="ru">Russian</option>
            <option value="nl">Dutch</option>
            <option value="pl">Polish</option>
            <option value="tr">Turkish</option>
          </select>
        </div>
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label htmlFor="sttDevice" className="text-sm font-medium text-text-primary">
              Device
            </label>
            <SavedIndicator visible={savedField === 'sttDevice'} />
          </div>
          <select
            id="sttDevice"
            value={form.sttDevice}
            onChange={(e) => handleFieldChange('sttDevice', e.target.value as VoiceSettings['sttDevice'])}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
          >
            <option value="auto">Auto (prefer GPU)</option>
            <option value="cpu">CPU</option>
            <option value="cuda">GPU (CUDA)</option>
          </select>
        </div>
      </div>

      {/* Wake word */}
      <div className="mb-5">
        <div className="space-y-4 rounded-lg border border-border bg-surface-raised p-4">
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label htmlFor="wakeWordBackend" className="text-sm font-medium text-text-primary">
                Wake Backend
              </label>
              <SavedIndicator visible={savedField === 'wakeWordBackend'} />
            </div>
            <select
              id="wakeWordBackend"
              value={form.wakeWordBackend}
              onChange={(e) => {
                const backend = e.target.value as VoiceSettings['wakeWordBackend']
                const selectedCustom = customWakeWords.find((w) => w.id === form.customWakeWordId)
                const updated: VoiceSettings = {
                  ...form,
                  wakeWordBackend: backend,
                  wakeWordPhrase:
                    form.wakeWordPhrase.trim()
                    || selectedCustom?.name
                    || 'hey rex',
                  wakeWordModelPath:
                    backend === 'custom_onnx'
                      ? (
                          form.wakeWordModelPath.trim()
                          || defaultCustomWakeWordAssetPath(
                            'custom_onnx',
                            form.wakeWordPhrase || selectedCustom?.name || 'hey rex'
                          )
                        )
                      : form.wakeWordModelPath,
                  wakeWordEmbeddingPath:
                    backend === 'custom_embedding'
                      ? (
                          form.wakeWordEmbeddingPath.trim()
                          || selectedCustom?.model_path
                          || defaultCustomWakeWordAssetPath(
                            'custom_embedding',
                            form.customWakeWordId || form.wakeWordPhrase || selectedCustom?.name || 'hey rex'
                          )
                        )
                      : form.wakeWordEmbeddingPath
                }
                saveField('wakeWordBackend', backend, updated)
              }}
              className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="openwakeword">Built-in openWakeWord</option>
              <option value="custom_onnx">Custom ONNX model</option>
              <option value="custom_embedding">Custom embedding</option>
            </select>
          </div>

          <WakeWordStatusPanel status={wakeWordStatus} />

          {form.wakeWordBackend === 'openwakeword' && (
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label htmlFor="wakeWord" className="text-sm font-medium text-text-primary">
                  Wake Word
                </label>
                <SavedIndicator visible={savedField === 'wakeWord'} />
              </div>
              <div className="flex items-center gap-2">
                <select
                  id="wakeWord"
                  value={form.wakeWord}
                  onChange={(e) => handleFieldChange('wakeWord', e.target.value)}
                  className="flex-1 bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
                >
                  <option value="">Disabled</option>
                  {builtInWakeWordOptions.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
                <button
                  onClick={handlePreviewWakeWord}
                  disabled={previewingWakeWord || !form.wakeWord}
                  title="Play a sample of this wake word"
                  className="flex items-center gap-1.5 bg-bg hover:bg-surface border border-border disabled:opacity-40 text-text-primary text-sm font-medium px-3 py-2 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-bg shrink-0"
                >
                  {previewingWakeWord ? (
                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
                    </svg>
                  ) : (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polygon points="5 3 19 12 5 21 5 3" />
                    </svg>
                  )}
                  Sample
                </button>
              </div>
            </div>
          )}

          {form.wakeWordBackend === 'custom_onnx' && (
            <>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label htmlFor="wakeWordPhrase" className="text-sm font-medium text-text-primary">
                    Custom Wake Phrase
                  </label>
                  <SavedIndicator visible={savedField === 'wakeWordPhrase'} />
                </div>
                <div className="flex items-center gap-2">
                  <input
                    id="wakeWordPhrase"
                    type="text"
                    value={form.wakeWordPhrase}
                    onChange={(e) => handleFieldChange('wakeWordPhrase', e.target.value)}
                    placeholder="hey rex"
                    className="flex-1 bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
                  />
                  <button
                    onClick={handlePreviewWakeWord}
                    disabled={previewingWakeWord || !form.wakeWordPhrase.trim()}
                    title="Play the configured custom phrase"
                    className="flex items-center gap-1.5 bg-bg hover:bg-surface border border-border disabled:opacity-40 text-text-primary text-sm font-medium px-3 py-2 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-bg shrink-0"
                  >
                    {previewingWakeWord ? (
                      <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
                      </svg>
                    ) : (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polygon points="5 3 19 12 5 21 5 3" />
                      </svg>
                    )}
                    Sample
                  </button>
                </div>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label htmlFor="wakeWordModelPath" className="text-sm font-medium text-text-primary">
                    Custom ONNX Model Path
                  </label>
                  <SavedIndicator visible={savedField === 'wakeWordModelPath'} />
                </div>
                <input
                  id="wakeWordModelPath"
                  type="text"
                  value={form.wakeWordModelPath}
                  onChange={(e) => handleFieldChange('wakeWordModelPath', e.target.value)}
                  placeholder="config\\wake_words\\hey_rex\\model.onnx"
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label htmlFor="wakeWordFallback" className="text-sm font-medium text-text-primary">
                    Built-in Fallback Wake Word
                  </label>
                  <SavedIndicator visible={savedField === 'wakeWord'} />
                </div>
                <select
                  id="wakeWordFallback"
                  value={form.wakeWord}
                  onChange={(e) => handleFieldChange('wakeWord', e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
                >
                  {builtInWakeWordOptions.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
              </div>
            </>
          )}

          {form.wakeWordBackend === 'custom_embedding' && (
            <>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label htmlFor="customWakeWordId" className="text-sm font-medium text-text-primary">
                    Trained Custom Wake Word
                  </label>
                  <SavedIndicator visible={savedField === 'customWakeWordId'} />
                </div>
                <div className="flex items-center gap-2">
                  <select
                    id="customWakeWordId"
                    value={form.customWakeWordId}
                    onChange={(e) => {
                      const selectedId = e.target.value
                      const selected = customWakeWords.find((w) => w.id === selectedId)
                      const updated: VoiceSettings = {
                        ...form,
                        customWakeWordId: selectedId,
                        wakeWordBackend: 'custom_embedding',
                        wakeWordPhrase: selected?.name ?? form.wakeWordPhrase,
                        wakeWordEmbeddingPath: selected?.model_path ?? form.wakeWordEmbeddingPath
                      }
                      saveField('customWakeWordId', selectedId, updated)
                    }}
                    className="flex-1 bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
                  >
                    <option value="">Select a trained wake word</option>
                    {customWakeWords.map((w) => (
                      <option key={w.id} value={w.id}>
                        {w.name}
                      </option>
                    ))}
                  </select>
                  <Tooltip
                    text={
                      form.customWakeWordId && customWakeWords.find((w) => w.id === form.customWakeWordId)?.has_sample === false
                        ? 'No sample recorded yet. Train this wake word to capture a recording.'
                        : ''
                    }
                    position="top"
                  >
                    <button
                      onClick={handlePreviewWakeWord}
                      disabled={
                        previewingWakeWord
                        || !form.customWakeWordId
                        || customWakeWords.find((w) => w.id === form.customWakeWordId)?.has_sample === false
                      }
                      title="Play the recorded custom wake word sample"
                      className="flex items-center gap-1.5 bg-bg hover:bg-surface border border-border disabled:opacity-40 text-text-primary text-sm font-medium px-3 py-2 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-bg shrink-0"
                    >
                      {previewingWakeWord ? (
                        <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
                        </svg>
                      ) : (
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <polygon points="5 3 19 12 5 21 5 3" />
                        </svg>
                      )}
                      Sample
                    </button>
                  </Tooltip>
                </div>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label htmlFor="wakeWordPhrase" className="text-sm font-medium text-text-primary">
                    Custom Wake Phrase
                  </label>
                  <SavedIndicator visible={savedField === 'wakeWordPhrase'} />
                </div>
                <input
                  id="wakeWordPhrase"
                  type="text"
                  value={form.wakeWordPhrase}
                  onChange={(e) => handleFieldChange('wakeWordPhrase', e.target.value)}
                  placeholder="hey rex"
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label htmlFor="wakeWordEmbeddingPath" className="text-sm font-medium text-text-primary">
                    Custom Embedding Path
                  </label>
                  <SavedIndicator visible={savedField === 'wakeWordEmbeddingPath'} />
                </div>
                <input
                  id="wakeWordEmbeddingPath"
                  type="text"
                  value={form.wakeWordEmbeddingPath}
                  onChange={(e) => handleFieldChange('wakeWordEmbeddingPath', e.target.value)}
                  placeholder="config\\wake_words\\hey_rex\\embedding.pt"
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
                />
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label htmlFor="wakeWordFallbackCustomEmbedding" className="text-sm font-medium text-text-primary">
                    Built-in Fallback Wake Word
                  </label>
                  <SavedIndicator visible={savedField === 'wakeWord'} />
                </div>
                <select
                  id="wakeWordFallbackCustomEmbedding"
                  value={form.wakeWord}
                  onChange={(e) => handleFieldChange('wakeWord', e.target.value)}
                  className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
                >
                  {builtInWakeWordOptions.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
              </div>
            </>
          )}

          <p className="text-xs text-text-secondary">
            Built-in openWakeWord remains the fallback path. For a real custom <span className="font-medium text-text-primary">Hey Rex</span> model,
            point the ONNX backend at an exported wake model or use a trained embedding while that ONNX asset is still pending.
            Changes take effect when the voice loop restarts.
          </p>
        </div>
      </div>

      {/* Train custom wake word */}
      <div className="mb-5">
        <button
          onClick={() => {
            setShowWwTrainer((v) => !v)
            setWwTrainStep('idle')
            setWwTrainError(null)
            setWwTrainMessage(null)
          }}
          className="text-sm font-medium text-accent hover:underline focus:outline-none"
        >
          {showWwTrainer ? 'Hide wake word trainer' : 'Train Custom Wake Word'}
        </button>

        {showWwTrainer && (
          <div className="mt-3 p-4 bg-surface-raised border border-border rounded-lg space-y-3">
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">Phrase</label>
              <input
                type="text"
                placeholder='e.g. "hey rex"'
                value={wwTrainPhrase}
                onChange={(e) => setWwTrainPhrase(e.target.value)}
                disabled={wwTraining}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent disabled:opacity-50"
              />
            </div>

            {wwTrainStep === 'positive' && (
              <div className="text-sm text-text-primary">
                <p className="font-medium mb-1">Recording positive samples</p>
                <p className="text-text-secondary">{wwTrainMessage}</p>
                {wwTrainCountdown > 0 && (
                  <p className="text-accent font-bold text-lg">{wwTrainCountdown}</p>
                )}
                <p className="text-xs text-text-secondary mt-1">
                  {wwPositiveSamples.length} / {WW_POSITIVE_TARGET} captured
                </p>
              </div>
            )}

            {wwTrainStep === 'negative' && (
              <div className="text-sm text-text-primary">
                <p className="font-medium mb-1">Recording background samples</p>
                <p className="text-text-secondary">{wwTrainMessage}</p>
                {wwTrainCountdown > 0 && (
                  <p className="text-accent font-bold text-lg">{wwTrainCountdown}</p>
                )}
                <p className="text-xs text-text-secondary mt-1">
                  {wwNegativeSamples.length} / {WW_NEGATIVE_TARGET} captured
                </p>
              </div>
            )}

            {wwTrainStep === 'done' && wwTrainMessage && (
              <p className="text-sm text-green-400">{wwTrainMessage}</p>
            )}

            {wwTrainError && (
              <p className="text-sm text-red-400">{wwTrainError}</p>
            )}

            {wwTrainStep === 'idle' && !wwTraining && (
              <p className="text-xs text-text-secondary">
                You will record {WW_POSITIVE_TARGET} samples of the phrase and {WW_NEGATIVE_TARGET} background samples.
                Keep recordings to ~1 second each.
              </p>
            )}

            <button
              onClick={() => { void handleStartWwTraining() }}
              disabled={wwTraining || !wwTrainPhrase.trim()}
              className="flex items-center gap-2 bg-accent hover:bg-accent/90 disabled:opacity-40 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-bg"
            >
              {wwTraining && (
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
                </svg>
              )}
              {wwTraining ? 'Recording…' : 'Start Recording'}
            </button>
          </div>
        )}
      </div>

      {/* TTS engine */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="ttsEngine" className="text-sm font-medium text-text-primary">
            TTS Engine
          </label>
          <SavedIndicator visible={savedField === 'ttsEngine'} />
        </div>
        <select
          id="ttsEngine"
          value={form.ttsEngine}
          onChange={(e) => {
            const v = e.target.value as VoiceSettings['ttsEngine']
            handleFieldChange('ttsEngine', v)
          }}
          className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
        >
          <option value="pyttsx3">pyttsx3 (offline, system voices)</option>
          <option value="edge-tts">edge-tts (Microsoft, requires internet)</option>
          <option value="xtts">XTTS (Coqui, voice cloning)</option>
        </select>
      </div>

      {/* TTS voice selector */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="ttsVoice" className="text-sm font-medium text-text-primary">
            Voice
          </label>
          <SavedIndicator visible={savedField === 'ttsVoice'} />
        </div>
        <div className="flex items-center gap-2">
          {voicesLoading ? (
            <div className="flex-1 flex items-center gap-2 bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-secondary">
              <svg className="animate-spin h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
              </svg>
              Loading voices…
            </div>
          ) : voices.length > 0 ? (
            <select
              id="ttsVoice"
              value={form.ttsVoice}
              onChange={(e) => handleFieldChange('ttsVoice', e.target.value)}
              className="flex-1 bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="">Select a voice…</option>
              {voices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}{v.language ? ` (${v.language})` : ''}{v.engine ? ` [${v.engine}]` : ''}
                </option>
              ))}
            </select>
          ) : (
            <input
              id="ttsVoice"
              type="text"
              value={form.ttsVoice}
              placeholder="Enter voice ID or name"
              onChange={(e) => setForm((f) => ({ ...f, ttsVoice: e.target.value }))}
              onBlur={(e) => saveField('ttsVoice', e.target.value)}
              className="flex-1 bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent"
            />
          )}
          <button
            onClick={handlePreviewVoice}
            disabled={previewing || !form.ttsVoice}
            title="Preview voice"
            className="flex items-center gap-1.5 bg-surface-raised hover:bg-surface border border-border disabled:opacity-40 text-text-primary text-sm font-medium px-3 py-2 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-bg shrink-0"
          >
            {previewing ? (
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
            )}
            Preview
          </button>
        </div>
      </div>

      {/* Custom Voice Upload (XTTS only) */}
      {form.ttsEngine === 'xtts' && (
        <div className="mb-5 p-4 bg-surface-raised border border-border rounded-lg">
          <p className="text-sm font-medium text-text-primary mb-3">Upload Custom Voice (XTTS)</p>
          <p className="text-xs text-text-secondary mb-3">
            Upload a WAV or MP3 recording (minimum 10 seconds) to create a custom speaker voice.
          </p>
          <div className="space-y-3">
            <input
              type="file"
              accept=".wav,.mp3"
              onChange={handleUploadFileChange}
              className="block w-full text-sm text-text-secondary file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-xs file:font-medium file:bg-accent file:text-white hover:file:bg-accent/80 cursor-pointer"
            />
            {uploadFile && uploadFileDuration !== null && (
              <div className="text-xs">
                {uploadFileDuration >= 10 ? (
                  <span className="text-green-500">{uploadFileDuration.toFixed(1)}s — ready</span>
                ) : (
                  <span className="text-amber-500">
                    {uploadFileDuration.toFixed(1)}s — need {(10 - uploadFileDuration).toFixed(1)}s more
                  </span>
                )}
              </div>
            )}
            {uploadFile && (
              <input
                type="text"
                value={uploadVoiceName}
                placeholder="Voice name"
                onChange={(e) => setUploadVoiceName(e.target.value)}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent"
              />
            )}
            {uploadFile && (
              <button
                onClick={handleUploadCustomVoice}
                disabled={uploading || !uploadVoiceName.trim() || (uploadFileDuration !== null && uploadFileDuration < 10)}
                className="flex items-center gap-2 bg-accent hover:bg-accent/80 disabled:opacity-40 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-accent"
              >
                {uploading ? (
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
                  </svg>
                ) : null}
                {uploading ? 'Saving…' : 'Create Voice'}
              </button>
            )}
            {uploadResult && (
              <p className={`text-xs ${uploadResult.ok ? 'text-green-500' : 'text-red-400'}`}>
                {uploadResult.message}
              </p>
            )}
          </div>
        </div>
      )}

      {/* Speech rate */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="speechRate" className="text-sm font-medium text-text-primary">
            Speech Rate
            <span className="ml-2 text-xs text-text-secondary font-normal">
              {form.speechRate.toFixed(1)}×
            </span>
          </label>
          <SavedIndicator visible={savedField === 'speechRate'} />
        </div>
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          <span>Slow</span>
          <input
            id="speechRate"
            type="range"
            min={0.5}
            max={2.0}
            step={0.1}
            value={form.speechRate}
            onChange={(e) => handleFieldChange('speechRate', parseFloat(e.target.value))}
            className="flex-1 accent-accent"
          />
          <span>Fast</span>
        </div>
      </div>

      {/* Volume */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="volume" className="text-sm font-medium text-text-primary">
            Volume
            <span className="ml-2 text-xs text-text-secondary font-normal">
              {Math.round(form.volume * 100)}%
            </span>
          </label>
          <SavedIndicator visible={savedField === 'volume'} />
        </div>
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          <span>0%</span>
          <input
            id="volume"
            type="range"
            min={0}
            max={1.0}
            step={0.05}
            value={form.volume}
            onChange={(e) => handleFieldChange('volume', parseFloat(e.target.value))}
            className="flex-1 accent-accent"
          />
          <span>100%</span>
        </div>
      </div>

      {/* Test Voice button */}
      <div className="border-t border-border pt-5 flex items-center gap-3">
        <button
          onClick={handleTestVoice}
          disabled={testing}
          className="flex items-center gap-2 bg-accent hover:bg-accent/90 disabled:opacity-50 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-bg"
        >
          {testing ? (
            <>
              <svg
                className="animate-spin h-4 w-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
              </svg>
              Testing…
            </>
          ) : (
            <>
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
              Test Voice
            </>
          )}
        </button>
        {testResult === 'ok' && (
          <span className="flex items-center gap-1 text-xs text-success">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
              <polyline points="20 6 9 17 4 12" />
            </svg>
            Playing sample
          </span>
        )}
        {testResult === 'error' && (
          <span className="text-xs text-danger">Failed to play sample</span>
        )}
      </div>

      <div className="mt-8 border-t border-border pt-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">Enroll Voice</h3>
            <p className="mt-1 text-sm text-text-secondary">
              Record three short samples for the active user so Rex can recognize your voice.
            </p>
            <p className="mt-1 text-xs text-text-secondary">
              Active user: <span className="font-medium text-text-primary">{activeUserId}</span>
            </p>
          </div>
          <button
            onClick={() => {
              void handleStartEnrollment()
            }}
            disabled={enrolling}
            className="flex items-center gap-2 bg-accent hover:bg-accent/90 disabled:opacity-50 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-bg"
          >
            {enrolling ? (
              <>
                <svg
                  className="animate-spin h-4 w-4"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
                </svg>
                Recording...
              </>
            ) : (
              'Start Enrollment'
            )}
          </button>
        </div>

        <div className="mt-4 rounded-xl border border-border bg-surface-raised p-4">
          <div className="flex items-center justify-between text-sm text-text-primary">
            <span>Samples captured</span>
            <span>
              {capturedSamples}/{ENROLLMENT_SAMPLE_TARGET}
            </span>
          </div>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface">
            <div
              className="h-full rounded-full bg-accent transition-all duration-300"
              style={{
                width: `${(capturedSamples / ENROLLMENT_SAMPLE_TARGET) * 100}%`
              }}
            />
          </div>
          <div className="mt-4 flex items-center justify-between text-sm">
            <span className="text-text-secondary">
              {enrollmentCountdown > 0
                ? `Sample ${Math.min(capturedSamples + 1, ENROLLMENT_SAMPLE_TARGET)} starts in`
                : enrolling
                  ? 'Recording now'
                  : 'Ready to record'}
            </span>
            <span className="text-2xl font-semibold text-text-primary tabular-nums">
              {enrollmentCountdown > 0 ? enrollmentCountdown : enrolling ? 'REC' : '--'}
            </span>
          </div>
          {enrollmentMessage && (
            <p className="mt-3 text-sm text-success">{enrollmentMessage}</p>
          )}
          {enrollmentError && (
            <p className="mt-3 text-sm text-danger">{enrollmentError}</p>
          )}
        </div>

        <div className="mt-6">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-text-primary">Enrolled Users</h4>
            <span className="text-xs text-text-secondary">{enrollments.length} total</span>
          </div>
          <div className="mt-3 space-y-3">
            {enrollments.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border bg-surface-raised px-4 py-5 text-sm text-text-secondary">
                No voice enrollments yet.
              </div>
            ) : (
              enrollments.map((enrollment) => (
                <div
                  key={enrollment.user_id}
                  className="flex items-center justify-between gap-4 rounded-xl border border-border bg-surface-raised px-4 py-3"
                >
                  <div>
                    <div className="text-sm font-medium text-text-primary">
                      {enrollment.user_id}
                    </div>
                    <div className="mt-1 text-xs text-text-secondary">
                      {enrollment.sample_count} samples, {enrollment.model_id}
                      {enrollment.updated_at ? `, updated ${new Date(enrollment.updated_at).toLocaleString()}` : ''}
                    </div>
                  </div>
                  <button
                    onClick={() => handleDeleteEnrollment(enrollment.user_id)}
                    disabled={deletingUserId === enrollment.user_id}
                    className="rounded-lg border border-danger/30 px-3 py-2 text-sm font-medium text-danger transition-colors hover:bg-danger/10 disabled:opacity-50"
                  >
                    {deletingUserId === enrollment.user_id ? 'Deleting...' : 'Delete Enrollment'}
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

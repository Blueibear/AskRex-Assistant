import { useEffect, useRef, useState } from 'react'
import type { Settings, SetupAudioDevice, VoiceEnrollment, VoiceInfo, VoiceSettings, WakeWordInfo, WakeWordStatus } from '../../../types/ipc'
import { useToast } from '../../../components/ui/Toast'
import type { MediaDeviceOption } from './voiceHelpers'
import { ENROLLMENT_SAMPLE_TARGET, WW_POSITIVE_TARGET, WW_NEGATIVE_TARGET, FALLBACK_BUILTIN_WAKE_WORDS, sleep, captureEnrollmentSample, runEnrollmentCountdown } from './voiceHelpers'

function normalizeTtsEngine(value: unknown): VoiceSettings['ttsEngine'] {
  if (value === 'xtts' || value === 'edge-tts' || value === 'pyttsx3') return value
  if (value === 'elevenlabs') return 'xtts'
  if (value === 'openai') return 'edge-tts'
  return 'pyttsx3'
}

function normalizeSttDevice(value: unknown): VoiceSettings['sttDevice'] {
  return value === 'cpu' || value === 'cuda' ? value : 'auto'
}

function normalizeWakeWordBackend(value: unknown): VoiceSettings['wakeWordBackend'] {
  return value === 'custom_onnx' || value === 'custom_embedding' ? value : 'openwakeword'
}

function normalizeDeviceIndex(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

function stringSetting(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function numberSetting(value: unknown, fallback: number): number {
  return typeof value === 'number' ? value : fallback
}

function normalizeWakeWordPhrase(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : 'hey rex'
}

function normalizeVoiceSettings(settings: Settings): VoiceSettings {
  return {
    backgroundVoiceEnabled: settings.backgroundVoiceEnabled === true,
    microphoneDeviceIndex: normalizeDeviceIndex(settings.microphoneDeviceIndex),
    speakerDeviceIndex: normalizeDeviceIndex(settings.speakerDeviceIndex),
    microphoneDeviceId: stringSetting(settings.microphoneDeviceId),
    speakerDeviceId: stringSetting(settings.speakerDeviceId),
    ttsEngine: normalizeTtsEngine(settings.ttsEngine),
    ttsVoice: stringSetting(settings.ttsVoice),
    speechRate: numberSetting(settings.speechRate, 1.0),
    volume: numberSetting(settings.volume, 1.0),
    sttModel: stringSetting(settings.sttModel, 'base'),
    sttLanguage: stringSetting(settings.sttLanguage, 'auto'),
    sttDevice: normalizeSttDevice(settings.sttDevice),
    wakeWord: stringSetting(settings.wakeWord),
    wakeWordBackend: normalizeWakeWordBackend(settings.wakeWordBackend),
    customWakeWordId: stringSetting(settings.customWakeWordId),
    wakeWordPhrase: normalizeWakeWordPhrase(settings.wakeWordPhrase),
    wakeWordModelPath: stringSetting(settings.wakeWordModelPath),
    wakeWordEmbeddingPath: stringSetting(settings.wakeWordEmbeddingPath)
  }
}

export function useVoiceSettingsController() {
  const addToast = useToast()
  const [form, setForm] = useState<VoiceSettings>({
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
  })
  const [loading, setLoading] = useState(true)
  const [mics, setMics] = useState<MediaDeviceOption[]>([])
  const [speakers, setSpeakers] = useState<MediaDeviceOption[]>([])
  const [runtimeAudioDevices, setRuntimeAudioDevices] = useState<SetupAudioDevice[]>([])
  const [savedField, setSavedField] = useState<keyof VoiceSettings | null>(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<'ok' | 'error' | null>(null)
  const [voices, setVoices] = useState<VoiceInfo[]>([])
  const [voicesLoading, setVoicesLoading] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [wakeWords, setWakeWords] = useState<WakeWordInfo[]>([])
  const [wakeWordStatus, setWakeWordStatus] = useState<WakeWordStatus | null>(null)
  const [previewingWakeWord, setPreviewingWakeWord] = useState(false)
  const [showWwTrainer, setShowWwTrainer] = useState(false)
  const [wwTrainPhrase, setWwTrainPhrase] = useState('')
  const [wwTraining, setWwTraining] = useState(false)
  const [wwTrainStep, setWwTrainStep] = useState<'idle' | 'positive' | 'negative' | 'done'>('idle')
  const [wwPositiveSamples, setWwPositiveSamples] = useState<number[][]>([])
  const [wwNegativeSamples, setWwNegativeSamples] = useState<number[][]>([])
  const [wwTrainMessage, setWwTrainMessage] = useState<string | null>(null)
  const [wwTrainError, setWwTrainError] = useState<string | null>(null)
  const [wwTrainCountdown, setWwTrainCountdown] = useState(0)
  const [activeUserId, setActiveUserId] = useState('default')
  const [enrollments, setEnrollments] = useState<VoiceEnrollment[]>([])
  const [enrollmentCountdown, setEnrollmentCountdown] = useState(0)
  const [capturedSamples, setCapturedSamples] = useState(0)
  const [enrollmentMessage, setEnrollmentMessage] = useState<string | null>(null)
  const [enrollmentError, setEnrollmentError] = useState<string | null>(null)
  const [enrolling, setEnrolling] = useState(false)
  const [deletingUserId, setDeletingUserId] = useState<string | null>(null)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadFileDuration, setUploadFileDuration] = useState<number | null>(null)
  const [uploadVoiceName, setUploadVoiceName] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadResult, setUploadResult] = useState<{ ok: boolean; message: string } | null>(null)
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const testResultTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function engineToProvider(engine: VoiceSettings['ttsEngine']): string {
    if (engine === 'xtts' || engine === 'elevenlabs') return 'xtts'
    if (engine === 'edge-tts' || engine === 'openai') return 'edge-tts'
    return 'pyttsx3'
  }

  function loadVoices(engine: VoiceSettings['ttsEngine']): void {
    setVoicesLoading(true)
    setVoices([])
    window.rex
      .listVoices(engineToProvider(engine))
      .then((res) => {
        setVoices(res.voices ?? [])
      })
      .catch(() => {
        setVoices([])
      })
      .finally(() => setVoicesLoading(false))
  }

  function loadWakeWords(): void {
    window.rex
      .listWakeWords()
      .then((res) => {
        setWakeWords(res.wake_words ?? [])
      })
      .catch(() => setWakeWords([]))
  }

  const builtInWakeWords = wakeWords.filter((w) => w.engine === 'openwakeword')
  const builtInWakeWordOptions = builtInWakeWords.length > 0 ? builtInWakeWords : FALLBACK_BUILTIN_WAKE_WORDS
  const customWakeWords = wakeWords.filter((w) => w.engine === 'custom_embedding')

  function loadEnrollmentState(): void {
    window.rex
      .getVoiceEnrollments()
      .then((result) => {
        if (!result.ok) {
          throw new Error(result.error ?? 'Failed to load enrollments')
        }
        setActiveUserId(result.active_user_id || 'default')
        setEnrollments(result.enrollments ?? [])
      })
      .catch((error: unknown) => {
        const message = error instanceof Error ? error.message : 'Failed to load voice enrollments'
        setEnrollmentError(message)
      })
  }

  useEffect(() => {
    // Load devices
    if (navigator.mediaDevices?.enumerateDevices) {
      navigator.mediaDevices
        .enumerateDevices()
        .then((devices) => {
          const micList = devices
            .filter((d) => d.kind === 'audioinput')
            .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Microphone ${i + 1}` }))
          const speakerList = devices
            .filter((d) => d.kind === 'audiooutput')
            .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Speaker ${i + 1}` }))
          setMics(micList)
          setSpeakers(speakerList)
        })
        .catch(() => {
          /* no devices available */
        })
    }

    window.rex.getSetupAudioDevices()
      .then((result) => {
        setRuntimeAudioDevices(result.ok ? result.devices : [])
      })
      .catch(() => {
        setRuntimeAudioDevices([])
      })

    // Load settings
    window.rex
      .getSettings('voice')
      .then((settings: Settings) => {
        setForm(normalizeVoiceSettings(settings))
      })
      .catch(() => {
        addToast('Failed to load voice settings', 'error')
      })
      .finally(() => setLoading(false))

    loadEnrollmentState()
    loadWakeWords()
  }, [addToast])

  useEffect(() => {
    if (!loading) {
      loadVoices(form.ttsEngine)
    }
  }, [form.ttsEngine, loading]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (loading) return
    window.rex
      .getWakeWordStatus(form)
      .then((status) => {
        setWakeWordStatus(status)
      })
      .catch(() => {
        setWakeWordStatus(null)
      })
  }, [
    loading,
    form.wakeWordBackend,
    form.wakeWord,
    form.customWakeWordId,
    form.wakeWordPhrase,
    form.wakeWordModelPath,
    form.wakeWordEmbeddingPath
  ])

  function showSaved(field: keyof VoiceSettings): void {
    if (savedTimerRef.current) clearTimeout(savedTimerRef.current)
    setSavedField(field)
    savedTimerRef.current = setTimeout(() => setSavedField(null), 2000)
  }

  function saveField(
    field: keyof VoiceSettings,
    value: VoiceSettings[keyof VoiceSettings],
    updatedForm?: VoiceSettings
  ): void {
    const updated: VoiceSettings = { ...(updatedForm ?? form), [field]: value }
    setForm(updated)
    window.rex
      .setSettings('voice', updated as unknown as Settings)
      .then((result) => {
        if (result.ok) {
          showSaved(field)
        } else {
          addToast(result.error ?? 'Failed to save voice settings', 'error')
        }
      })
      .catch(() => {
        addToast('Failed to save voice settings', 'error')
      })
  }

  function handleFieldChange<K extends keyof VoiceSettings>(
    field: K,
    value: VoiceSettings[K]
  ): void {
    const updated = { ...form, [field]: value }
    saveField(field, value, updated)
  }

  function handleTestVoice(): void {
    setTesting(true)
    setTestResult(null)
    window.rex
      .testVoice(form)
      .then((res) => {
        setTestResult(res.ok ? 'ok' : 'error')
      })
      .catch(() => {
        setTestResult('error')
      })
      .finally(() => {
        setTesting(false)
        if (testResultTimerRef.current) clearTimeout(testResultTimerRef.current)
        testResultTimerRef.current = setTimeout(() => setTestResult(null), 3000)
      })
  }

  function handlePreviewVoice(): void {
    if (!form.ttsVoice) return
    setPreviewing(true)
    window.rex
      .previewVoice(engineToProvider(form.ttsEngine), form.ttsVoice)
      .then((res) => {
        if (res.ok && res.audio_base64) {
          const binary = atob(res.audio_base64)
          const bytes = new Uint8Array(binary.length)
          for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i)
          }
          const ctx = new AudioContext()
          ctx.decodeAudioData(bytes.buffer).then((buf) => {
            const src = ctx.createBufferSource()
            src.buffer = buf
            src.connect(ctx.destination)
            src.start()
          }).catch(() => {
            addToast('Could not decode audio preview', 'error')
          })
        } else {
          addToast(res.error ?? 'Preview failed', 'error')
        }
      })
      .catch(() => {
        addToast('Preview failed', 'error')
      })
      .finally(() => setPreviewing(false))
  }

  function playAudioBase64(audioBase64: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const binary = atob(audioBase64)
      const bytes = new Uint8Array(binary.length)
      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i)
      }
      const ctx = new AudioContext()
      ctx.decodeAudioData(bytes.buffer).then((buf) => {
        const src = ctx.createBufferSource()
        src.buffer = buf
        src.connect(ctx.destination)
        src.onended = () => resolve()
        src.start()
      }).catch(reject)
    })
  }

  function handlePreviewWakeWord(): void {
    const selectedBuiltInWakeWord = builtInWakeWordOptions.find((w) => w.id === form.wakeWord)
    const selectedCustomWakeWord = customWakeWords.find((w) => w.id === form.customWakeWordId)
    setPreviewingWakeWord(true)

    if (form.wakeWordBackend === 'custom_embedding' && selectedCustomWakeWord) {
      window.rex
        .previewWakeWordSample(form.customWakeWordId)
        .then((res) => {
          if (res.ok && res.audio_base64) {
            return playAudioBase64(res.audio_base64).catch(() => {
              addToast('Could not play wake word sample', 'error')
            })
          } else {
            addToast(res.error ?? 'No sample recording available', 'error')
            return undefined
          }
        })
        .catch(() => addToast('Preview failed', 'error'))
        .finally(() => setPreviewingWakeWord(false))
    } else {
      const phrase = form.wakeWordBackend === 'custom_onnx'
        ? (form.wakeWordPhrase.trim() || 'hey rex')
        : (selectedBuiltInWakeWord?.name ?? form.wakeWord.replace(/_/g, ' '))
      window.rex
        .previewVoice('pyttsx3', phrase)
        .then((res) => {
          if (res.ok && res.audio_base64) {
            return playAudioBase64(res.audio_base64).catch(() => {
              addToast('Could not play wake word sample', 'error')
            })
          } else {
            addToast(res.error ?? 'Preview failed', 'error')
            return undefined
          }
        })
        .catch(() => addToast('Preview failed', 'error'))
        .finally(() => setPreviewingWakeWord(false))
    }
  }

  async function handleStartWwTraining(): Promise<void> {
    if (!wwTrainPhrase.trim()) {
      setWwTrainError('Enter a wake word phrase first.')
      return
    }
    setWwTraining(true)
    setWwTrainError(null)
    setWwTrainMessage(null)
    setWwPositiveSamples([])
    setWwNegativeSamples([])
    setWwTrainStep('positive')

    let stream: MediaStream | null = null
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: form.microphoneDeviceId ? { deviceId: { exact: form.microphoneDeviceId } } : true
      })

      // Record positive samples
      const positives: number[][] = []
      for (let i = 0; i < WW_POSITIVE_TARGET; i++) {
        setWwTrainMessage(`Say "${wwTrainPhrase.trim()}" — sample ${i + 1} of ${WW_POSITIVE_TARGET}`)
        await runEnrollmentCountdown(setWwTrainCountdown)
        const sample = await captureEnrollmentSample(stream)
        positives.push(sample)
        setWwPositiveSamples([...positives])
        await sleep(300)
      }

      // Record negative samples (background noise / other speech)
      setWwTrainStep('negative')
      const negatives: number[][] = []
      for (let i = 0; i < WW_NEGATIVE_TARGET; i++) {
        setWwTrainMessage(`Stay silent or say something else — sample ${i + 1} of ${WW_NEGATIVE_TARGET}`)
        await runEnrollmentCountdown(setWwTrainCountdown)
        const sample = await captureEnrollmentSample(stream)
        negatives.push(sample)
        setWwNegativeSamples([...negatives])
        await sleep(300)
      }

      setWwTrainMessage('Training… please wait.')
      const result = await window.rex.trainWakeWord(wwTrainPhrase.trim(), positives, negatives)
      if (!result.ok) {
        throw new Error(result.error ?? 'Training failed')
      }

      setWwTrainStep('done')
      setWwTrainMessage(`Wake word "${result.phrase ?? wwTrainPhrase.trim()}" trained successfully!`)
      const trainedPhrase = (result.phrase ?? wwTrainPhrase.trim()).trim()
      const trainedId = trainedPhrase
        .toLowerCase()
        .replace(/[^\w\s-]/g, '')
        .replace(/[\s-]+/g, '_')
      const updatedForm: VoiceSettings = {
        ...form,
        wakeWordBackend: 'custom_embedding',
        customWakeWordId: trainedId,
        wakeWordPhrase: trainedPhrase || 'hey rex',
        wakeWordEmbeddingPath:
          typeof result.model_path === 'string' ? result.model_path : form.wakeWordEmbeddingPath
      }
      setForm(updatedForm)
      try {
        const saveResult = await window.rex.setSettings('voice', updatedForm as unknown as Settings)
        if (!saveResult.ok) {
          addToast(saveResult.error ?? 'Failed to save custom wake word settings', 'error')
        } else {
          addToast('Custom wake word trained', 'success')
        }
      } catch {
        addToast('Failed to save custom wake word settings', 'error')
      }
      loadWakeWords()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setWwTrainError(msg)
      setWwTrainStep('idle')
      addToast(msg, 'error')
    } finally {
      setWwTraining(false)
      setWwTrainCountdown(0)
      stream?.getTracks().forEach((t) => t.stop())
    }
  }

  function handleUploadFileChange(e: React.ChangeEvent<HTMLInputElement>): void {
    const file = e.target.files?.[0] ?? null
    setUploadFile(file)
    setUploadFileDuration(null)
    setUploadResult(null)
    if (file) {
      setUploadVoiceName(file.name.replace(/\.[^.]+$/, '').replace(/[_-]/g, ' '))
      const audio = new Audio(URL.createObjectURL(file))
      audio.addEventListener('loadedmetadata', () => {
        setUploadFileDuration(audio.duration)
      })
      audio.addEventListener('error', () => {
        setUploadFileDuration(0)
      })
    } else {
      setUploadVoiceName('')
    }
  }

  async function handleUploadCustomVoice(): Promise<void> {
    if (!uploadFile || !uploadVoiceName.trim()) return
    setUploading(true)
    setUploadResult(null)
    try {
      // Write file to a temp path via the file system API is unavailable in the
      // renderer; instead we use a blob URL and pass the file path from the
      // webkitRelativePath or name. For Electron, we read the file as an
      // ArrayBuffer and write to a temp path via the main process.
      // Simpler approach: use the native file path exposed by Electron.
      const nativePath: string = (uploadFile as File & { path?: string }).path ?? ''
      if (!nativePath) {
        setUploadResult({ ok: false, message: 'Cannot read file path. Try again.' })
        setUploading(false)
        return
      }
      const res = await window.rex.uploadCustomVoice(nativePath, uploadVoiceName.trim())
      if (res.ok) {
        setUploadResult({ ok: true, message: `Voice "${res.voice_name}" saved successfully.` })
        setUploadFile(null)
        setUploadVoiceName('')
        setUploadFileDuration(null)
        // Refresh voice list so the new voice appears in the dropdown.
        if (form.ttsEngine === 'xtts') {
          loadVoices('xtts')
        }
      } else {
        setUploadResult({ ok: false, message: res.error ?? 'Upload failed.' })
      }
    } catch (err) {
      setUploadResult({ ok: false, message: String(err) })
    } finally {
      setUploading(false)
    }
  }

  async function handleStartEnrollment(): Promise<void> {
    setEnrolling(true)
    setEnrollmentCountdown(0)
    setCapturedSamples(0)
    setEnrollmentMessage(null)
    setEnrollmentError(null)

    let stream: MediaStream | null = null

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: form.microphoneDeviceId
          ? { deviceId: { exact: form.microphoneDeviceId } }
          : true
      })

      const samples: number[][] = []
      for (let index = 0; index < ENROLLMENT_SAMPLE_TARGET; index += 1) {
        await runEnrollmentCountdown(setEnrollmentCountdown)
        const sample = await captureEnrollmentSample(stream)
        samples.push(sample)
        setCapturedSamples(index + 1)
        setEnrollmentMessage(`Captured sample ${index + 1} of ${ENROLLMENT_SAMPLE_TARGET}.`)
        await sleep(250)
      }

      const result = await window.rex.enrollVoice(activeUserId, samples)
      if (!result.ok) {
        throw new Error(result.error ?? 'Voice enrollment failed')
      }

      setEnrollmentMessage(`Voice enrollment saved for ${activeUserId}.`)
      addToast('Voice enrollment saved', 'success')
      loadEnrollmentState()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Voice enrollment failed'
      setEnrollmentError(message)
      addToast(message, 'error')
    } finally {
      setEnrollmentCountdown(0)
      setEnrolling(false)
      stream?.getTracks().forEach((track) => track.stop())
    }
  }

  function handleDeleteEnrollment(userId: string): void {
    setDeletingUserId(userId)
    setEnrollmentError(null)
    setEnrollmentMessage(null)
    window.rex
      .deleteVoiceEnrollment(userId)
      .then((result) => {
        if (!result.ok) {
          throw new Error(result.error ?? 'Failed to delete voice enrollment')
        }
        setEnrollmentMessage(`Deleted enrollment for ${userId}.`)
        addToast('Voice enrollment deleted', 'success')
        loadEnrollmentState()
      })
      .catch((error: unknown) => {
        const message =
          error instanceof Error ? error.message : 'Failed to delete voice enrollment'
        setEnrollmentError(message)
        addToast(message, 'error')
      })
      .finally(() => {
        setDeletingUserId(null)
      })
  }

  return {
    form,
    setForm,
    loading,
    mics,
    speakers,
    runtimeAudioDevices,
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
  }
}

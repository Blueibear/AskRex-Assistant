import React, { useEffect, useRef, useState } from 'react'
import type {
  Settings,
  AiSettings,
  ModelDiscoveryProvider,
  PreferenceSuggestion
} from '../../types/ipc'
import { PageLoadingFallback } from '../../components/ui/PageLoadingFallback'
import { useToast } from '../../components/ui/Toast'
import { PasswordInput, SavedIndicator } from './shared'
import { ModelDiscoveryRequestGate } from '../../types/modelDiscoveryRequestGate'
import { ModelDiscoveryResults, type ModelDiscoveryStatus } from './ModelDiscoveryResults'

const MODEL_ROUTING_FIELDS: Array<{
  key: keyof AiSettings['modelRouting']
  label: string
  placeholder: string
}> = [
  { key: 'default', label: 'Default', placeholder: 'gpt-4o' },
  { key: 'coding', label: 'Coding', placeholder: 'claude-sonnet-4' },
  { key: 'reasoning', label: 'Reasoning', placeholder: 'o3-mini' },
  { key: 'search', label: 'Search', placeholder: 'gpt-4o-mini' },
  { key: 'vision', label: 'Vision', placeholder: 'gpt-4o' },
  { key: 'fast', label: 'Fast', placeholder: 'llama3.2' }
]

type SavedField = keyof AiSettings | 'modelRouting'

const PERSONALITIES = [
  {
    name: 'Friendly',
    toneKeywords: ['warm', 'conversational', 'upbeat', 'encouraging'],
    greeting: 'Hey there! How can I help you today?'
  },
  {
    name: 'Professional',
    toneKeywords: ['precise', 'formal', 'concise', 'business-like'],
    greeting: 'Hello. How may I assist you?'
  },
  {
    name: 'Minimal',
    toneKeywords: ['brief', 'terse', 'direct'],
    greeting: 'Ready.'
  }
] as const

export function AiSettingsSection(): React.ReactElement {
  const addToast = useToast()
  const [form, setForm] = useState<AiSettings>({
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
    modelRouting: {
      default: '',
      coding: '',
      reasoning: '',
      search: '',
      vision: '',
      fast: ''
    },
    personality: 'Friendly'
  })
  const [loading, setLoading] = useState(true)
  const [savedField, setSavedField] = useState<SavedField | null>(null)
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [suggestions, setSuggestions] = useState<PreferenceSuggestion[]>([])
  const [dismissedFields, setDismissedFields] = useState<Set<string>>(new Set())
  const [routingDirty, setRoutingDirty] = useState(false)
  const [savingRouting, setSavingRouting] = useState(false)
  const [openaiKeySet, setOpenaiKeySet] = useState(false)
  const [openrouterKeySet, setOpenrouterKeySet] = useState(false)
  const [openaiKeyValue, setOpenaiKeyValue] = useState('')
  const [openrouterKeyValue, setOpenrouterKeyValue] = useState('')
  const [credentialSaving, setCredentialSaving] = useState<'openai' | 'openrouter' | null>(null)
  const [discoveryProvider, setDiscoveryProvider] = useState<ModelDiscoveryProvider | null>(null)
  const [discoveryStatus, setDiscoveryStatus] = useState<ModelDiscoveryStatus>('idle')
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([])
  const [discoveryError, setDiscoveryError] = useState('')
  const discoveryRequestGateRef = useRef(new ModelDiscoveryRequestGate())

  function loadSuggestions(): void {
    window.rex
      .getPreferenceSuggestions()
      .then((s) => setSuggestions(s))
      .catch(() => {
        // Non-fatal — suggestions are best-effort
      })
  }

  useEffect(() => {
    window.rex
      .getSettings('ai')
      .then((settings: Settings) => {
        const modelRouting =
          settings.modelRouting && typeof settings.modelRouting === 'object'
            ? (settings.modelRouting as Record<string, unknown>)
            : {}
        const rawProvider = settings.provider
        const provider: AiSettings['provider'] =
          rawProvider === 'openai' || rawProvider === 'openrouter' || rawProvider === 'ollama' || rawProvider === 'local'
            ? rawProvider
            : 'openai'
        setForm({
          model: typeof settings.model === 'string' && settings.model.trim() ? settings.model : 'gpt-4o',
          provider,
          customModelId: typeof settings.customModelId === 'string' ? settings.customModelId : '',
          openaiBaseUrl: typeof settings.openaiBaseUrl === 'string' ? settings.openaiBaseUrl : '',
          ollamaBaseUrl: typeof settings.ollamaBaseUrl === 'string' ? settings.ollamaBaseUrl : 'http://localhost:11434',
          openrouterModel:
            typeof settings.openrouterModel === 'string' && settings.openrouterModel.trim()
              ? settings.openrouterModel
              : 'openai/gpt-4o',
          openrouterBaseUrl:
            typeof settings.openrouterBaseUrl === 'string' && settings.openrouterBaseUrl.trim()
              ? settings.openrouterBaseUrl
              : 'https://openrouter.ai/api/v1',
          temperature: typeof settings.temperature === 'number' ? settings.temperature : 0.7,
          maxTokens: typeof settings.maxTokens === 'number' ? settings.maxTokens : 2048,
          systemPrompt: typeof settings.systemPrompt === 'string' ? settings.systemPrompt : '',
          autonomyMode:
            settings.autonomyMode === 'supervised' || settings.autonomyMode === 'full-auto'
              ? settings.autonomyMode
              : 'manual',
          budgetPerPlan: typeof settings.budgetPerPlan === 'number' ? settings.budgetPerPlan : 0,
          budgetPerStep: typeof settings.budgetPerStep === 'number' ? settings.budgetPerStep : 0,
          modelRouting: {
            default: typeof modelRouting.default === 'string' ? modelRouting.default : '',
            coding: typeof modelRouting.coding === 'string' ? modelRouting.coding : '',
            reasoning: typeof modelRouting.reasoning === 'string' ? modelRouting.reasoning : '',
            search: typeof modelRouting.search === 'string' ? modelRouting.search : '',
            vision: typeof modelRouting.vision === 'string' ? modelRouting.vision : '',
            fast: typeof modelRouting.fast === 'string' ? modelRouting.fast : ''
          },
          personality: typeof settings.personality === 'string' && PERSONALITIES.some((p) => p.name === settings.personality)
            ? settings.personality
            : 'Friendly'
        })
        setRoutingDirty(false)
      })
      .catch(() => {
        addToast('Failed to load AI settings', 'error')
      })
      .finally(() => setLoading(false))

    window.rex
      .getApiKeys()
      .then((keys) => {
        setOpenaiKeySet(keys.openai_key_set)
        setOpenrouterKeySet(keys.openrouter_key_set)
        if (keys.error) addToast(keys.error, 'error')
      })
      .catch(() => {
        // Non-fatal — API key status will show as unset
      })

    loadSuggestions()
  }, [addToast])

  function showSaved(field: SavedField): void {
    if (savedTimerRef.current) clearTimeout(savedTimerRef.current)
    setSavedField(field)
    savedTimerRef.current = setTimeout(() => setSavedField(null), 2000)
  }

  function resetModelDiscovery(): void {
    discoveryRequestGateRef.current.invalidate()
    setDiscoveryProvider(null)
    setDiscoveryStatus('idle')
    setDiscoveredModels([])
    setDiscoveryError('')
  }

  function handleDiscoverModels(provider: ModelDiscoveryProvider): void {
    const requestId = discoveryRequestGateRef.current.begin()
    setDiscoveryProvider(provider)
    setDiscoveryStatus('loading')
    setDiscoveredModels([])
    setDiscoveryError('')

    // Persist only the endpoint field so a discovery attempt (success or
    // failure) can never overwrite the user's existing persisted model
    // choice with the form's untouched default (buildAiSettings falls back
    // to the currently-persisted model for any field omitted here).
    const endpointSettings: Settings =
      provider === 'ollama'
        ? { ollamaBaseUrl: form.ollamaBaseUrl }
        : { openaiBaseUrl: form.openaiBaseUrl }

    window.rex
      .setSettings('ai', endpointSettings)
      .then((saved) => {
        if (!saved.ok) {
          throw new Error(saved.error ?? 'Failed to save the discovery endpoint before model discovery')
        }
        if (!discoveryRequestGateRef.current.isCurrent(requestId)) return null
        return window.rex.discoverAiModels(provider)
      })
      .then((result) => {
        if (!result || !discoveryRequestGateRef.current.isCurrent(requestId)) return
        if (result.ok) {
          setDiscoveredModels(result.models)
          setDiscoveryStatus('success')
          return
        }
        setDiscoveryError(result.error ?? 'Model discovery failed')
        setDiscoveryStatus('error')
      })
      .catch(() => {
        if (!discoveryRequestGateRef.current.isCurrent(requestId)) return
        setDiscoveryError('Model discovery could not be completed')
        setDiscoveryStatus('error')
      })
  }

  function handleFieldChange<K extends keyof AiSettings>(field: K, value: AiSettings[K]): void {
    const updated = { ...form, [field]: value }
    if (field === 'provider' || field === 'openaiBaseUrl' || field === 'ollamaBaseUrl') {
      resetModelDiscovery()
    }
    setForm(updated)
    window.rex
      .setSettings('ai', updated as unknown as Settings)
      .then((result) => {
        if (result.ok) {
          showSaved(field)
        } else {
          addToast(result.error ?? 'Failed to save AI settings', 'error')
        }
      })
      .catch(() => {
        addToast('Failed to save AI settings', 'error')
      })
  }

  function handleRoutingChange(
    field: keyof AiSettings['modelRouting'],
    value: string
  ): void {
    setForm((current) => ({
      ...current,
      modelRouting: {
        ...current.modelRouting,
        [field]: value
      }
    }))
    setRoutingDirty(true)
  }

  function handleSaveRouting(): void {
    const updated = {
      ...form,
      modelRouting: { ...form.modelRouting }
    }
    setSavingRouting(true)
    window.rex
      .setSettings('ai', updated as unknown as Settings)
      .then((result) => {
        if (result.ok) {
          setForm(updated)
          setRoutingDirty(false)
          showSaved('modelRouting')
        } else {
          addToast(result.error ?? 'Failed to save model routing', 'error')
        }
      })
      .catch(() => {
        addToast('Failed to save model routing', 'error')
      })
      .finally(() => setSavingRouting(false))
  }

  function handleApplySuggestion(suggestion: PreferenceSuggestion): void {
    window.rex
      .applyPreferenceSuggestion(suggestion.field, suggestion.suggested_value)
      .then(() => {
        setForm((f) => ({ ...f, [suggestion.field]: suggestion.suggested_value }))
        setDismissedFields((prev) => new Set(prev).add(suggestion.field))
        loadSuggestions()
      })
      .catch(() => {
        addToast('Failed to apply suggestion', 'error')
      })
  }

  function handleDismissSuggestion(field: string): void {
    setDismissedFields((prev) => new Set(prev).add(field))
  }

  function handleSaveApiKey(provider: 'openai' | 'openrouter'): void {
    const value = provider === 'openai' ? openaiKeyValue.trim() : openrouterKeyValue.trim()
    if (!value) return
    setCredentialSaving(provider)
    const logicalName = provider === 'openai' ? 'OPENAI_API_KEY' : 'OPENROUTER_API_KEY'
    window.rex
      .setApiKey(logicalName, value)
      .then((result) => {
        if (result.ok) {
          if (provider === 'openai') {
            setOpenaiKeySet(true)
            setOpenaiKeyValue('')
          } else {
            setOpenrouterKeySet(true)
            setOpenrouterKeyValue('')
          }
          addToast(`${provider === 'openai' ? 'OpenAI' : 'OpenRouter'} API key saved`, 'success')
        } else {
          addToast(result.error ?? 'Failed to save API key', 'error')
        }
      })
      .catch(() => {
        addToast('Failed to save API key', 'error')
      })
      .finally(() => setCredentialSaving(null))
  }

  const activeSuggestion = suggestions.find((s) => !dismissedFields.has(s.field)) ?? null

  if (loading) {
    return <PageLoadingFallback lines={5} />
  }

  return (
    <div className="p-6 max-w-lg">
      <h2 className="text-lg font-semibold text-text-primary mb-6">AI</h2>

      {/* Personality */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-2">
          <label className="text-sm font-medium text-text-primary">Personality</label>
          <SavedIndicator visible={savedField === 'personality'} />
        </div>
        <div className="flex flex-col gap-2">
          {PERSONALITIES.map((p) => (
            <button
              key={p.name}
              type="button"
              onClick={() => handleFieldChange('personality', p.name)}
              className={[
                'text-left rounded-lg border px-4 py-3 transition-colors focus:outline-none focus:ring-2 focus:ring-accent',
                form.personality === p.name
                  ? 'border-accent bg-accent/10'
                  : 'border-border bg-surface-raised hover:border-accent/50'
              ].join(' ')}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-text-primary">{p.name}</span>
                {form.personality === p.name && (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" className="text-accent shrink-0">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                )}
              </div>
              <p className="text-xs text-text-secondary italic mb-1">"{p.greeting}"</p>
              <p className="text-xs text-text-secondary">{p.toneKeywords.join(' · ')}</p>
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-xs text-text-secondary">Takes effect on your next message — no restart needed.</p>
      </div>

      {/* LLM Provider */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="llmProvider" className="text-sm font-medium text-text-primary">
            LLM Provider
          </label>
          <SavedIndicator visible={savedField === 'provider'} />
        </div>
        <select
          id="llmProvider"
          value={form.provider}
          onChange={(e) => handleFieldChange('provider', e.target.value as AiSettings['provider'])}
          className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
        >
          <option value="openai">OpenAI</option>
          <option value="openrouter">OpenRouter</option>
          <option value="ollama">Ollama (local)</option>
          <option value="local">Local Transformers</option>
        </select>
      </div>

      {/* OpenAI: model ID + API key */}
      {form.provider === 'openai' && (
        <>
          <div className="mb-5">
            <div className="flex items-center justify-between mb-1.5">
              <label htmlFor="aiModel" className="text-sm font-medium text-text-primary">
                OpenAI Model ID
              </label>
              <SavedIndicator visible={savedField === 'model'} />
            </div>
            <input
              id="aiModel"
              type="text"
              value={form.model}
              placeholder="gpt-4o"
              onChange={(e) => setForm((current) => ({ ...current, model: e.target.value }))}
              onBlur={(e) => handleFieldChange('model', e.target.value.trim() || 'gpt-4o')}
              className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent"
            />
          </div>

          <div className="mb-5">
            <div className="flex items-center justify-between mb-1.5">
              <label htmlFor="openaiBaseUrl" className="text-sm font-medium text-text-primary">
                OpenAI-compatible Base URL (LM Studio)
              </label>
              <SavedIndicator visible={savedField === 'openaiBaseUrl'} />
            </div>
            <input
              id="openaiBaseUrl"
              type="text"
              value={form.openaiBaseUrl}
              placeholder="Leave blank for official OpenAI"
              onChange={(e) => {
                resetModelDiscovery()
                setForm((current) => ({ ...current, openaiBaseUrl: e.target.value }))
              }}
              onBlur={(e) => handleFieldChange('openaiBaseUrl', e.target.value)}
              className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent"
            />
            <p className="mt-1 text-xs text-text-secondary">
              Leave blank for official OpenAI. For LM Studio, use its OpenAI-compatible endpoint such as http://127.0.0.1:1234/v1.
            </p>
            {form.openaiBaseUrl.trim() && (
              <button
                type="button"
                onClick={() => handleDiscoverModels('lmstudio')}
                disabled={discoveryProvider === 'lmstudio' && discoveryStatus === 'loading'}
                className="mt-2 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm font-medium text-text-primary transition-colors hover:border-accent/60 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Discover LM Studio Models
              </button>
            )}
            <ModelDiscoveryResults
              providerLabel="LM Studio"
              status={discoveryProvider === 'lmstudio' ? discoveryStatus : 'idle'}
              models={discoveryProvider === 'lmstudio' ? discoveredModels : []}
              selectedModel={form.model}
              error={discoveryProvider === 'lmstudio' ? discoveryError : ''}
              onSelect={(model) => handleFieldChange('model', model)}
            />
          </div>

          <div className="mb-5">
            <div className="flex items-center justify-between mb-1.5">
              <label htmlFor="openaiApiKey" className="text-sm font-medium text-text-primary">
                OpenAI API Key
              </label>
              {openaiKeySet && <span className="text-xs text-success font-medium">Key set</span>}
            </div>
            <div className="flex gap-2">
              <div className="flex-1">
                <PasswordInput
                  id="openaiApiKey"
                  value={openaiKeyValue}
                  placeholder={openaiKeySet ? 'Stored securely' : 'Enter API key'}
                  onChange={setOpenaiKeyValue}
                />
              </div>
              <button
                type="button"
                onClick={() => handleSaveApiKey('openai')}
                disabled={credentialSaving !== null || !openaiKeyValue.trim()}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50 shrink-0"
              >
                {credentialSaving === 'openai' ? 'Saving...' : 'Save'}
              </button>
            </div>
            <p className="mt-1 text-xs text-text-secondary">
              Stored in the Windows credential vault. The saved key is never loaded back into this field.
            </p>
          </div>
        </>
      )}

      {/* OpenRouter: OpenAI-compatible endpoint, model slug, and separate key */}
      {form.provider === 'openrouter' && (
        <>
          <div className="mb-5">
            <div className="flex items-center justify-between mb-1.5">
              <label htmlFor="openrouterModel" className="text-sm font-medium text-text-primary">
                OpenRouter Model Slug
              </label>
              <SavedIndicator visible={savedField === 'openrouterModel'} />
            </div>
            <input
              id="openrouterModel"
              type="text"
              value={form.openrouterModel}
              placeholder="openai/gpt-4o"
              onChange={(e) => setForm((current) => ({ ...current, openrouterModel: e.target.value }))}
              onBlur={(e) => handleFieldChange('openrouterModel', e.target.value.trim())}
              className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent"
            />
            <p className="mt-1 text-xs text-text-secondary">
              Use the complete OpenRouter model identifier, including its provider prefix.
            </p>
          </div>
          <div className="mb-5">
            <div className="flex items-center justify-between mb-1.5">
              <label htmlFor="openrouterBaseUrl" className="text-sm font-medium text-text-primary">
                OpenRouter Base URL
              </label>
              <SavedIndicator visible={savedField === 'openrouterBaseUrl'} />
            </div>
            <input
              id="openrouterBaseUrl"
              type="url"
              value="https://openrouter.ai/api/v1"
              readOnly
              aria-readonly="true"
              className="w-full cursor-not-allowed bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-secondary"
            />
            <p className="mt-1 text-xs text-text-secondary">
              Locked to OpenRouter's official HTTPS endpoint so the saved key cannot be redirected.
            </p>
          </div>
          <div className="mb-5">
            <div className="flex items-center justify-between mb-1.5">
              <label htmlFor="openrouterApiKey" className="text-sm font-medium text-text-primary">
                OpenRouter API Key
              </label>
              {openrouterKeySet && <span className="text-xs text-success font-medium">Key set</span>}
            </div>
            <div className="flex gap-2">
              <div className="flex-1">
                <PasswordInput
                  id="openrouterApiKey"
                  value={openrouterKeyValue}
                  placeholder={openrouterKeySet ? 'Stored securely' : 'Enter OpenRouter key'}
                  onChange={setOpenrouterKeyValue}
                />
              </div>
              <button
                type="button"
                onClick={() => handleSaveApiKey('openrouter')}
                disabled={credentialSaving !== null || !openrouterKeyValue.trim()}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50 shrink-0"
              >
                {credentialSaving === 'openrouter' ? 'Saving...' : 'Save'}
              </button>
            </div>
            <p className="mt-1 text-xs text-text-secondary">
              Stored separately from the OpenAI key in the Windows credential vault.
            </p>
          </div>
        </>
      )}

      {/* Ollama: base URL + model name */}
      {form.provider === 'ollama' && (
        <>
          <div className="mb-5">
            <div className="flex items-center justify-between mb-1.5">
              <label htmlFor="ollamaBaseUrl" className="text-sm font-medium text-text-primary">
                Ollama Base URL
              </label>
              <SavedIndicator visible={savedField === 'ollamaBaseUrl'} />
            </div>
            <input
              id="ollamaBaseUrl"
              type="text"
              value={form.ollamaBaseUrl}
              placeholder="http://localhost:11434"
              onChange={(e) => {
                resetModelDiscovery()
                setForm((f) => ({ ...f, ollamaBaseUrl: e.target.value }))
              }}
              onBlur={(e) => handleFieldChange('ollamaBaseUrl', e.target.value)}
              className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent"
            />
            <button
              type="button"
              onClick={() => handleDiscoverModels('ollama')}
              disabled={discoveryProvider === 'ollama' && discoveryStatus === 'loading'}
              className="mt-2 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm font-medium text-text-primary transition-colors hover:border-accent/60 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Discover Models
            </button>
            <ModelDiscoveryResults
              providerLabel="Ollama"
              status={discoveryProvider === 'ollama' ? discoveryStatus : 'idle'}
              models={discoveryProvider === 'ollama' ? discoveredModels : []}
              selectedModel={form.customModelId}
              error={discoveryProvider === 'ollama' ? discoveryError : ''}
              onSelect={(model) => handleFieldChange('customModelId', model)}
            />
          </div>
          <div className="mb-5">
            <div className="flex items-center justify-between mb-1.5">
              <label htmlFor="customModelId" className="text-sm font-medium text-text-primary">
                Model Name / Tag
              </label>
              <SavedIndicator visible={savedField === 'customModelId'} />
            </div>
            <input
              id="customModelId"
              type="text"
              value={form.customModelId}
              placeholder="e.g. llama3:8b"
              onChange={(e) => setForm((f) => ({ ...f, customModelId: e.target.value }))}
              onBlur={(e) => handleFieldChange('customModelId', e.target.value)}
              className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent"
            />
          </div>
        </>
      )}

      {/* Local Transformers: model path */}
      {form.provider === 'local' && (
        <div className="mb-5">
          <div className="flex items-center justify-between mb-1.5">
            <label htmlFor="customModelId" className="text-sm font-medium text-text-primary">
              Model Name or Path
            </label>
            <SavedIndicator visible={savedField === 'customModelId'} />
          </div>
          <input
            id="customModelId"
            type="text"
            value={form.customModelId}
            placeholder="e.g. mistralai/Mistral-7B-Instruct-v0.3"
            onChange={(e) => setForm((f) => ({ ...f, customModelId: e.target.value }))}
            onBlur={(e) => handleFieldChange('customModelId', e.target.value)}
            className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent"
          />
        </div>
      )}

      <div className="mb-6 rounded-xl border border-border bg-surface-raised/40 p-4">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">Model Routing</h3>
            <p className="mt-1 text-xs text-text-secondary">
              Override the model used for each task category. Leave a field blank to fall back to Rex’s default routing.
            </p>
          </div>
          <SavedIndicator visible={savedField === 'modelRouting'} />
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {MODEL_ROUTING_FIELDS.map((field) => (
            <div key={field.key}>
              <label
                htmlFor={`model-routing-${field.key}`}
                className="mb-1.5 block text-sm font-medium text-text-primary"
              >
                {field.label}
              </label>
              <input
                id={`model-routing-${field.key}`}
                type="text"
                value={form.modelRouting[field.key]}
                placeholder={field.placeholder}
                onChange={(e) => handleRoutingChange(field.key, e.target.value)}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent"
              />
            </div>
          ))}
        </div>
        <div className="mt-4 flex items-center justify-between gap-3">
          <p className="text-xs text-text-secondary">
            Supported values include OpenAI model IDs, Claude model names, or local model identifiers such as Ollama tags.
          </p>
          <button
            type="button"
            onClick={handleSaveRouting}
            disabled={!routingDirty || savingRouting}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {savingRouting ? 'Saving…' : 'Save Routing'}
          </button>
        </div>
      </div>

      {/* Temperature */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="temperature" className="text-sm font-medium text-text-primary">
            Temperature
            <span className="ml-2 text-xs text-text-secondary font-normal">
              {form.temperature.toFixed(2)}
            </span>
          </label>
          <SavedIndicator visible={savedField === 'temperature'} />
        </div>
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          <span>Precise</span>
          <input
            id="temperature"
            type="range"
            min={0}
            max={1.0}
            step={0.01}
            value={form.temperature}
            onChange={(e) => handleFieldChange('temperature', parseFloat(e.target.value))}
            className="flex-1 accent-accent"
          />
          <span>Creative</span>
        </div>
      </div>

      {/* Max tokens */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="maxTokens" className="text-sm font-medium text-text-primary">
            Max Tokens
          </label>
          <SavedIndicator visible={savedField === 'maxTokens'} />
        </div>
        <input
          id="maxTokens"
          type="number"
          min={1}
          max={128000}
          step={256}
          value={form.maxTokens}
          onChange={(e) => {
            const val = parseInt(e.target.value, 10)
            if (!isNaN(val) && val > 0) handleFieldChange('maxTokens', val)
          }}
          className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
        />
      </div>

      {/* System prompt override */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="systemPrompt" className="text-sm font-medium text-text-primary">
            System Prompt Override
          </label>
          <SavedIndicator visible={savedField === 'systemPrompt'} />
        </div>
        <textarea
          id="systemPrompt"
          rows={4}
          value={form.systemPrompt}
          placeholder="Leave blank to use the default system prompt"
          onChange={(e) => setForm((f) => ({ ...f, systemPrompt: e.target.value }))}
          onBlur={(e) => handleFieldChange('systemPrompt', e.target.value)}
          className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent resize-none"
        />
      </div>

      {/* Autonomy mode */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="autonomyMode" className="text-sm font-medium text-text-primary">
            Autonomy Mode
          </label>
          <SavedIndicator visible={savedField === 'autonomyMode'} />
        </div>
        <select
          id="autonomyMode"
          value={form.autonomyMode}
          onChange={(e) =>
            handleFieldChange('autonomyMode', e.target.value as AiSettings['autonomyMode'])
          }
          className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
        >
          <option value="manual">Manual — confirm every action</option>
          <option value="supervised">Supervised — confirm risky actions</option>
          <option value="full-auto">Full Auto — act without confirmation</option>
        </select>
      </div>

      {/* Full-auto warning */}
      {form.autonomyMode === 'full-auto' && (
        <div className="flex items-start gap-2.5 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-warning">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="shrink-0 mt-0.5"
          >
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          Rex will act without confirmation. Review task history regularly.
        </div>
      )}

      {/* Budget per plan */}
      <div className="mb-5 mt-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="budgetPerPlan" className="text-sm font-medium text-text-primary">
            Budget per Plan (USD)
          </label>
          <SavedIndicator visible={savedField === 'budgetPerPlan'} />
        </div>
        <input
          id="budgetPerPlan"
          type="number"
          min="0"
          step="0.01"
          value={form.budgetPerPlan}
          onChange={(e) => handleFieldChange('budgetPerPlan', parseFloat(e.target.value) || 0)}
          className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
        />
        <p className="mt-1 text-xs text-text-secondary">Maximum estimated cost per plan run in USD. Set to 0 for unlimited.</p>
      </div>

      {/* Budget per step */}
      <div className="mb-5">
        <div className="flex items-center justify-between mb-1.5">
          <label htmlFor="budgetPerStep" className="text-sm font-medium text-text-primary">
            Budget per Step (USD)
          </label>
          <SavedIndicator visible={savedField === 'budgetPerStep'} />
        </div>
        <input
          id="budgetPerStep"
          type="number"
          min="0"
          step="0.001"
          value={form.budgetPerStep}
          onChange={(e) => handleFieldChange('budgetPerStep', parseFloat(e.target.value) || 0)}
          className="w-full bg-surface-raised border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent"
        />
        <p className="mt-1 text-xs text-text-secondary">Maximum estimated cost per individual step in USD. Steps over this limit are skipped. Set to 0 for unlimited.</p>
      </div>

      {/* Preference suggestion banner */}
      {activeSuggestion !== null && (
        <div className="flex items-start gap-3 rounded-lg border border-accent/40 bg-accent/10 px-4 py-3 text-sm text-accent">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="shrink-0 mt-0.5"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <div className="flex-1">
            <p>Based on your usage: {activeSuggestion.reason}.</p>
            <div className="flex items-center gap-2 mt-2">
              <button
                onClick={() => handleApplySuggestion(activeSuggestion)}
                className="text-xs font-medium bg-accent text-white px-3 py-1 rounded-md hover:bg-accent/90 transition-colors focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-bg"
              >
                Apply
              </button>
              <button
                onClick={() => handleDismissSuggestion(activeSuggestion.field)}
                className="text-xs font-medium text-accent hover:text-accent/80 transition-colors focus:outline-none"
              >
                Dismiss
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

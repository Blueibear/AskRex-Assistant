import { readFileSync } from 'fs'
import { fileURLToPath } from 'url'
import { describe, it, expect } from 'vitest'
import { buildSetupSubmission } from '../src/pages/setupWizardModel'

describe('setupWizardModel', () => {
  describe('buildSetupSubmission', () => {
    it('returns basic submission without options', () => {
      const result = buildSetupSubmission({
        username: 'alice',
        password: 'pass1234', // pragma: allowlist secret
        llmProvider: 'local',
        llmApiKey: '',
        ttsProvider: 'none',
        haBaseUrl: 'http://ha.local:8123',
        haToken: 'secret-token'
      })

      expect(result.username).toBe('alice')
      expect(result.password).toBe('pass1234')
      expect(result.llm_provider).toBe('local')
      expect(result.tts_provider).toBe('none')
      expect(result.ha_base_url).toBe('http://ha.local:8123')
      expect(result.ha_token).toBe('secret-token')
    })

    it('returns empty HA fields when deferHomeAssistant is true', () => {
      const result = buildSetupSubmission(
        {
          username: 'bob',
          password: 'pass1234', // pragma: allowlist secret
          llmProvider: 'openai',
          llmApiKey: 'sk-test', // pragma: allowlist secret
          ttsProvider: 'edge',
          haBaseUrl: 'http://ha.local:8123',
          haToken: 'secret-token'
        },
        { deferHomeAssistant: true }
      )

      expect(result.username).toBe('bob')
      expect(result.ha_base_url).toBe('')
      expect(result.ha_token).toBe('')
      expect(result.defer_home_assistant).toBe(true)
    })

    it('includes llm_api_key when provided', () => {
      const result = buildSetupSubmission({
        username: 'carol',
        password: 'pass1234', // pragma: allowlist secret
        llmProvider: 'openai',
        llmApiKey: 'sk-test-key', // pragma: allowlist secret
        ttsProvider: 'none',
        haBaseUrl: '',
        haToken: ''
      })

      expect(result.llm_api_key).toBe('sk-test-key')
    })

    it('does not include llm_api_key when empty', () => {
      const result = buildSetupSubmission({
        username: 'dave',
        password: 'pass1234', // pragma: allowlist secret
        llmProvider: 'local',
        llmApiKey: '',
        ttsProvider: 'none',
        haBaseUrl: '',
        haToken: ''
      })

      expect(result.llm_api_key).toBeUndefined()
    })

    it('includes openai_base_url when LM Studio is selected', () => {
      const result = buildSetupSubmission({
        username: 'frank',
        password: 'pass1234', // pragma: allowlist secret
        llmProvider: 'lmstudio',
        llmApiKey: '',
        openaiBaseUrl: 'http://127.0.0.1:1234/v1',
        ttsProvider: 'none',
        haBaseUrl: '',
        haToken: ''
      })

      expect(result.llm_provider).toBe('lmstudio')
      expect(result.openai_base_url).toBe('http://127.0.0.1:1234/v1')
    })

    it('does not include openai_base_url for other providers', () => {
      const result = buildSetupSubmission({
        username: 'grace',
        password: 'pass1234', // pragma: allowlist secret
        llmProvider: 'openai',
        llmApiKey: '',
        openaiBaseUrl: 'http://127.0.0.1:1234/v1',
        ttsProvider: 'none',
        haBaseUrl: '',
        haToken: ''
      })

      expect(result.openai_base_url).toBeUndefined()
    })

    it('respects deferHomeAssistant=false explicitly', () => {
      const result = buildSetupSubmission(
        {
          username: 'eve',
          password: 'pass1234', // pragma: allowlist secret
          llmProvider: 'local',
          llmApiKey: '',
          ttsProvider: 'none',
          haBaseUrl: 'http://ha.local:8123',
          haToken: 'token'
        },
        { deferHomeAssistant: false }
      )

      expect(result.ha_base_url).toBe('http://ha.local:8123')
      expect(result.ha_token).toBe('token')
      expect(result.defer_home_assistant).toBe(false)
    })

    it('wires Do this later directly to a deferred submission', () => {
      const pagePath = fileURLToPath(new URL('../src/pages/SetupWizardPage.tsx', import.meta.url))
      const pageSource = readFileSync(pagePath, 'utf8')

      expect(pageSource).toContain('Do this later')
      expect(pageSource).toContain('void handleSubmit(true)')
      expect(pageSource).not.toContain('setDeferHomeAssistant')
    })
  })

  describe('LM Studio first-run provider choice', () => {
    const pagePath = fileURLToPath(new URL('../src/pages/SetupWizardPage.tsx', import.meta.url))
    const pageSource = readFileSync(pagePath, 'utf8')

    it('offers an explicit LM Studio option distinct from Local/Ollama', () => {
      expect(pageSource).toContain('<option value="lmstudio">LM Studio (OpenAI-compatible)</option>')
    })

    it('collects the LM Studio base URL only when LM Studio is selected', () => {
      expect(pageSource).toContain("const isLmStudio = data.llmProvider === 'lmstudio'")
      expect(pageSource).toContain('LM Studio Base URL')
      expect(pageSource).toContain('http://127.0.0.1:1234/v1')
    })

    it('requires the LM Studio base URL before advancing past the AI step', () => {
      expect(pageSource).toContain("data.llmProvider === 'lmstudio' && !data.openaiBaseUrl.trim()")
      expect(pageSource).toContain('LM Studio base URL is required.')
    })
  })
})

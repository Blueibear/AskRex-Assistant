import { describe, expect, it, vi } from 'vitest'
import { readFileSync, readdirSync } from 'fs'
import { join } from 'path'

const mockApp = vi.hoisted(() => ({
  isPackaged: true,
  getAppPath: () => 'C:\\AskRex\\app',
  getPath: () => 'C:\\AskRex\\runtime'
}))

vi.mock('electron', () => ({ app: mockApp }))

import { bridgeSpawnOptions } from '../src/main/bridgeResolver'

function walkTypeScriptFiles(root: string): string[] {
  const results: string[] = []
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name)
    if (entry.isDirectory()) results.push(...walkTypeScriptFiles(path))
    else if (entry.isFile() && entry.name.endsWith('.ts')) results.push(path)
  }
  return results
}

describe('Python bridge runtime path isolation', () => {
  it('gives every Python bridge spawn the canonical runtime cwd and environment', () => {
    const mainRoot = join(__dirname, '../src/main')
    const failures: string[] = []
    for (const file of walkTypeScriptFiles(mainRoot)) {
      const source = readFileSync(file, 'utf8')
      const bridgeCalls = source.matchAll(
        /\bspawn(?:Sync)?\(resolvePythonCommand\(\),\s*\[[^\]]*\],\s*\{([^}]*)\}/gs
      )
      for (const bridgeCall of bridgeCalls) {
        const options = bridgeCall[1]
        if (!/\.\.\.(?:bridgeSpawnOptions\(\)|voiceBridgeOptions),?/.test(options)) {
          failures.push(file)
        }
      }
    }
    expect(failures).toEqual([])
  })

  it('keeps every chat bridge on the isolated spawn contract', () => {
    const source = readFileSync(join(__dirname, '../src/main/handlers/chat.ts'), 'utf8')
    const bridgeCalls = [
      ...source.matchAll(
        /\bspawn(?:Sync)?\(resolvePythonCommand\(\),\s*\[[^\]]*\],\s*\{([\s\S]*?)\n\s*\}\)/g
      )
    ]

    // Chat, conversation, persistent STT, and streaming chat all execute
    // Python. A new path must explicitly inherit the canonical runtime root.
    expect(bridgeCalls).toHaveLength(4)
    expect(bridgeCalls.every((call) => call[1].includes('...bridgeSpawnOptions()'))).toBe(true)
  })

  it('clears inherited Python module and prefix overrides for packaged bridges', () => {
    const originalPythonPath = process.env.PYTHONPATH
    const originalPythonHome = process.env.PYTHONHOME
    process.env.PYTHONPATH = 'C:\\untrusted\\modules'
    process.env.PYTHONHOME = 'C:\\untrusted\\python'

    try {
      const options = bridgeSpawnOptions()

      expect(options.cwd).toBe('C:\\AskRex\\runtime')
      expect(options.env.PYTHONPATH).toBeUndefined()
      expect(options.env.PYTHONHOME).toBeUndefined()
      expect(options.env.PYTHONNOUSERSITE).toBe('1')
    } finally {
      if (originalPythonPath === undefined) delete process.env.PYTHONPATH
      else process.env.PYTHONPATH = originalPythonPath
      if (originalPythonHome === undefined) delete process.env.PYTHONHOME
      else process.env.PYTHONHOME = originalPythonHome
    }
  })
})

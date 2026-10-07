import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'fs'
import { join } from 'path'

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
})

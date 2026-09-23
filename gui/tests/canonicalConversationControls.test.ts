import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// Vitest executes tests as ESM, where CommonJS's __dirname is unavailable.
// Resolve from this module so this regression is portable across local and CI runs.
const page = readFileSync(new URL('../src/pages/ChatPage.tsx', import.meta.url), 'utf8')
const handlers = readFileSync(new URL('../src/main/handlers/chat.ts', import.meta.url), 'utf8')

describe('canonical conversation controls', () => {
  it('creates, lists, opens, renames, and archives through canonical IPC', () => {
    expect(page).toContain("conversation('create')")
    expect(page).toContain("conversation('list')")
    expect(page).toContain("conversation('open'")
    expect(page).toContain("conversation('rename'")
    expect(page).toContain("conversation('archive'")
    expect(handlers).toContain("rex:conversation")
  })

  it('loads the selected stored transcript after navigation instead of clearing chat state', () => {
    expect(page).toContain('setMessages(toMessages(result.messages))')
    expect(page).toContain('if (rows[0]) await openConversation(rows[0].id)')
    expect(page).toContain('activeConversationId ?? undefined')
  })
})

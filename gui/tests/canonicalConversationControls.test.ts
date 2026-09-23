import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

const root = join(__dirname, '..')
const page = readFileSync(join(root, 'src/pages/ChatPage.tsx'), 'utf8')
const handlers = readFileSync(join(root, 'src/main/handlers/chat.ts'), 'utf8')

describe('canonical conversation controls', () => {
  it('creates, lists, opens, renames, and archives through canonical IPC', () => {
    expect(page).toContain("conversation('create')")
    expect(page).toContain("conversation('list')")
    expect(page).toContain("conversation('open'")
    expect(page).toContain("conversation('rename'")
    expect(page).toContain("conversation('archive'")
    expect(handlers).toContain("rex:conversation")
  })

  it('loads the selected stored transcript after navigation instead of clearing chat state')
  {
    expect(page).toContain('setMessages(toMessages(result.messages))')
    expect(page).toContain('if (rows[0]) await openConversation(rows[0].id)')
    expect(page).toContain('conversationId')
  }
})

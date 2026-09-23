import React, { useState, useCallback, useEffect } from 'react'
import { MessageList } from '../components/chat/MessageList'
import { ChatInput } from '../components/chat/ChatInput'
import type { Message, MessageAttachment } from '../components/chat/MessageList'
import type { PendingAttachment } from '../components/chat/ChatInput'
import type { ConversationMessage, ConversationSummary } from '../types/ipc'

let nextId = 1
function genId(): string {
  return `msg-${nextId++}`
}

/** Build the augmented message text that includes extracted file content. */
function buildAugmentedMessage(text: string, extractions: Map<string, string>): string {
  const parts: string[] = []
  for (const [name, content] of extractions) {
    parts.push(`[Attached file: ${name}]\n${content}\n---`)
  }
  if (text) parts.push(text)
  return parts.join('\n\n')
}

const internalToolSyntaxPattern = /\bTOOL_(?:REQUEST|RESULT)\s*:/i

function sanitizeAssistantText(text: string): string {
  return internalToolSyntaxPattern.test(text)
    ? 'I could not complete that tool request.'
    : text
}

export function ChatPage(): React.ReactElement {
  const [messages, setMessages] = useState<Message[]>([])
  const [sending, setSending] = useState(false)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)

  const toMessages = (rows: ConversationMessage[]): Message[] => rows.map((row) => ({
    id: `stored-${row.id}`, role: row.role === 'assistant' ? 'rex' : 'user', content: row.content,
    timestamp: new Date(row.timestamp)
  }))

  const refreshConversations = useCallback(async (): Promise<ConversationSummary[]> => {
    const rows = await window.rex.conversation('list') as ConversationSummary[]
    setConversations(rows)
    return rows
  }, [])

  const openConversation = useCallback(async (id: string): Promise<void> => {
    const result = await window.rex.conversation('open', { conversation_id: id }) as { messages: ConversationMessage[] }
    setActiveConversationId(id)
    setMessages(toMessages(result.messages))
  }, [])

  const createConversation = useCallback(async (): Promise<void> => {
    const created = await window.rex.conversation('create') as ConversationSummary
    await refreshConversations()
    setActiveConversationId(created.id)
    setMessages([])
  }, [refreshConversations])

  useEffect(() => {
    void refreshConversations().then(async (rows) => {
      if (rows[0]) await openConversation(rows[0].id)
      else await createConversation()
    }).catch(() => undefined)
  }, [createConversation, openConversation, refreshConversations])

  useEffect(() => {
    const focusInput = (): void => {
      const el = document.querySelector<HTMLTextAreaElement>(
        'textarea[aria-label="Chat message input"]'
      )
      el?.focus()
    }
    window.addEventListener('rex:focus-chat', focusInput)
    return () => window.removeEventListener('rex:focus-chat', focusInput)
  }, [])

  const handleSend = useCallback(
    async (text: string, attachments: PendingAttachment[]): Promise<void> => {
      // Process attachments: extract text from documents, keep images as-is
      const displayAttachments: MessageAttachment[] = []
      const textExtractions = new Map<string, string>()

      for (const att of attachments) {
        const isImage = att.mimeType.startsWith('image/')

        if (isImage) {
          displayAttachments.push({ name: att.name, isImage: true, dataUrl: att.dataUrl })
        } else {
          // Extract text via IPC
          try {
            const result = await window.rex.extractFileForChat({
              filename: att.name,
              dataBase64: att.dataBase64,
              mimeType: att.mimeType,
              sizeBytes: att.sizeBytes
            })
            if (result.ok && result.extractedText) {
              textExtractions.set(att.name, result.extractedText)
            }
          } catch {
            // Extraction failed — still show the chip but don't inject content
          }
          displayAttachments.push({ name: att.name, isImage: false })
        }
      }

      const augmentedText = buildAugmentedMessage(text, textExtractions)
      const displayText = text || (attachments.length > 0 ? '(file attachment)' : '')

      const userMsg: Message = {
        id: genId(),
        role: 'user',
        content: displayText,
        timestamp: new Date(),
        attachments: displayAttachments.length > 0 ? displayAttachments : undefined
      }

      setMessages((prev) => [...prev, userMsg])
      setSending(true)

      const rexMsgId = genId()
      const rexMsg: Message = {
        id: rexMsgId,
        role: 'rex',
        content: '',
        timestamp: new Date(),
        streaming: true
      }
      setMessages((prev) => [...prev, rexMsg])

      try {
        let receivedToken = false
        let replyText = ''
        await window.rex.sendChatStream(
          augmentedText,
          (token) => {
            replyText += token
            const safeReplyText = sanitizeAssistantText(replyText)
            setMessages((prev) =>
              prev.map((m) =>
                m.id === rexMsgId
                  ? { ...m, content: safeReplyText }
                  : m
              )
            )
            receivedToken = true
          },
          undefined,
          (status) => {
            if (status === 'model_failure') {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === rexMsgId ? { ...m, status: 'model_failure' } : m
                )
              )
            }
          },
          (recovery) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === rexMsgId ? { ...m, recoveryActions: recovery.actions } : m
              )
            )
          },
          activeConversationId ?? undefined
        )
        // Finalize: remove streaming cursor
        setMessages((prev) =>
          prev.map((m) =>
            m.id === rexMsgId
              ? {
                  ...m,
                  content: receivedToken ? m.content : 'I did not receive a reply from the model.',
                  streaming: false
                }
              : m
          )
        )
      } catch (err) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === rexMsgId
              ? {
                  ...m,
                  content: `\`Error: ${err instanceof Error ? err.message : String(err)}\``,
                  streaming: false
                }
              : m
          )
        )
      } finally {
        setSending(false)
        void refreshConversations()
      }
    },
    [activeConversationId, refreshConversations]
  )

  const renameActive = async (): Promise<void> => {
    if (!activeConversationId) return
    const current = conversations.find((item) => item.id === activeConversationId)
    const title = window.prompt('Conversation name', current?.title ?? '')
    if (title?.trim()) {
      await window.rex.conversation('rename', { conversation_id: activeConversationId, title })
      await refreshConversations()
    }
  }

  const archiveActive = async (): Promise<void> => {
    if (!activeConversationId || !window.confirm('Archive this conversation?')) return
    await window.rex.conversation('archive', { conversation_id: activeConversationId })
    const rows = await refreshConversations()
    if (rows[0]) await openConversation(rows[0].id)
    else await createConversation()
  }

  return (
    <div className="flex h-full">
      <aside className="w-56 shrink-0 border-r border-border p-3 overflow-y-auto" aria-label="Conversations">
        <button className="w-full rounded bg-accent px-3 py-2 text-left" onClick={() => void createConversation()}>New conversation</button>
        <div className="mt-3 space-y-1">
          {conversations.map((conversation) => <button key={conversation.id} className="w-full rounded px-2 py-2 text-left text-sm hover:bg-surface" aria-current={conversation.id === activeConversationId ? 'page' : undefined} onClick={() => void openConversation(conversation.id)}>{conversation.title}</button>)}
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex justify-end gap-2 border-b border-border p-2">
          <button onClick={() => void renameActive()} disabled={!activeConversationId}>Rename</button>
          <button onClick={() => void archiveActive()} disabled={!activeConversationId}>Archive</button>
        </div>
        <MessageList messages={messages} />
        <ChatInput onSend={handleSend} sending={sending || !activeConversationId} />
      </div>
    </div>
  )
}

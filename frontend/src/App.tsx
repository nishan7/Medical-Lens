import './App.css'
import { useEffect, useRef, useState, useCallback } from 'react'
import type { ChangeEvent, FormEvent, KeyboardEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

type Role = 'user' | 'assistant'

type ChatMessage = {
  id: string
  role: Role
  content: string
  attachmentName?: string
  toolStatus?: string
  isStreaming?: boolean
}

type StreamEvent =
  | { type: 'token'; delta: string; request_id: string }
  | { type: 'tool_start'; tool: string; input?: string; request_id: string }
  | { type: 'tool_end'; tool: string; request_id: string }
  | { type: 'done'; request_id: string }
  | { type: 'error'; message?: string; request_id: string }

type UploadResponse = {
  image_id: string
  filename: string
  ocr_status: 'ready' | 'failed'
  ocr_error?: string
  page_count?: number
}

type AttachmentState = 'pending' | 'uploading' | 'ready' | 'failed'

type Conversation = {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: number
}

const STREAM_FLUSH_INTERVAL_MS = 28
const STREAM_CHARS_PER_TICK = 52

const SUGGESTION_CHIPS = [
  { icon: '🩸', label: 'What does a TB test cost at San Jose hospitals?' },
  { icon: '🏥', label: 'Compare MRI prices across hospitals' },
  { icon: '📋', label: 'Analyze my medical bill for errors' },
  { icon: '💳', label: 'Which insurer has the best rates for surgery?' },
  { icon: '🔬', label: 'Show blood panel options and prices' },
  { icon: '📊', label: 'List all available insurers in the dataset' },
]

function getApiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL
  if (configured) return configured.replace(/\/$/, '')
  if (typeof window === 'undefined') return 'http://localhost:8000'
  return `${window.location.protocol}//${window.location.hostname}:8000`
}

function getWsUrl(): string {
  const configured = import.meta.env.VITE_WS_URL
  if (configured) return configured
  if (typeof window === 'undefined') return 'ws://localhost:8000/ws/chat'
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.hostname}:8000/ws/chat`
}

function TypingDots() {
  return (
    <span className="typing-dots" aria-label="Assistant is thinking">
      <span /><span /><span />
    </span>
  )
}

function ToolStatusBadge({ status }: { status: string }) {
  return (
    <div className="tool-status-badge">
      <span className="tool-spinner" />
      <span>{status}</span>
    </div>
  )
}

function App() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConvId, setActiveConvId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [attachedFile, setAttachedFile] = useState<File | null>(null)
  const [attachedImageId, setAttachedImageId] = useState<string | null>(null)
  const [attachmentState, setAttachmentState] = useState<AttachmentState | null>(null)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [attachmentOcrStatus, setAttachmentOcrStatus] = useState<'ready' | 'failed' | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [_currentToolStatus, setCurrentToolStatus] = useState<string | null>(null)

  const chatEndRef = useRef<HTMLDivElement | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const formRef = useRef<HTMLFormElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const activeRequestIdRef = useRef<string | null>(null)
  const activeAssistantIdRef = useRef<string | null>(null)
  const deltaQueueRef = useRef('')
  const doneReceivedRef = useRef(false)
  const flushTimerRef = useRef<number | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    return () => {
      if (flushTimerRef.current !== null) window.clearInterval(flushTimerRef.current)
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [])

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 180) + 'px'
  }, [input])

  function clearFlushTimer() {
    if (flushTimerRef.current !== null) {
      window.clearInterval(flushTimerRef.current)
      flushTimerRef.current = null
    }
  }

  function appendAssistantDelta(assistantMessageId: string, delta: string) {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === assistantMessageId
          ? { ...m, content: m.content + delta, isStreaming: true }
          : m,
      ),
    )
  }

  function finishActiveStream() {
    clearFlushTimer()
    if (wsRef.current) {
      const s = wsRef.current
      wsRef.current = null
      if (s.readyState === WebSocket.OPEN || s.readyState === WebSocket.CONNECTING) s.close()
    }
    // Mark streaming done
    const id = activeAssistantIdRef.current
    if (id) {
      setMessages((prev) => prev.map((m) => m.id === id ? { ...m, isStreaming: false, toolStatus: undefined } : m))
    }
    activeRequestIdRef.current = null
    activeAssistantIdRef.current = null
    deltaQueueRef.current = ''
    doneReceivedRef.current = false
    setIsSending(false)
    setCurrentToolStatus(null)
  }

  function maybeFinishActiveStream() {
    if (doneReceivedRef.current && deltaQueueRef.current.length === 0) {
      finishActiveStream()
    }
  }

  function ensureFlushLoop() {
    if (flushTimerRef.current !== null) return
    flushTimerRef.current = window.setInterval(() => {
      const assistantMessageId = activeAssistantIdRef.current
      if (!assistantMessageId) { clearFlushTimer(); return }
      if (deltaQueueRef.current.length > 0) {
        const delta = deltaQueueRef.current.slice(0, STREAM_CHARS_PER_TICK)
        deltaQueueRef.current = deltaQueueRef.current.slice(STREAM_CHARS_PER_TICK)
        appendAssistantDelta(assistantMessageId, delta)
      }
      maybeFinishActiveStream()
    }, STREAM_FLUSH_INTERVAL_MS)
  }

  function failActiveRequest(assistantMessageId: string, message: string) {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === assistantMessageId && !m.content
          ? { ...m, content: message, isStreaming: false }
          : m,
      ),
    )
    finishActiveStream()
  }

  function addAssistantError(message: string) {
    setMessages((prev) => [...prev, {
      id: crypto.randomUUID(), role: 'assistant', content: message, isStreaming: false,
    }])
  }

  function clearAttachment() {
    setAttachedFile(null)
    setAttachedImageId(null)
    setAttachmentState(null)
    setAttachmentError(null)
    setAttachmentOcrStatus(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] || null
    setAttachedFile(nextFile)
    setAttachedImageId(null)
    setAttachmentError(null)
    setAttachmentOcrStatus(null)
    setAttachmentState(nextFile ? 'pending' : null)
  }

  async function uploadAttachmentIfNeeded(): Promise<string | null> {
    if (!attachedFile) return null
    if (attachedImageId) return attachedImageId

    setAttachmentState('uploading')
    setAttachmentError(null)

    const formData = new FormData()
    formData.append('file', attachedFile)

    const response = await fetch(`${getApiBaseUrl()}/upload-image`, {
      method: 'POST',
      body: formData,
    })

    if (!response.ok) {
      let detail = `Upload failed with status ${response.status}`
      try {
        const payload = await response.json()
        detail = payload?.detail || detail
      } catch { /* keep default */ }
      throw new Error(detail)
    }

    const payload = (await response.json()) as UploadResponse
    setAttachedImageId(payload.image_id)
    setAttachmentOcrStatus(payload.ocr_status)
    setAttachmentState(payload.ocr_status === 'ready' ? 'ready' : 'failed')
    setAttachmentError(payload.ocr_error || null)
    return payload.image_id
  }

  const saveConversation = useCallback((convId: string, msgs: ChatMessage[]) => {
    setConversations((prev) => {
      const existing = prev.find((c) => c.id === convId)
      if (!existing) {
        const firstUserMsg = msgs.find((m) => m.role === 'user')
        const title = firstUserMsg
          ? firstUserMsg.content.slice(0, 42) + (firstUserMsg.content.length > 42 ? '…' : '')
          : 'New conversation'
        return [{ id: convId, title, messages: msgs, createdAt: Date.now() }, ...prev]
      }
      return prev.map((c) => c.id === convId ? { ...c, messages: msgs } : c)
    })
  }, [])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!input.trim()) return

    setIsSending(true)

    let imageIdForRequest: string | null = null
    try {
      imageIdForRequest = await uploadAttachmentIfNeeded()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to upload attached bill.'
      setAttachmentState('failed')
      setAttachmentError(message)
      addAssistantError(`Upload failed: ${message}`)
      setIsSending(false)
      return
    }

    const convId = activeConvId || crypto.randomUUID()
    if (!activeConvId) setActiveConvId(convId)

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: input,
      attachmentName: attachedFile?.name,
    }
    const assistantMessageId = crypto.randomUUID()
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      isStreaming: true,
    }
    const requestId = crypto.randomUUID()

    const newMessages = [...messages, userMessage, assistantMessage]
    setMessages(newMessages)
    setInput('')
    activeRequestIdRef.current = requestId
    activeAssistantIdRef.current = assistantMessageId
    deltaQueueRef.current = ''
    doneReceivedRef.current = false
    clearFlushTimer()

    if (wsRef.current) { wsRef.current.close(); wsRef.current = null }

    const ws = new WebSocket(getWsUrl())
    wsRef.current = ws

    const payload = JSON.stringify({
      request_id: requestId,
      image_id: imageIdForRequest || undefined,
      messages: [
        ...messages.map(({ role, content }) => ({ role, content })),
        { role: 'user', content: userMessage.content },
      ],
    })

    ws.onopen = () => { ws.send(payload) }

    ws.onmessage = (event) => {
      const data: StreamEvent = JSON.parse(event.data)
      if (data.request_id !== activeRequestIdRef.current) return

      if (data.type === 'tool_start') {
        const label = humanizeToolName(data.tool)
        setCurrentToolStatus(label)
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMessageId ? { ...m, toolStatus: label } : m
        ))
      }
      if (data.type === 'tool_end') {
        setCurrentToolStatus(null)
        setMessages((prev) => prev.map((m) =>
          m.id === assistantMessageId ? { ...m, toolStatus: undefined } : m
        ))
      }
      if (data.type === 'token' && data.delta) {
        deltaQueueRef.current += data.delta
        ensureFlushLoop()
      }
      if (data.type === 'done') {
        doneReceivedRef.current = true
        maybeFinishActiveStream()
        // Save conversation after done
        setMessages((prev) => {
          saveConversation(convId, prev)
          return prev
        })
      }
      if (data.type === 'error') {
        failActiveRequest(assistantMessageId, 'Request failed. Please retry.')
      }
    }

    ws.onerror = () => {
      if (activeRequestIdRef.current === requestId)
        failActiveRequest(assistantMessageId, 'Connection error while streaming response.')
    }

    ws.onclose = () => {
      if (activeRequestIdRef.current === requestId)
        failActiveRequest(assistantMessageId, 'Connection closed before response completed.')
    }
  }

  function humanizeToolName(tool: string): string {
    const map: Record<string, string> = {
      lc_hospital_search_by_name: 'Searching hospital pricing data…',
      lc_hospital_search_by_code: 'Looking up procedure code…',
      lc_hospital_cheapest_by_name: 'Finding cheapest options…',
      lc_hospital_list_insurers: 'Loading insurer list…',
      lc_get_server_time: 'Getting server time…',
      lc_echo: 'Processing…',
    }
    return map[tool] || `Running ${tool.replace(/^lc_/, '').replace(/_/g, ' ')}…`
  }

  function handleTextareaKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.metaKey) {
      e.preventDefault()
      if (!isSending && input.trim()) formRef.current?.requestSubmit()
    }
  }

  function handleChipClick(label: string) {
    setInput(label)
    textareaRef.current?.focus()
  }

  function startNewConversation() {
    if (messages.length > 0 && activeConvId) {
      saveConversation(activeConvId, messages)
    }
    setMessages([])
    setActiveConvId(null)
    clearAttachment()
    setInput('')
  }

  function loadConversation(conv: Conversation) {
    if (activeConvId && messages.length > 0) saveConversation(activeConvId, messages)
    setMessages(conv.messages)
    setActiveConvId(conv.id)
  }

  const showWelcome = messages.length === 0

  return (
    <div className="app-shell">
      {/* Sidebar */}
      <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : 'sidebar-closed'}`}>
        <div className="sidebar-header">
          <div className="logo-mark">
            <span className="logo-icon">⚕</span>
            <span className="logo-text">MedicalLens</span>
          </div>
          <button className="sidebar-toggle" onClick={() => setSidebarOpen((v) => !v)} title="Toggle sidebar">
            {sidebarOpen ? '◀' : '▶'}
          </button>
        </div>

        {sidebarOpen && (
          <>
            <button className="new-chat-btn" onClick={startNewConversation}>
              <span>＋</span> New conversation
            </button>

            <div className="sidebar-section-label">Recent</div>
            <div className="conv-list">
              {conversations.length === 0 && (
                <div className="conv-empty">No conversations yet</div>
              )}
              {conversations.map((conv) => (
                <button
                  key={conv.id}
                  className={`conv-item ${conv.id === activeConvId ? 'conv-item-active' : ''}`}
                  onClick={() => loadConversation(conv)}
                >
                  <span className="conv-icon">💬</span>
                  <span className="conv-title">{conv.title}</span>
                </button>
              ))}
            </div>

            <div className="sidebar-footer">
              <div className="sidebar-badge">
                <span className="badge-dot" />
                Powered by NVIDIA AI
              </div>
            </div>
          </>
        )}
      </aside>

      {/* Main content */}
      <div className="main-content">
        <header className="top-bar">
          {!sidebarOpen && (
            <button className="sidebar-toggle-inline" onClick={() => setSidebarOpen(true)} title="Open sidebar">
              ☰
            </button>
          )}
          <div className="top-bar-title">
            <span className="top-bar-icon">⚕</span>
            <div>
              <div className="top-bar-name">MedicalLens</div>
              <div className="top-bar-sub">Healthcare pricing & bill intelligence · San Jose, CA</div>
            </div>
          </div>
          <div className="top-bar-actions">
            <button className="icon-btn" onClick={startNewConversation} title="New chat">＋</button>
          </div>
        </header>

        <main className="chat-main">
          <div className="chat-window" id="chat-window">
            {showWelcome && (
              <div className="welcome-screen">
                <div className="welcome-hero">
                  <div className="welcome-icon-ring">⚕</div>
                  <h2 className="welcome-title">Your Healthcare Transparency Lens</h2>
                  <p className="welcome-sub">
                    Explore real hospital standard charges, compare insurer negotiated rates, find the most affordable care options — and validate your medical bills with AI.
                  </p>
                  <div className="welcome-data-badge">
                    <span className="data-badge-dot" />
                    Live data · O'Connor · Regional Medical · Santa Clara Valley — San Jose, CA
                  </div>
                </div>
                <div className="chip-grid">
                  {SUGGESTION_CHIPS.map((chip) => (
                    <button key={chip.label} className="chip" onClick={() => handleChipClick(chip.label)}>
                      <span className="chip-icon">{chip.icon}</span>
                      <span>{chip.label}</span>
                    </button>
                  ))}
                </div>
                <div className="welcome-features">
                  <div className="feature-pill">🩺 Procedure Lookup</div>
                  <div className="feature-pill">💳 Insurer Rate Compare</div>
                  <div className="feature-pill">🏥 3 San Jose Hospitals</div>
                  <div className="feature-pill">📄 Bill OCR & Audit</div>
                  <div className="feature-pill">💡 Cost Optimization</div>
                </div>
              </div>
            )}

            {messages.map((m) => (
              <div
                key={m.id}
                className={`bubble ${m.role === 'user' ? 'bubble-user' : 'bubble-assistant'}`}
              >
                {m.role === 'assistant' && (
                  <div className="bubble-avatar">⚕</div>
                )}
                <div className="bubble-body">
                  {m.attachmentName && (
                    <div className="bubble-attachment-tag">
                      <span className="attach-icon">📎</span>
                      {m.attachmentName}
                    </div>
                  )}
                  {m.toolStatus && (
                    <ToolStatusBadge status={m.toolStatus} />
                  )}
                  {m.content ? (
                    <div className={`bubble-content ${m.role === 'assistant' ? 'bubble-markdown' : ''}`}>
                      {m.role === 'assistant' ? (
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                      ) : (
                        m.content
                      )}
                    </div>
                  ) : (
                    m.isStreaming && !m.toolStatus && <TypingDots />
                  )}
                  {m.isStreaming && m.content && (
                    <span className="stream-cursor" aria-hidden="true" />
                  )}
                </div>
              </div>
            ))}
            <div ref={chatEndRef} />
          </div>

          {/* Input area */}
          <div className="input-panel">
            {attachmentError && (
              <div className="attachment-error">
                <span>⚠️</span> {attachmentError}
              </div>
            )}

            {attachedFile && (
              <div className="attachment-bar">
                <span className="attach-icon">📎</span>
                <span className="attachment-name">{attachedFile.name}</span>
                <span className={`attachment-pill ${attachmentState}`}>
                  {attachmentState === 'uploading' && '⏳ '}
                  {attachmentState === 'ready' && '✅ '}
                  {attachmentState === 'failed' && '❌ '}
                  {attachmentState === 'pending' && '📄 '}
                  {attachmentState || 'pending'}
                  {attachmentOcrStatus ? ` · OCR ${attachmentOcrStatus}` : ''}
                </span>
                <button className="remove-attach" onClick={clearAttachment} title="Remove">✕</button>
              </div>
            )}

            <form ref={formRef} className="input-form" onSubmit={handleSubmit}>
              <label className="attach-btn" title="Attach bill (image/PDF)">
                <input
                  ref={fileInputRef}
                  className="file-input"
                  type="file"
                  accept="image/jpeg,image/png,image/webp,application/pdf"
                  onChange={handleFileChange}
                />
                📎
              </label>

              <textarea
                ref={textareaRef}
                className="text-input"
                placeholder="Ask about a test, procedure, insurer rates, or upload a bill to audit…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleTextareaKeyDown}
                rows={1}
                disabled={isSending}
              />

              <button
                className={`send-btn ${isSending ? 'sending' : ''}`}
                type="submit"
                disabled={isSending || !input.trim()}
                title="Send"
              >
                {isSending ? (
                  <span className="send-spinner" />
                ) : (
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="22" y1="2" x2="11" y2="13" />
                    <polygon points="22 2 15 22 11 13 2 9 22 2" />
                  </svg>
                )}
              </button>
            </form>

            <div className="input-hint">
              Press <kbd>Enter</kbd> to send · <kbd>Shift+Enter</kbd> for newline · Attach a bill image or PDF
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}

export default App

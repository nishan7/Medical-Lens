import './App.css'
import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent, KeyboardEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

type Role = 'user' | 'assistant'

type ChatMessage = {
  id: string
  role: Role
  content: string
  attachmentName?: string
}

type StreamEvent =
  | { type: 'token'; delta: string; request_id: string }
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

const STREAM_FLUSH_INTERVAL_MS = 33
const STREAM_CHARS_PER_TICK = 48

function getApiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL
  if (configured) {
    return configured.replace(/\/$/, '')
  }

  if (typeof window === 'undefined') {
    return 'http://localhost:8000'
  }

  return `${window.location.protocol}//${window.location.hostname}:8000`
}

function getWsUrl(): string {
  const configured = import.meta.env.VITE_WS_URL
  if (configured) {
    return configured
  }

  if (typeof window === 'undefined') {
    return 'ws://localhost:8000/ws/chat'
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.hostname}:8000/ws/chat`
}

function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [attachedFile, setAttachedFile] = useState<File | null>(null)
  const [attachedImageId, setAttachedImageId] = useState<string | null>(null)
  const [attachmentState, setAttachmentState] = useState<AttachmentState | null>(null)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [attachmentOcrStatus, setAttachmentOcrStatus] = useState<'ready' | 'failed' | null>(null)
  const chatEndRef = useRef<HTMLDivElement | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const formRef = useRef<HTMLFormElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const activeRequestIdRef = useRef<string | null>(null)
  const activeAssistantIdRef = useRef<string | null>(null)
  const deltaQueueRef = useRef('')
  const doneReceivedRef = useRef(false)
  const flushTimerRef = useRef<number | null>(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    return () => {
      if (flushTimerRef.current !== null) {
        window.clearInterval(flushTimerRef.current)
      }
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [])

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
          ? { ...m, content: m.content + delta }
          : m,
      ),
    )
  }

  function finishActiveStream() {
    clearFlushTimer()
    if (wsRef.current) {
      const currentSocket = wsRef.current
      wsRef.current = null
      if (currentSocket.readyState === WebSocket.OPEN || currentSocket.readyState === WebSocket.CONNECTING) {
        currentSocket.close()
      }
    }
    activeRequestIdRef.current = null
    activeAssistantIdRef.current = null
    deltaQueueRef.current = ''
    doneReceivedRef.current = false
    setIsSending(false)
  }

  function maybeFinishActiveStream() {
    if (doneReceivedRef.current && deltaQueueRef.current.length === 0) {
      finishActiveStream()
    }
  }

  function ensureFlushLoop() {
    if (flushTimerRef.current !== null) {
      return
    }

    flushTimerRef.current = window.setInterval(() => {
      const assistantMessageId = activeAssistantIdRef.current
      if (!assistantMessageId) {
        clearFlushTimer()
        return
      }

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
          ? { ...m, content: message }
          : m,
      ),
    )
    finishActiveStream()
  }

  function addAssistantError(message: string) {
    const errorMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: message,
    }
    setMessages((prev) => [...prev, errorMessage])
  }

  function clearAttachment() {
    setAttachedFile(null)
    setAttachedImageId(null)
    setAttachmentState(null)
    setAttachmentError(null)
    setAttachmentOcrStatus(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
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
    if (!attachedFile) {
      return null
    }
    if (attachedImageId) {
      return attachedImageId
    }

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
      } catch {
        // keep default detail
      }
      throw new Error(detail)
    }

    const payload = (await response.json()) as UploadResponse
    setAttachedImageId(payload.image_id)
    setAttachmentOcrStatus(payload.ocr_status)
    setAttachmentState(payload.ocr_status === 'ready' ? 'ready' : 'failed')
    setAttachmentError(payload.ocr_error || null)
    return payload.image_id
  }

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
    }
    const requestId = crypto.randomUUID()

    setMessages((prev) => [...prev, userMessage, assistantMessage])
    setInput('')
    activeRequestIdRef.current = requestId
    activeAssistantIdRef.current = assistantMessageId
    deltaQueueRef.current = ''
    doneReceivedRef.current = false
    clearFlushTimer()

    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }

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

    ws.onopen = () => {
      ws.send(payload)
    }

    ws.onmessage = (event) => {
      const data: StreamEvent = JSON.parse(event.data)
      if (data.request_id !== activeRequestIdRef.current) {
        return
      }

      if (data.type === 'token' && data.delta) {
        deltaQueueRef.current += data.delta
        ensureFlushLoop()
      }
      if (data.type === 'done') {
        doneReceivedRef.current = true
        maybeFinishActiveStream()
      }
      if (data.type === 'error') {
        failActiveRequest(assistantMessageId, 'Request failed. Please retry.')
      }
    }

    ws.onerror = () => {
      if (activeRequestIdRef.current === requestId) {
        failActiveRequest(assistantMessageId, 'Connection error while streaming response.')
      }
    }

    ws.onclose = () => {
      if (activeRequestIdRef.current === requestId) {
        failActiveRequest(assistantMessageId, 'Connection closed before response completed.')
      }
    }
  }

  function handleTextareaKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.metaKey) {
      e.preventDefault()
      if (!isSending && input.trim()) {
        formRef.current?.requestSubmit()
      }
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>RightCost</h1>
        <p>Healthcare pricing chat with streaming</p>
      </header>

      <main className="chat-container">
        <div className="chat-window">
          {messages.map((m) => (
            <div
              key={m.id}
              className={`bubble ${m.role === 'user' ? 'bubble-user' : 'bubble-assistant'}`}
            >
              {m.attachmentName && (
                <div className="bubble-meta">
                  Bill: {m.attachmentName}
                </div>
              )}
              <div className="bubble-content bubble-markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {m.content}
                </ReactMarkdown>
              </div>
            </div>
          ))}
          <div ref={chatEndRef} />
        </div>

        <form ref={formRef} className="input-area" onSubmit={handleSubmit}>
          <div className="attachment-row">
            <label className="file-button">
              <input
                ref={fileInputRef}
                className="file-input"
                type="file"
                accept="image/jpeg,image/png,image/webp,application/pdf"
                onChange={handleFileChange}
              />
              {attachedFile ? 'Replace bill' : 'Attach bill (image/PDF)'}
            </label>
            {attachedFile && (
              <div className="attachment-chip">
                <span className="attachment-name">{attachedFile.name}</span>
                <span className="attachment-status">
                  {attachmentState || 'pending'}
                  {attachmentOcrStatus ? ` / OCR ${attachmentOcrStatus}` : ''}
                </span>
                <button type="button" className="chip-remove" onClick={clearAttachment}>
                  Remove
                </button>
              </div>
            )}
          </div>

          {attachmentError && (
            <div className="attachment-error">{attachmentError}</div>
          )}

          <textarea
            className="text-input"
            placeholder="Type your message..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleTextareaKeyDown}
            rows={2}
          />
          <div className="input-row">
            <button className="send-button" type="submit" disabled={isSending}>
              {isSending ? 'Sending...' : 'Send'}
            </button>
          </div>
        </form>
      </main>
    </div>
  )
}

export default App

import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  chatMealWithProgress,
  getChatModels,
  importFoodLabel,
  MACRO_KEYS, MACRO_LABELS, MACRO_UNITS,
  type ChatProgressEvent,
  type ChatMessage, type ChatModelOption, type ChatProposedItem, type ChatResponse, type Meal, type Macros, type FoodImportResult,
} from '../api'

interface SpeechRecognitionAlternativeLike {
  transcript: string
}

interface SpeechRecognitionResultLike {
  isFinal: boolean
  [index: number]: SpeechRecognitionAlternativeLike
}

interface SpeechRecognitionResultListLike {
  length: number
  [index: number]: SpeechRecognitionResultLike
}

interface SpeechRecognitionEventLike extends Event {
  resultIndex: number
  results: SpeechRecognitionResultListLike
}

interface SpeechRecognitionErrorEventLike extends Event {
  error: string
}

interface SpeechRecognitionLike extends EventTarget {
  continuous: boolean
  interimResults: boolean
  lang: string
  onstart: ((this: SpeechRecognitionLike, ev: Event) => unknown) | null
  onend: ((this: SpeechRecognitionLike, ev: Event) => unknown) | null
  onresult: ((this: SpeechRecognitionLike, ev: SpeechRecognitionEventLike) => unknown) | null
  onerror: ((this: SpeechRecognitionLike, ev: SpeechRecognitionErrorEventLike) => unknown) | null
  start(): void
  stop(): void
  abort(): void
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike

interface WindowWithSpeechRecognition extends Window {
  SpeechRecognition?: SpeechRecognitionConstructor
  webkitSpeechRecognition?: SpeechRecognitionConstructor
}

function getSpeechRecognitionConstructor(): SpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null
  const speechWindow = window as WindowWithSpeechRecognition
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition ?? null
}

function speechErrorMessage(error: string): string {
  switch (error) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Microphone permission is blocked. Allow microphone access in Safari settings.'
    case 'audio-capture':
      return 'No microphone was detected.'
    case 'no-speech':
      return 'No speech detected. Try again and speak closer to the microphone.'
    case 'network':
      return 'Speech recognition needs a network connection.'
    default:
      return 'Voice input failed. You can still use keyboard dictation on iPhone/Mac.'
  }
}

function formatModelDate(created: number): string {
  if (created <= 0) return 'unknown date'
  const asMillis = created > 1_000_000_000_000 ? created : created * 1000
  return new Date(asMillis).toLocaleDateString()
}

function modelOptionLabel(model: ChatModelOption): string {
  const inCost = model.input_cost_per_million.toFixed(2)
  const outCost = model.output_cost_per_million.toFixed(2)
  const tier = model.tier_label ?? (model.tier ? model.tier.replace('_', ' ') : '')
  const tierPrefix = tier ? `${tier} · ` : ''
  return (
    `${model.provider} · ${tierPrefix}${model.name} · `
    + `${formatModelDate(model.created)} · $${inCost}/$${outCost}`
  )
}

function formatElapsedTime(elapsedMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function progressMessageForEvent(eventName: string | null): string | null {
  switch (eventName) {
    case 'upstream_request_started':
      return 'Request sent to OpenRouter...'
    case 'upstream_keepalive_comment':
      return 'OpenRouter acknowledged request and is still processing...'
    case 'upstream_text_chunk':
      return 'Receiving response chunks...'
    case 'tool_calls_received':
      return 'Model requested tool calls...'
    case 'tool_call_started':
      return 'Running a local tool...'
    case 'tool_call_completed':
      return 'Local tool finished; continuing...'
    case 'tool_call_failed':
      return 'A local tool failed...'
    case 'upstream_response_received':
      return 'OpenRouter responded, processing payload...'
    case 'upstream_transport_error':
      return 'Transient network issue; retrying if possible...'
    case 'upstream_retryable_status':
      return 'OpenRouter returned a retryable error; retrying...'
    case 'upstream_stream_error_chunk':
      return 'OpenRouter reported a stream error...'
    case 'upstream_generation_error':
      return 'Model provider returned a generation error...'
    case 'upstream_empty_terminal_response':
      return 'Model provider ended with an empty response...'
    case 'gemini_forced_tool_retry':
      return 'Retrying with stricter tool-calling mode...'
    case 'tool_call_arguments_invalid':
      return 'Model returned invalid tool arguments...'
    case 'upstream_round_complete':
      return 'Model provider finished; finalizing response...'
    case 'upstream_stream_done':
      return 'Stream completed ([DONE] received)'
    case 'upstream_stream_idle_timeout':
      return 'Stream timed out waiting for data'
    case 'upstream_stream_exhausted':
      return 'Stream closed without finish signal'
    case 'upstream_finish_reason':
      return 'Received finish reason from provider'
    case 'upstream_raw_line':
      return null  // handled specially below
    default:
      return 'OpenRouter is still processing your request...'
  }
}

function progressSourceLabel(source: string | null): string {
  switch (source) {
    case 'openrouter':
      return 'OpenRouter'
    case 'local_tool':
      return 'Local tool'
    case 'finalizing':
      return 'Finalizing'
    case 'backend':
      return 'Backend'
    default:
      return 'Activity'
  }
}

function importedFoodToChatPrompt(food: FoodImportResult): string {
  const descriptor = food.brand ? `${food.brand} ${food.name}` : food.name
  const lines = [
    `I scanned a nutrition label. Here's what was detected:`,
    `- Name: ${food.name}`,
    `- Brand: ${food.brand || '(none detected)'}`,
    `- Serving size: ${food.serving_size_grams}g`,
    `- Calories: ${food.calories_per_serving}`,
    `- Fat: ${food.fat_per_serving}g`,
    `- Sat Fat: ${food.saturated_fat_per_serving}g`,
    `- Cholesterol: ${food.cholesterol_per_serving}mg`,
    `- Sodium: ${food.sodium_per_serving}mg`,
    `- Carbs: ${food.carbs_per_serving}g`,
    `- Fiber: ${food.fiber_per_serving}g`,
    `- Protein: ${food.protein_per_serving}g`,
    ``,
    `Please verify these details are correct before saving.`,
    `I ate one serving (${food.serving_size_grams}g) of ${descriptor}.`,
  ]
  return lines.join('\n')
}

interface MessageBubble {
  role: 'user' | 'assistant'
  content: string
  proposedItems?: ChatProposedItem[]
  savedMeal?: Meal
  editMealId?: number
}

function computeItemMacro(item: ChatProposedItem, macro: keyof Macros): number {
  const ratio = item.serving_size_grams > 0 ? item.amount_grams / item.serving_size_grams : 0
  return Math.round(item.macros_per_serving[macro] * ratio * 10) / 10
}

function ProposedItemsCard({
  items,
  onConfirm,
  confirmed,
  isEdit,
}: {
  items: ChatProposedItem[]
  onConfirm: () => void
  confirmed: boolean
  isEdit?: boolean
}) {
  const totals = MACRO_KEYS.reduce((acc, m) => {
    acc[m] = items.reduce((sum, item) => sum + computeItemMacro(item, m), 0)
    return acc
  }, {} as Record<keyof Macros, number>)

  return (
    <div className="mt-2 bg-gray-50 rounded-lg border border-gray-200 p-3">
      <p className="text-xs font-medium text-gray-500 mb-2">
        {isEdit ? 'Proposed edit:' : 'Proposed breakdown:'}
      </p>
      <div className="space-y-1">
        {items.map((item, i) => (
          <div key={i} className="flex justify-between text-sm gap-3">
            <span className={item.food_id === null && !item.recipe_id ? 'text-gray-400 italic' : 'text-gray-700'}>
              {item.name}
              {item.food_id === null && !item.recipe_id && ' (not in database)'}
            </span>
            <span className="text-gray-500 whitespace-nowrap">
              {item.amount_grams}g
              {(item.food_id !== null || item.recipe_id) && ` · ${Math.round(computeItemMacro(item, 'calories'))} cal`}
            </span>
          </div>
        ))}
      </div>
      <div className="border-t border-gray-200 mt-2 pt-2 flex flex-wrap gap-2 text-xs text-gray-500">
        {MACRO_KEYS.map((m) => (
          <span key={m}>
            {MACRO_LABELS[m]}: <strong>{Math.round(totals[m])}</strong>{MACRO_UNITS[m]}
          </span>
        ))}
      </div>
      {!confirmed && (
        <button
          type="button"
          onClick={onConfirm}
          className="mt-3 w-full py-2.5 bg-green-600 text-white text-sm font-medium rounded-md hover:bg-green-700 active:bg-green-800"
        >
          {isEdit ? 'Confirm Edit' : 'Confirm & Save'}
        </button>
      )}
    </div>
  )
}

function SavedMealCard({ meal, isEdit }: { meal: Meal; isEdit?: boolean }) {
  return (
    <div className="mt-2 bg-green-50 border border-green-200 rounded-lg p-3">
      <p className="text-sm font-medium text-green-800">
        {isEdit ? 'Meal updated!' : 'Meal saved!'} {meal.date} ({meal.meal_type}) — {Math.round(meal.total_calories)} kcal
      </p>
      <p className="text-xs text-green-600 mt-1">
        P: {Math.round(meal.total_protein)}g · C: {Math.round(meal.total_carbs)}g · F: {Math.round(meal.total_fat)}g
      </p>
    </div>
  )
}

function today() {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const CHAT_STORAGE_KEY = 'diet-chat-messages'
const CHAT_SAVED_KEY = 'diet-chat-saved'
const CHAT_MODEL_KEY = 'diet-chat-model'

function loadChatState(): { messages: MessageBubble[]; saved: boolean } {
  try {
    const raw = sessionStorage.getItem(CHAT_STORAGE_KEY)
    const savedFlag = sessionStorage.getItem(CHAT_SAVED_KEY)
    if (!raw) return { messages: [], saved: false }
    const data = JSON.parse(raw)
    if (data.date !== today()) {
      sessionStorage.removeItem(CHAT_STORAGE_KEY)
      sessionStorage.removeItem(CHAT_SAVED_KEY)
      return { messages: [], saved: false }
    }
    return { messages: data.messages ?? [], saved: savedFlag === 'true' }
  } catch {
    return { messages: [], saved: false }
  }
}

function saveChatState(messages: MessageBubble[], saved: boolean) {
  sessionStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify({
    date: today(),
    messages,
  }))
  sessionStorage.setItem(CHAT_SAVED_KEY, String(saved))
}

export default function MealLogPage() {
  const initial = loadChatState()
  const [messages, setMessages] = useState<MessageBubble[]>(initial.messages)
  const [input, setInput] = useState('')
  const [editingMessageIndex, setEditingMessageIndex] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [importingImage, setImportingImage] = useState(false)
  const [listening, setListening] = useState(false)
  const [speechError, setSpeechError] = useState<string | null>(null)
  const [chatModels, setChatModels] = useState<ChatModelOption[]>([])
  const [selectedModel, setSelectedModel] = useState(() => localStorage.getItem(CHAT_MODEL_KEY) ?? '')
  const [modelLoadError, setModelLoadError] = useState<string | null>(null)
  const [modelsLoading, setModelsLoading] = useState(true)
  const [saved, setSaved] = useState(initial.saved)
  const [progressElapsedMs, setProgressElapsedMs] = useState(0)
  const [progressLog, setProgressLog] = useState<{ time: number; text: string }[]>([])
  const progressLogRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const speechRecognitionAvailable = getSpeechRecognitionConstructor() !== null

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  useEffect(() => {
    progressLogRef.current?.scrollTo({ top: progressLogRef.current.scrollHeight, behavior: 'smooth' })
  }, [progressLog])

  useEffect(() => {
    saveChatState(messages, saved)
  }, [messages, saved])

  useEffect(() => {
    let cancelled = false
    setModelsLoading(true)
    getChatModels()
      .then((resp) => {
        if (cancelled) return
        setChatModels(resp.models)
        const availableIds = new Set(resp.models.map((model) => model.id))
        const stored = localStorage.getItem(CHAT_MODEL_KEY)
        const nonzero = resp.models.filter(
          (m) => m.input_cost_per_million + m.output_cost_per_million > 0,
        )
        const cheapest = nonzero.length > 0
          ? nonzero.reduce((a, b) =>
              a.input_cost_per_million + a.output_cost_per_million <=
              b.input_cost_per_million + b.output_cost_per_million
                ? a : b,
            )
          : null
        const fallbackModel = cheapest?.id ?? resp.default_model ?? (resp.models[0]?.id ?? '')
        setSelectedModel((previous) => {
          // Prefer localStorage value, then current state, then cheapest
          const preferred = stored && availableIds.has(stored) ? stored : null
          if (preferred) return preferred
          if (previous && availableIds.has(previous)) return previous
          localStorage.setItem(CHAT_MODEL_KEY, fallbackModel)
          return fallbackModel
        })
        setModelLoadError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setChatModels([])
        setSelectedModel('')
        setModelLoadError(err instanceof Error ? err.message : 'Failed to load model list')
      })
      .finally(() => {
        if (!cancelled) setModelsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    return () => {
      recognitionRef.current?.abort()
      recognitionRef.current = null
    }
  }, [])

  useEffect(() => {
    if (loading || importingImage) {
      recognitionRef.current?.stop()
    }
  }, [importingImage, loading])

  const handleSend = async (overrideContent?: string) => {
    const isManualSend = overrideContent === undefined
    const userContent = (overrideContent ?? input).trim()
    if (!userContent || loading) return
    recognitionRef.current?.stop()

    const activeEditIndex = isManualSend ? editingMessageIndex : null
    const baseMessages = activeEditIndex !== null
      ? messages.slice(0, activeEditIndex)
      : messages
    const newMessages: MessageBubble[] = [
      ...baseMessages,
      { role: 'user', content: userContent },
    ]
    setMessages(newMessages)
    if (isManualSend) setInput('')
    setEditingMessageIndex(null)
    setLoading(true)
    setProgressElapsedMs(0)
    setProgressLog([{ time: 0, text: 'Submitting request to model provider...' }])

    try {
      const apiHistory: ChatMessage[] = newMessages.map((m) => ({
        role: m.role,
        content: m.content,
      }))
      let prevActivityEvent: string | null = null
      const onProgressEvent = (event: ChatProgressEvent) => {
        if (event.type === 'status') {
          setProgressElapsedMs(event.elapsed_ms)
          const activityEvent = event.last_activity_event

          // Raw stream lines: skip keepalive comments, show everything else
          if (activityEvent === 'upstream_raw_line' && event.stream_line) {
            if (!event.stream_line.startsWith(':')) {
              const truncated = event.stream_line.length > 120
                ? event.stream_line.slice(0, 120) + '...'
                : event.stream_line
              setProgressLog((prev) => [...prev, { time: event.elapsed_ms, text: `SSE: ${truncated}` }])
            }
            return
          }

          // Skip keepalive events from cluttering the log
          if (activityEvent === 'upstream_keepalive_comment') return

          // Only append a log entry when the activity event changes
          if (activityEvent !== prevActivityEvent) {
            prevActivityEvent = activityEvent
            const source = progressSourceLabel(event.activity_source)
            const toolSuffix = event.active_tool_name ? ` (${event.active_tool_name})` : ''
            const roundPrefix = event.upstream_round ? `[R${event.upstream_round}] ` : ''
            const msg = progressMessageForEvent(activityEvent) || activityEvent || 'processing'
            const line = `${roundPrefix}${source}: ${msg}${toolSuffix}`
            setProgressLog((prev) => [...prev, { time: event.elapsed_ms, text: line }])
          }
        }
      }

      const resp: ChatResponse = await chatMealWithProgress(
        apiHistory,
        onProgressEvent,
        undefined,
        undefined,
        undefined,
        selectedModel || undefined,
      )

      const assistantBubble: MessageBubble = {
        role: 'assistant',
        content: resp.message,
        proposedItems: resp.proposed_items ?? undefined,
        savedMeal: resp.saved_meal ?? undefined,
        editMealId: resp.edit_meal_id ?? undefined,
      }
      setMessages([...newMessages, assistantBubble])
      if (resp.saved_meal) {
        setSaved(true)  // track for session persistence only
      }
    } catch (err) {
      const errorText = err instanceof Error ? err.message : 'Something went wrong. Please try again.'
      // Preserve the progress log in the error message for debugging
      setProgressLog((prevLog) => {
        const logDump = prevLog.length > 0
          ? '\n\n<details><summary>Stream log</summary>\n\n```\n'
            + prevLog.map((e) => `${formatElapsedTime(e.time)} ${e.text}`).join('\n')
            + '\n```\n</details>'
          : ''
        setMessages([...newMessages, {
          role: 'assistant',
          content: errorText + logDump,
        }])
        return prevLog
      })
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleClearChat = () => {
    recognitionRef.current?.stop()
    setMessages([])
    setInput('')
    setEditingMessageIndex(null)
    setSaved(false)
    setSpeechError(null)
    setProgressElapsedMs(0)
    setProgressLog([])
    sessionStorage.removeItem(CHAT_STORAGE_KEY)
    sessionStorage.removeItem(CHAT_SAVED_KEY)
  }

  const handleImportImage = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const target = e.target
    const file = target.files?.[0]
    target.value = ''
    if (!file || loading || importingImage) return

    setImportingImage(true)
    try {
      const imported = await importFoodLabel(file)
      await handleSend(importedFoodToChatPrompt(imported))
    } catch (err) {
      const errorText = err instanceof Error ? err.message : 'Failed to import image'
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Image import failed: ${errorText}` },
      ])
    } finally {
      setImportingImage(false)
    }
  }

  const handleStartMessageEdit = (index: number) => {
    const message = messages[index]
    if (!message || message.role !== 'user') return
    recognitionRef.current?.stop()
    setSpeechError(null)
    setEditingMessageIndex(index)
    setInput(message.content)
  }

  const handleCancelMessageEdit = () => {
    setEditingMessageIndex(null)
    setInput('')
  }

  const handleToggleSpeechInput = () => {
    if (loading || importingImage) return

    if (listening && recognitionRef.current) {
      recognitionRef.current.stop()
      return
    }

    const SpeechRecognitionCtor = getSpeechRecognitionConstructor()
    if (!SpeechRecognitionCtor) {
      setSpeechError('Voice input is unavailable. Use the keyboard mic for native dictation.')
      return
    }

    if (!recognitionRef.current) {
      const recognition = new SpeechRecognitionCtor()
      recognition.continuous = true
      recognition.interimResults = false
      recognition.onstart = () => {
        setListening(true)
      }
      recognition.onend = () => {
        setListening(false)
      }
      recognition.onresult = (event) => {
        let transcript = ''
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const result = event.results[i]
          if (result.isFinal) {
            transcript += result[0]?.transcript ?? ''
          }
        }
        const normalized = transcript.trim()
        if (!normalized) return
        setInput((previous) => (previous.trim() ? `${previous.trimEnd()} ${normalized}` : normalized))
      }
      recognition.onerror = (event) => {
        setSpeechError(speechErrorMessage(event.error))
      }
      recognitionRef.current = recognition
    }

    const recognition = recognitionRef.current
    if (!recognition) return
    recognition.lang = navigator.language || 'en-US'
    setSpeechError(null)
    try {
      recognition.start()
    } catch {
      setSpeechError('Could not start voice input. Check microphone permission and try again.')
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2 mb-3 shrink-0">
        <h1 className="text-lg font-semibold text-gray-900 shrink-0">Chat</h1>
        <select
          id="chat-model-select"
          value={selectedModel}
          onChange={(e) => { setSelectedModel(e.target.value); localStorage.setItem(CHAT_MODEL_KEY, e.target.value) }}
          disabled={modelsLoading || loading || importingImage}
          className="min-w-0 flex-1 px-2 py-1.5 border border-gray-300 rounded-md text-xs bg-white disabled:bg-gray-50"
        >
          {chatModels.length === 0 && <option value="">Default model</option>}
          {chatModels.map((model) => (
            <option key={model.id} value={model.id}>
              {modelOptionLabel(model)}
            </option>
          ))}
        </select>
        <button
          onClick={handleClearChat}
          className="shrink-0 px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-md hover:bg-blue-700 active:bg-blue-800"
        >
          Clear
        </button>
      </div>
      {modelLoadError && (
        <p className="mb-2 text-xs text-red-500">
          Model list unavailable: {modelLoadError}
        </p>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-3 mb-3 pr-1">
          {messages.length === 0 && (
            <div className="flex items-center justify-center h-full">
              <div className="text-center text-gray-400 max-w-sm px-4">
                <p className="text-lg mb-2">Describe a meal or log your weight</p>
                <p className="text-sm">
                  Tap the camera icon to scan a nutrition label, or type a meal or weigh-in.
                </p>
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex items-start gap-2 ${
                msg.role === 'user' ? 'justify-end' : 'justify-start'
              }`}
            >
              <div
                className={`max-w-[90%] md:max-w-[80%] rounded-lg px-4 py-2 ${
                  msg.role === 'user'
                    ? 'bg-blue-600 text-white'
                    : 'bg-white border border-gray-200 text-gray-700'
                } ${editingMessageIndex === i ? 'ring-2 ring-amber-300 ring-offset-1' : ''}`}
              >
                {msg.role === 'assistant' ? (
                  <div className="text-sm prose prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-table:my-1 prose-headings:my-1.5 prose-pre:my-1">
                    <Markdown remarkPlugins={[remarkGfm]}>
                      {msg.content}
                    </Markdown>
                  </div>
                ) : (
                  <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
                )}
                {msg.proposedItems && msg.proposedItems.length > 0 && (
                  <ProposedItemsCard
                    items={msg.proposedItems}
                    onConfirm={() => handleSend('Yes, save it')}
                    confirmed={!!msg.savedMeal || i < messages.length - 1}
                    isEdit={!!msg.editMealId}
                  />
                )}
                {msg.savedMeal && <SavedMealCard meal={msg.savedMeal} isEdit={!!msg.editMealId} />}
              </div>
              {msg.role === 'user' && (
                <button
                  type="button"
                  onClick={() => handleStartMessageEdit(i)}
                  disabled={loading || importingImage}
                  className="text-xs text-gray-400 hover:text-gray-600 disabled:opacity-40"
                >
                  Edit
                </button>
              )}
            </div>
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="bg-white border border-gray-200 rounded-lg px-4 py-2 w-full max-w-md">
                <div className="flex items-center gap-2">
                  <div className="h-3.5 w-3.5 rounded-full border-2 border-gray-300 border-t-blue-600 animate-spin shrink-0" />
                  <p className="text-sm text-gray-600 truncate">
                    {progressLog.length > 0 ? progressLog[progressLog.length - 1].text : 'Starting...'}
                  </p>
                  <span className="ml-auto text-xs text-gray-400 shrink-0">{formatElapsedTime(progressElapsedMs)}</span>
                </div>
                <div className="mt-2 h-1.5 w-full rounded-full bg-gray-100 overflow-hidden">
                  <div
                    className="h-full bg-blue-500 transition-[width] duration-300"
                    style={{ width: `${Math.min(95, Math.round((progressElapsedMs / 180000) * 100))}%` }}
                  />
                </div>
                <div
                  ref={progressLogRef}
                  className="mt-2 max-h-32 overflow-y-auto text-[11px] text-gray-500 font-mono space-y-0.5"
                >
                  {progressLog.map((entry, i) => (
                    <p key={i}>
                      <span className="text-gray-400">{formatElapsedTime(entry.time)}</span>
                      {' '}
                      {entry.text}
                    </p>
                  ))}
                </div>
              </div>
            </div>
          )}

        <div ref={bottomRef} />
      </div>

          {editingMessageIndex !== null && (
            <div className="mb-2 flex items-center justify-between rounded-md bg-amber-50 px-2.5 py-1.5">
              <p className="text-xs text-amber-700">
                Editing a previous message. Sending will replace later replies.
              </p>
              <button
                type="button"
                onClick={handleCancelMessageEdit}
                className="text-xs text-amber-700 hover:text-amber-900"
              >
                Cancel
              </button>
            </div>
          )}
          <div className="flex gap-2">
            <input
              ref={cameraInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              onChange={handleImportImage}
              className="hidden"
              disabled={loading || importingImage}
            />
            <button
              type="button"
              onClick={() => cameraInputRef.current?.click()}
              disabled={loading || importingImage}
              className="px-3 py-2.5 border border-gray-300 rounded-md text-gray-600 hover:text-gray-900 hover:border-gray-400 disabled:opacity-50"
              aria-label="Scan nutrition label"
              title="Scan nutrition label"
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M4 8.5C4 7.12 5.12 6 6.5 6H8l1.2-1.6A2 2 0 0 1 10.8 3.5h2.4a2 2 0 0 1 1.6.9L16 6h1.5A2.5 2.5 0 0 1 20 8.5V18a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 18V8.5Z"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <circle cx="12" cy="13" r="3.5" stroke="currentColor" strokeWidth="1.8" />
              </svg>
            </button>
            <button
              type="button"
              onClick={handleToggleSpeechInput}
              disabled={(!speechRecognitionAvailable && !listening) || loading || importingImage}
              className={`px-3 py-2.5 border rounded-md disabled:opacity-50 ${
                listening
                  ? 'border-red-300 text-red-600 bg-red-50'
                  : 'border-gray-300 text-gray-600 hover:text-gray-900 hover:border-gray-400'
              }`}
              aria-label={listening ? 'Stop voice input' : 'Start voice input'}
              title={
                speechRecognitionAvailable
                  ? (listening ? 'Stop voice input' : 'Start voice input')
                  : 'Voice input unavailable here. Use keyboard dictation mic.'
              }
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M12 15.5a3.5 3.5 0 0 0 3.5-3.5V7.5a3.5 3.5 0 1 0-7 0V12a3.5 3.5 0 0 0 3.5 3.5Z"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path d="M6.5 11.5a5.5 5.5 0 0 0 11 0" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                <path d="M12 17v3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                <path d="M9 20.5h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </button>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Describe what you ate..."
              className="flex-1 px-3 py-2.5 border border-gray-300 rounded-md text-sm disabled:bg-gray-50"
              disabled={loading || importingImage}
            />
            <button
              onClick={() => handleSend()}
              disabled={loading || importingImage || !input.trim()}
              className="px-4 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 active:bg-blue-800 disabled:opacity-50"
            >
              {importingImage ? 'Importing...' : editingMessageIndex !== null ? 'Resend' : 'Send'}
            </button>
          </div>
      {speechError ? (
        <p className="mt-2 text-xs text-red-500">{speechError}</p>
      ) : !speechRecognitionAvailable ? (
        <p className="mt-2 text-xs text-gray-400">
          Voice button is unavailable here. On iPhone/Mac Safari, use the keyboard microphone for dictation.
        </p>
      ) : null}
      {listening && <p className="mt-2 text-xs text-red-500">Listening...</p>}
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import {
  chatMeal,
  importFoodLabel,
  getDailySummary,
  MACRO_KEYS, MACRO_LABELS, MACRO_UNITS,
  type ChatMessage, type ChatProposedItem, type ChatResponse, type DailySummary, type Meal, type Macros, type FoodImportResult,
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

function TodayLogTab() {
  const [data, setData] = useState<DailySummary | null>(null)
  const [loadError, setLoadError] = useState(false)

  useEffect(() => {
    let cancelled = false
    getDailySummary(today())
      .then((summary) => {
        if (cancelled) return
        setData(summary)
        setLoadError(false)
      })
      .catch(() => {
        if (cancelled) return
        setData(null)
        setLoadError(true)
      })

    return () => {
      cancelled = true
    }
  }, [])

  if (!data && !loadError) return <p className="text-gray-400 text-sm py-4 text-center">Loading...</p>
  if (loadError) return <p className="text-red-400 text-sm py-4 text-center">Failed to load today&apos;s log.</p>
  if (!data || data.meals.length === 0)
    return <p className="text-gray-400 text-sm py-4 text-center">No meals logged today.</p>

  return (
    <div className="overflow-y-auto flex-1 space-y-3">
      <div className="grid grid-cols-4 gap-2">
        {MACRO_KEYS.map((m) => (
          <div key={m} className="bg-white rounded-lg border border-gray-200 p-2 text-center">
            <p className="text-xs text-gray-500">{MACRO_LABELS[m]}</p>
            <p className="text-sm font-semibold text-gray-900">
              {data[`total_${m}` as keyof DailySummary] as number}
              <span className="text-xs font-normal text-gray-400"> {MACRO_UNITS[m]}</span>
            </p>
          </div>
        ))}
      </div>
      {data.meals.map((meal) => (
        <div key={meal.id} className="bg-white rounded-lg border border-gray-200 p-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-sm font-medium text-gray-900 capitalize">{meal.meal_type}</span>
            <span className="text-xs text-gray-500">
              {meal.total_calories} kcal · P:{meal.total_protein}g F:{meal.total_fat}g C:{meal.total_carbs}g
            </span>
          </div>
          {meal.notes && <p className="text-xs text-gray-400 mb-1">{meal.notes}</p>}
          <div className="space-y-0.5">
            {meal.items.map((item, i) => (
              <div key={i} className="flex justify-between text-xs text-gray-600">
                <span>{item.name}</span>
                <span className="text-gray-400">{item.grams}g · {item.calories} cal</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

export default function MealLogPage() {
  const [tab, setTab] = useState<'chat' | 'log'>('chat')
  const [logRefresh, setLogRefresh] = useState(0)
  const initial = loadChatState()
  const [messages, setMessages] = useState<MessageBubble[]>(initial.messages)
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [importingImage, setImportingImage] = useState(false)
  const [listening, setListening] = useState(false)
  const [speechError, setSpeechError] = useState<string | null>(null)
  const [saved, setSaved] = useState(initial.saved)
  const bottomRef = useRef<HTMLDivElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const speechRecognitionAvailable = getSpeechRecognitionConstructor() !== null

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  useEffect(() => {
    saveChatState(messages, saved)
  }, [messages, saved])

  useEffect(() => {
    return () => {
      recognitionRef.current?.abort()
      recognitionRef.current = null
    }
  }, [])

  useEffect(() => {
    if (tab !== 'chat' || loading || importingImage || saved) {
      recognitionRef.current?.stop()
    }
  }, [importingImage, loading, saved, tab])

  const handleSend = async (overrideContent?: string) => {
    const userContent = overrideContent ?? input.trim()
    if (!userContent || loading || saved) return
    if (!overrideContent) setInput('')

    const newMessages: MessageBubble[] = [
      ...messages,
      { role: 'user', content: userContent },
    ]
    setMessages(newMessages)
    setLoading(true)

    try {
      const apiHistory: ChatMessage[] = newMessages.map((m) => ({
        role: m.role,
        content: m.content,
      }))
      const resp: ChatResponse = await chatMeal(apiHistory)

      const assistantBubble: MessageBubble = {
        role: 'assistant',
        content: resp.message,
        proposedItems: resp.proposed_items ?? undefined,
        savedMeal: resp.saved_meal ?? undefined,
        editMealId: resp.edit_meal_id ?? undefined,
      }
      setMessages([...newMessages, assistantBubble])
      if (resp.saved_meal) {
        setSaved(true)
        setLogRefresh((n) => n + 1)
      } else if (resp.data_changed) {
        setLogRefresh((n) => n + 1)
      }
    } catch (err) {
      setMessages([...newMessages, {
        role: 'assistant',
        content: err instanceof Error ? err.message : 'Something went wrong. Please try again.',
      }])
    }
    setLoading(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleNewMeal = () => {
    recognitionRef.current?.stop()
    setMessages([])
    setInput('')
    setSaved(false)
    setSpeechError(null)
    sessionStorage.removeItem(CHAT_STORAGE_KEY)
    sessionStorage.removeItem(CHAT_SAVED_KEY)
  }

  const handleImportImage = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const target = e.target
    const file = target.files?.[0]
    target.value = ''
    if (!file || loading || importingImage || saved) return

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

  const handleToggleSpeechInput = () => {
    if (saved || loading || importingImage) return

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
    // Height formula: full dynamic viewport minus top nav (+ its safe-area-top padding) and
    // main element's top/bottom padding (bottom includes the mobile tab bar + safe-area-bottom).
    // On desktop the safe area vars resolve to 0px so this collapses to calc(100dvh - 8rem).
    <div
      className="flex flex-col"
      style={{ height: 'calc(100dvh - var(--safe-top) - var(--safe-bottom) - 8rem)' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="flex gap-1 rounded-lg bg-gray-100 p-0.5">
          <button
            onClick={() => setTab('chat')}
            className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              tab === 'chat' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Log Meal
          </button>
          <button
            onClick={() => { setTab('log'); setLogRefresh((n) => n + 1) }}
            className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              tab === 'log' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            Today's Log
          </button>
        </div>
        {saved && tab === 'chat' && (
          <button
            onClick={handleNewMeal}
            className="px-3 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 active:bg-blue-800"
          >
            New Entry
          </button>
        )}
      </div>

      {/* Today's Log tab */}
      {tab === 'log' && <TodayLogTab key={logRefresh} />}

      {/* Messages */}
      {tab === 'chat' && (
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
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[90%] md:max-w-[80%] rounded-lg px-4 py-2 ${
                  msg.role === 'user'
                    ? 'bg-blue-600 text-white'
                    : 'bg-white border border-gray-200 text-gray-700'
                }`}
              >
                <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
                {msg.proposedItems && msg.proposedItems.length > 0 && (
                  <ProposedItemsCard
                    items={msg.proposedItems}
                    onConfirm={() => handleSend('Yes, save it')}
                    confirmed={saved || i < messages.length - 1}
                    isEdit={!!msg.editMealId}
                  />
                )}
                {msg.savedMeal && <SavedMealCard meal={msg.savedMeal} isEdit={!!msg.editMealId} />}
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="bg-white border border-gray-200 rounded-lg px-4 py-2">
                <p className="text-sm text-gray-400">Thinking...</p>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      )}

      {tab === 'chat' && (
        <>
          <div className="flex gap-2">
            <input
              ref={cameraInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              onChange={handleImportImage}
              className="hidden"
              disabled={loading || saved || importingImage}
            />
            <button
              type="button"
              onClick={() => cameraInputRef.current?.click()}
              disabled={loading || saved || importingImage}
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
              disabled={(!speechRecognitionAvailable && !listening) || loading || saved || importingImage}
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
              placeholder={saved ? 'Meal saved — tap New Meal to log another' : 'Describe what you ate...'}
              className="flex-1 px-3 py-2.5 border border-gray-300 rounded-md text-sm disabled:bg-gray-50"
              disabled={loading || saved || importingImage}
            />
            <button
              onClick={() => handleSend()}
              disabled={loading || importingImage || !input.trim() || saved}
              className="px-4 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 active:bg-blue-800 disabled:opacity-50"
            >
              {importingImage ? 'Importing...' : 'Send'}
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
        </>
      )}
    </div>
  )
}

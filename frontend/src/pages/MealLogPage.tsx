import { useEffect, useRef, useState } from 'react'
import {
  chatMeal,
  MACRO_KEYS, MACRO_LABELS, MACRO_UNITS,
  type ChatMessage, type ChatProposedItem, type ChatResponse, type Meal, type Macros,
} from '../api'

function today() {
  return new Date().toISOString().split('T')[0]
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
            <span className={item.food_id === null ? 'text-gray-400 italic' : 'text-gray-700'}>
              {item.name}
              {item.food_id === null && ' (not in database)'}
            </span>
            <span className="text-gray-500 whitespace-nowrap">
              {item.amount_grams}g
              {item.food_id !== null && ` · ${Math.round(computeItemMacro(item, 'calories'))} cal`}
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

export default function MealLogPage() {
  const [messages, setMessages] = useState<MessageBubble[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [date, setDate] = useState(today())
  const [mealType, setMealType] = useState('lunch')
  const [notes, setNotes] = useState('')
  const [saved, setSaved] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

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
      const resp: ChatResponse = await chatMeal(apiHistory, date, mealType, notes || undefined)

      const assistantBubble: MessageBubble = {
        role: 'assistant',
        content: resp.message,
        proposedItems: resp.proposed_items ?? undefined,
        savedMeal: resp.saved_meal ?? undefined,
        editMealId: resp.edit_meal_id ?? undefined,
      }
      setMessages([...newMessages, assistantBubble])
      if (resp.saved_meal) setSaved(true)
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
    setMessages([])
    setInput('')
    setSaved(false)
  }

  return (
    // Height formula: full dynamic viewport minus top nav (+ its safe-area-top padding) and
    // main element's top/bottom padding (bottom includes the mobile tab bar + safe-area-bottom).
    // On desktop the safe area vars resolve to 0px so this collapses to calc(100dvh - 8rem).
    <div
      className="flex flex-col"
      style={{ height: 'calc(100dvh - var(--safe-top) - var(--safe-bottom) - 8rem)' }}
    >
      {/* Header — stacks vertically on mobile */}
      <div className="flex flex-col gap-2 mb-3">
        {/* Row 1: title + New Meal button */}
        <div className="flex items-center justify-between gap-2">
          <h1 className="text-xl font-semibold text-gray-900">Log Meal</h1>
          {saved && (
            <button
              onClick={handleNewMeal}
              className="px-3 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 active:bg-blue-800"
            >
              New Meal
            </button>
          )}
        </div>

        {/* Row 2: date + meal type side-by-side */}
        <div className="flex gap-2">
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="flex-1 min-w-0 px-2 py-2.5 border border-gray-300 rounded-md text-sm"
          />
          <select
            value={mealType}
            onChange={(e) => setMealType(e.target.value)}
            className="flex-1 min-w-0 px-2 py-2.5 border border-gray-300 rounded-md text-sm"
          >
            <option value="breakfast">Breakfast</option>
            <option value="lunch">Lunch</option>
            <option value="dinner">Dinner</option>
            <option value="snack">Snack</option>
          </select>
        </div>

        {/* Row 3: notes full width */}
        <input
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Notes (optional)"
          className="w-full px-2 py-2.5 border border-gray-300 rounded-md text-sm"
        />
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-3 mb-3 pr-1">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center text-gray-400 max-w-sm px-4">
              <p className="text-lg mb-2">Describe what you ate</p>
              <p className="text-sm">
                e.g. "ham sandwich with Oroweat bread, 50g Hillshire Farm ham, and Kirkland cheddar"
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

      {/* Input */}
      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={saved ? 'Meal saved — tap New Meal to log another' : 'Describe what you ate...'}
          className="flex-1 px-3 py-2.5 border border-gray-300 rounded-md text-sm disabled:bg-gray-50"
          disabled={loading || saved}
        />
        <button
          onClick={() => handleSend()}
          disabled={loading || !input.trim() || saved}
          className="px-4 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 active:bg-blue-800 disabled:opacity-50"
        >
          Send
        </button>
      </div>
    </div>
  )
}

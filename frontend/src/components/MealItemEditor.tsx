import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  ChatProposedItem, Food, Macros, Meal, MealItem,
} from '../api'
import {
  addMealItem, createMeal, deleteMealItem, getFoods,
  MACRO_KEYS, MACRO_LABELS, MACRO_UNITS, updateMeal, updateMealItem,
} from '../api'

// ── Types ──────────────────────────────────────────────────────────

/** Internal row used for both propose and edit modes. */
interface EditorRow {
  key: string
  id?: number            // MealItem.id (edit mode only)
  food_id: number | null
  recipe_id?: number | null
  name: string
  amount_grams: number
  rate_per_gram: Record<keyof Macros, number>
  group?: string
  source_recipe_id?: number
}

export interface MealItemEditorProps {
  mode: 'propose' | 'edit'
  // Propose mode
  items?: ChatProposedItem[]
  mealType?: string
  date?: string
  editMealId?: number | null
  onSaved?: (meal: Meal) => void
  // Edit mode
  meal?: Meal
  onMealChanged?: () => void
  compact?: boolean
}

// ── Helpers ────────────────────────────────────────────────────────

function rateFromProposed(item: ChatProposedItem): Record<keyof Macros, number> {
  const r = {} as Record<keyof Macros, number>
  const sg = item.serving_size_grams || 100
  for (const m of MACRO_KEYS) r[m] = (item.macros_per_serving[m] ?? 0) / sg
  return r
}

function rateFromMealItem(item: MealItem): Record<keyof Macros, number> {
  const r = {} as Record<keyof Macros, number>
  const g = item.grams || 1
  for (const m of MACRO_KEYS) r[m] = (item[m] ?? 0) / g
  return r
}

function scaledMacro(
  rate: Record<keyof Macros, number>, grams: number, macro: keyof Macros,
): number {
  return Math.round(rate[macro] * grams * 10) / 10
}

function totalMacros(
  rows: EditorRow[],
): Record<keyof Macros, number> {
  const t = {} as Record<keyof Macros, number>
  for (const m of MACRO_KEYS) {
    t[m] = rows.reduce((s, r) => s + scaledMacro(r.rate_per_gram, r.amount_grams, m), 0)
  }
  return t
}

function proposedToRows(items: ChatProposedItem[]): EditorRow[] {
  return items.map((item, i) => ({
    key: `p-${item.food_id ?? item.recipe_id ?? i}-${i}`,
    food_id: item.food_id,
    recipe_id: item.recipe_id,
    name: item.name,
    amount_grams: item.amount_grams,
    rate_per_gram: rateFromProposed(item),
    group: item.group,
    source_recipe_id: item.source_recipe_id,
  }))
}

function mealToRows(meal: Meal): EditorRow[] {
  return meal.items.map((item) => ({
    key: `m-${item.id}`,
    id: item.id,
    food_id: item.food_id,
    recipe_id: item.recipe_id,
    name: item.name,
    amount_grams: item.grams,
    rate_per_gram: rateFromMealItem(item),
  }))
}

// ── Debounce hook ──────────────────────────────────────────────────

function useDebouncedCallback<T extends (...args: never[]) => void>(
  fn: T, delay: number,
) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const latestFn = useRef(fn)
  useEffect(() => { latestFn.current = fn })
  return useCallback((...args: Parameters<T>) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => latestFn.current(...args), delay)
  }, [delay]) as (...args: Parameters<T>) => void
}

// ── FoodPicker ─────────────────────────────────────────────────────

function FoodPicker(
  { onSelect, onCancel }: {
    onSelect: (food: Food) => void
    onCancel: () => void
  },
) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Food[]>([])
  const [loading, setLoading] = useState(false)

  const search = useDebouncedCallback(async (q: string) => {
    if (q.length < 2) { setResults([]); return }
    setLoading(true)
    try { setResults(await getFoods(q)) }
    catch { setResults([]) }
    finally { setLoading(false) }
  }, 300)

  return (
    <div className="mt-2 border border-blue-200 rounded-lg bg-blue-50 p-2">
      <div className="flex items-center gap-2">
        <input
          type="text"
          className="flex-1 text-sm border border-gray-300 rounded px-2 py-1"
          placeholder="Search foods…"
          value={query}
          onChange={(e) => { setQuery(e.target.value); search(e.target.value) }}
          autoFocus
        />
        <button
          type="button"
          className="text-xs text-gray-500 hover:text-gray-700"
          onClick={onCancel}
        >✕</button>
      </div>
      {loading && <p className="text-xs text-gray-400 mt-1">Searching…</p>}
      {results.length > 0 && (
        <div className="mt-1 max-h-36 overflow-y-auto space-y-0.5">
          {results.map((f) => (
            <button
              key={f.id}
              type="button"
              className="w-full text-left text-sm px-2 py-1 rounded
                         hover:bg-blue-100 text-gray-700"
              onClick={() => onSelect(f)}
            >
              {f.name}
              <span className="text-xs text-gray-400 ml-1">
                ({f.serving_size_grams}g serving)
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Inline number input ────────────────────────────────────────────

function GramsInput(
  { value, onChange }: { value: number; onChange: (v: number) => void },
) {
  const [text, setText] = useState(String(Math.round(value)))

  useEffect(() => { setText(String(Math.round(value))) }, [value])

  const commit = () => {
    const n = parseFloat(text)
    if (!isNaN(n) && n >= 0) onChange(n)
    else setText(String(Math.round(value)))
  }

  return (
    <div className="flex items-center gap-0.5">
      <input
        type="text"
        inputMode="decimal"
        className="w-14 text-right text-sm border border-gray-300 rounded
                   px-1 py-0.5 focus:border-blue-400 focus:ring-1
                   focus:ring-blue-200 outline-none"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') commit() }}
      />
      <span className="text-xs text-gray-400">g</span>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────

export default function MealItemEditor({
  mode, items, mealType, date, editMealId,
  onSaved, meal, onMealChanged, compact,
}: MealItemEditorProps) {
  const [rows, setRows] = useState<EditorRow[]>([])
  const [saving, setSaving] = useState(false)
  const [showPicker, setShowPicker] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedLabel, setSavedLabel] = useState<string | null>(null)

  // Initialize rows from props
  useEffect(() => {
    if (mode === 'propose' && items) setRows(proposedToRows(items))
    else if (mode === 'edit' && meal) setRows(mealToRows(meal))
  }, [mode, items, meal])

  // ── Propose mode: update local amount ──
  const updateProposedAmount = useCallback((key: string, grams: number) => {
    setRows((prev) => prev.map(
      (r) => r.key === key ? { ...r, amount_grams: grams } : r,
    ))
  }, [])

  // ── Edit mode: update amount + debounced PATCH ──
  const patchItem = useDebouncedCallback(
    async (itemId: number, grams: number) => {
      try {
        const updated = await updateMealItem(itemId, { amount_grams: grams })
        if (onMealChanged) onMealChanged()
        setRows(mealToRows(updated))
      } catch {
        setError('Failed to update item')
      }
    }, 600,
  )

  const updateEditAmount = useCallback((key: string, grams: number) => {
    setRows((prev) => prev.map(
      (r) => r.key === key ? { ...r, amount_grams: grams } : r,
    ))
    const row = rows.find((r) => r.key === key)
    if (row?.id) patchItem(row.id, grams)
  }, [rows, patchItem])

  const handleAmountChange = mode === 'propose' ? updateProposedAmount : updateEditAmount

  // ── Delete item ──
  const handleDelete = useCallback(async (key: string) => {
    if (mode === 'edit') {
      const row = rows.find((r) => r.key === key)
      if (!row?.id) return
      try {
        await deleteMealItem(row.id)
        if (onMealChanged) onMealChanged()
      } catch {
        setError('Failed to delete item')
        return
      }
    }
    setRows((prev) => prev.filter((r) => r.key !== key))
  }, [mode, rows, onMealChanged])

  // ── Add item ──
  const handleAddFood = useCallback(async (food: Food) => {
    setShowPicker(false)
    const rate = {} as Record<keyof Macros, number>
    const sg = food.serving_size_grams || 100
    for (const m of MACRO_KEYS) {
      const key = `${m}_per_serving` as keyof Food
      rate[m] = (food[key] as number ?? 0) / sg
    }
    if (mode === 'edit' && meal) {
      try {
        const updated = await addMealItem(meal.id, {
          food_id: food.id, amount_grams: food.serving_size_grams,
        })
        if (onMealChanged) onMealChanged()
        setRows(mealToRows(updated))
      } catch {
        setError('Failed to add item')
      }
    } else {
      setRows((prev) => [...prev, {
        key: `new-${Date.now()}`,
        food_id: food.id,
        name: food.name,
        amount_grams: food.serving_size_grams,
        rate_per_gram: rate,
      }])
    }
  }, [mode, meal, onMealChanged])

  // ── Save (propose mode) ──
  const handleSave = useCallback(async () => {
    if (!date || !mealType) {
      setError('Missing date or meal type')
      return
    }
    const saveable = rows.filter((r) => r.food_id !== null)
    if (saveable.length === 0) {
      setError('No valid items to save')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const payload = {
        date,
        meal_type: mealType,
        items: saveable.map((r) => ({
          food_id: r.food_id ?? undefined,
          recipe_id: r.recipe_id ?? undefined,
          amount_grams: r.amount_grams,
        })),
      }
      const saved = editMealId
        ? await updateMeal(editMealId, payload)
        : await createMeal(payload)
      setSavedLabel(editMealId ? 'Meal updated!' : 'Meal saved!')
      if (onSaved) onSaved(saved)
    } catch {
      setError('Failed to save meal')
    } finally {
      setSaving(false)
    }
  }, [rows, date, mealType, editMealId, onSaved])

  const totals = totalMacros(rows)

  // ── Group rows by recipe name for visual grouping ──
  const groups: { label: string | null; rows: EditorRow[] }[] = []
  let currentGroup: string | null = null
  for (const row of rows) {
    const g = row.group ?? null
    if (g !== currentGroup) {
      groups.push({ label: g, rows: [row] })
      currentGroup = g
    } else {
      groups[groups.length - 1].rows.push(row)
    }
  }

  if (savedLabel) {
    return (
      <div className="mt-2 bg-green-50 border border-green-200 rounded-lg p-3">
        <p className="text-sm font-medium text-green-800">{savedLabel}</p>
        <p className="text-xs text-green-600 mt-1">
          P: {Math.round(totals.protein)}g
          {' · '}C: {Math.round(totals.carbs)}g
          {' · '}F: {Math.round(totals.fat)}g
          {' · '}{Math.round(totals.calories)} kcal
        </p>
      </div>
    )
  }

  return (
    <div className={`mt-2 rounded-lg border p-3 ${
      mode === 'propose'
        ? 'bg-gray-50 border-gray-200'
        : 'bg-white border-gray-200'
    }`}>
      {error && (
        <p className="text-xs text-red-600 mb-2">{error}</p>
      )}

      {/* Item rows */}
      <div className="space-y-1">
        {groups.map((group, gi) => (
          <div key={gi}>
            {group.label && (
              <p className="text-xs font-medium text-indigo-600 mt-1 mb-0.5">
                📋 {group.label}
              </p>
            )}
            {group.rows.map((row) => (
              <div
                key={row.key}
                className={`flex items-center gap-2 text-sm ${
                  group.label ? 'pl-4' : ''
                }`}
              >
                <span className="flex-1 text-gray-700 truncate min-w-0">
                  {row.name}
                  {row.food_id === null && !row.recipe_id && (
                    <span className="text-gray-400 italic text-xs ml-1">
                      (not in DB)
                    </span>
                  )}
                </span>
                <GramsInput
                  value={row.amount_grams}
                  onChange={(v) => handleAmountChange(row.key, v)}
                />
                {!compact && (
                  <span className="text-xs text-gray-400 w-14 text-right whitespace-nowrap">
                    {Math.round(scaledMacro(
                      row.rate_per_gram, row.amount_grams, 'calories',
                    ))} cal
                  </span>
                )}
                <button
                  type="button"
                  className="text-gray-300 hover:text-red-500 text-sm
                             leading-none px-0.5"
                  onClick={() => handleDelete(row.key)}
                  title="Remove"
                >✕</button>
              </div>
            ))}
          </div>
        ))}
      </div>

      {/* Add item */}
      {showPicker ? (
        <FoodPicker
          onSelect={handleAddFood}
          onCancel={() => setShowPicker(false)}
        />
      ) : (
        <button
          type="button"
          className="mt-2 text-xs text-blue-600 hover:text-blue-800"
          onClick={() => setShowPicker(true)}
        >+ Add food</button>
      )}

      {/* Macro totals */}
      <div className={`border-t border-gray-200 mt-2 pt-2 flex flex-wrap
                        gap-x-3 gap-y-0.5 text-xs text-gray-500`}>
        {MACRO_KEYS.map((m) => (
          <span key={m}>
            {MACRO_LABELS[m]}:{' '}
            <strong>{Math.round(totals[m])}</strong>
            {MACRO_UNITS[m]}
          </span>
        ))}
      </div>

      {/* Action button (propose mode only) */}
      {mode === 'propose' && (
        <button
          type="button"
          disabled={saving || rows.length === 0}
          onClick={handleSave}
          className="mt-3 w-full py-2.5 bg-green-600 text-white text-sm
                     font-medium rounded-md hover:bg-green-700
                     active:bg-green-800 disabled:opacity-50
                     disabled:cursor-not-allowed"
        >
          {saving
            ? 'Saving…'
            : editMealId
              ? 'Update Meal'
              : 'Save Meal'}
        </button>
      )}
    </div>
  )
}

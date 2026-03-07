import { useEffect, useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  getDailySummary, deleteMeal, updateMeal, getFoods, getRecipes,
  MACRO_KEYS, MACRO_LABELS, MACRO_UNITS,
  type DailySummary, type Meal, type Food, type Recipe,
} from '../api'

function today() {
  return new Date().toISOString().split('T')[0]
}

const MEAL_TYPES = ['breakfast', 'lunch', 'dinner', 'snack']

interface EditItem {
  food_id: number | null
  recipe_id: number | null
  name: string
  amount_grams: number
}

export default function DashboardPage() {
  const [date, setDate] = useState(today())
  const [data, setData] = useState<DailySummary | null>(null)
  const [loading, setLoading] = useState(true)

  // Edit state
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editDate, setEditDate] = useState('')
  const [editMealType, setEditMealType] = useState('')
  const [editNotes, setEditNotes] = useState('')
  const [editItems, setEditItems] = useState<EditItem[]>([])
  const [saving, setSaving] = useState(false)

  // Add item search
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<{ id: number; name: string; type: 'food' | 'recipe' }[]>([])
  const [showResults, setShowResults] = useState(false)
  const [addGrams, setAddGrams] = useState(100)
  const searchRef = useRef<HTMLDivElement>(null)

  const load = async () => {
    setLoading(true)
    try {
      setData(await getDailySummary(date))
    } catch { /* redirect handled by api */ }
    setLoading(false)
  }

  useEffect(() => { load() }, [date])

  // Close search dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowResults(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const handleDelete = async (id: number) => {
    await deleteMeal(id)
    load()
  }

  const startEdit = (meal: Meal) => {
    setEditingId(meal.id)
    setEditDate(meal.date)
    setEditMealType(meal.meal_type)
    setEditNotes(meal.notes || '')
    setEditItems(meal.items.map((item) => ({
      food_id: item.food_id,
      recipe_id: item.recipe_id,
      name: item.name,
      amount_grams: item.grams,
    })))
    setSearchQuery('')
    setSearchResults([])
    setShowResults(false)
  }

  const cancelEdit = () => {
    setEditingId(null)
  }

  const saveEdit = async () => {
    if (editingId === null) return
    setSaving(true)
    try {
      await updateMeal(editingId, {
        date: editDate || undefined,
        meal_type: editMealType,
        notes: editNotes || undefined,
        items: editItems.map((item) => ({
          food_id: item.food_id ?? undefined,
          recipe_id: item.recipe_id ?? undefined,
          amount_grams: item.amount_grams,
        })),
      })
      setEditingId(null)
      load()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to save')
    }
    setSaving(false)
  }

  const removeItem = (index: number) => {
    setEditItems((prev) => prev.filter((_, i) => i !== index))
  }

  const updateItemGrams = (index: number, grams: number) => {
    setEditItems((prev) => prev.map((item, i) => i === index ? { ...item, amount_grams: grams } : item))
  }

  const handleSearch = async (query: string) => {
    setSearchQuery(query)
    if (query.length < 2) {
      setSearchResults([])
      setShowResults(false)
      return
    }
    try {
      const [foods, recipes] = await Promise.all([getFoods(query), getRecipes()])
      const results: { id: number; name: string; type: 'food' | 'recipe' }[] = [
        ...foods.map((f: Food) => ({ id: f.id, name: f.brand ? `${f.name} (${f.brand})` : f.name, type: 'food' as const })),
        ...recipes
          .filter((r: Recipe) => r.name.toLowerCase().includes(query.toLowerCase()))
          .map((r: Recipe) => ({ id: r.id, name: r.name, type: 'recipe' as const })),
      ]
      setSearchResults(results.slice(0, 10))
      setShowResults(true)
    } catch { /* ignore */ }
  }

  const addItem = (result: { id: number; name: string; type: 'food' | 'recipe' }) => {
    setEditItems((prev) => [
      ...prev,
      {
        food_id: result.type === 'food' ? result.id : null,
        recipe_id: result.type === 'recipe' ? result.id : null,
        name: result.name,
        amount_grams: addGrams,
      },
    ])
    setSearchQuery('')
    setSearchResults([])
    setShowResults(false)
    setAddGrams(100)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-gray-900">Daily Summary</h1>
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="px-3 py-1.5 border border-gray-300 rounded-md text-sm"
        />
      </div>

      {loading ? (
        <p className="text-gray-500">Loading...</p>
      ) : data ? (
        <>
          {/* Macro totals */}
          <div className="grid grid-cols-4 gap-3 mb-6">
            {MACRO_KEYS.map((m) => (
              <div key={m} className="bg-white p-3 rounded-lg border border-gray-200">
                <p className="text-xs text-gray-500">{MACRO_LABELS[m]}</p>
                <p className="text-xl font-semibold text-gray-900">
                  {data[`total_${m}` as keyof DailySummary] as number}{' '}
                  <span className="text-xs font-normal text-gray-400">{MACRO_UNITS[m]}</span>
                </p>
              </div>
            ))}
          </div>

          {/* Meals */}
          {data.meals.length === 0 ? (
            <p className="text-gray-500">
              No meals logged.{' '}
              <Link to="/log" className="text-blue-600 hover:underline">Log a meal</Link>
            </p>
          ) : (
            <div className="space-y-4">
              {data.meals.map((meal) => (
                <div key={meal.id} className="bg-white p-4 rounded-lg border border-gray-200">
                  {editingId === meal.id ? (
                    /* ---- Edit mode ---- */
                    <div>
                      <div className="flex items-center gap-3 mb-3 flex-wrap">
                        <input
                          type="date"
                          value={editDate}
                          onChange={(e) => setEditDate(e.target.value)}
                          className="px-2 py-1 border border-gray-300 rounded text-sm"
                        />
                        <select
                          value={editMealType}
                          onChange={(e) => setEditMealType(e.target.value)}
                          className="px-2 py-1 border border-gray-300 rounded text-sm"
                        >
                          {MEAL_TYPES.map((t) => (
                            <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                          ))}
                        </select>
                        <input
                          type="text"
                          value={editNotes}
                          onChange={(e) => setEditNotes(e.target.value)}
                          placeholder="Notes (optional)"
                          className="flex-1 px-2 py-1 border border-gray-300 rounded text-sm"
                        />
                      </div>

                      {/* Editable items */}
                      <div className="space-y-2 mb-3">
                        {editItems.map((item, i) => (
                          <div key={i} className="flex items-center gap-2 text-sm">
                            <span className="flex-1 text-gray-700">{item.name}</span>
                            <input
                              type="number"
                              value={item.amount_grams}
                              onChange={(e) => updateItemGrams(i, parseFloat(e.target.value) || 0)}
                              className="w-20 px-2 py-1 border border-gray-300 rounded text-right"
                              min={0}
                              step={1}
                            />
                            <span className="text-gray-400 text-xs">g</span>
                            <button
                              onClick={() => removeItem(i)}
                              className="text-red-400 hover:text-red-600 text-xs px-1"
                            >
                              Remove
                            </button>
                          </div>
                        ))}
                      </div>

                      {/* Add item search */}
                      <div ref={searchRef} className="relative mb-3">
                        <div className="flex items-center gap-2">
                          <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => handleSearch(e.target.value)}
                            onFocus={() => searchResults.length > 0 && setShowResults(true)}
                            placeholder="Search foods or recipes to add..."
                            className="flex-1 px-2 py-1 border border-gray-300 rounded text-sm"
                          />
                          <input
                            type="number"
                            value={addGrams}
                            onChange={(e) => setAddGrams(parseFloat(e.target.value) || 0)}
                            className="w-20 px-2 py-1 border border-gray-300 rounded text-sm text-right"
                            min={0}
                            step={1}
                          />
                          <span className="text-gray-400 text-xs">g</span>
                        </div>
                        {showResults && searchResults.length > 0 && (
                          <div className="absolute z-10 left-0 right-20 mt-1 bg-white border border-gray-200 rounded-md shadow-lg max-h-48 overflow-y-auto">
                            {searchResults.map((r) => (
                              <button
                                key={`${r.type}-${r.id}`}
                                onClick={() => addItem(r)}
                                className="block w-full text-left px-3 py-2 text-sm hover:bg-gray-50"
                              >
                                {r.name}
                                <span className="text-xs text-gray-400 ml-1">({r.type})</span>
                              </button>
                            ))}
                          </div>
                        )}
                      </div>

                      {/* Save / Cancel */}
                      <div className="flex gap-2">
                        <button
                          onClick={saveEdit}
                          disabled={saving || editItems.length === 0}
                          className="px-3 py-1 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50"
                        >
                          {saving ? 'Saving...' : 'Save'}
                        </button>
                        <button
                          onClick={cancelEdit}
                          className="px-3 py-1 border border-gray-300 text-sm rounded hover:bg-gray-50"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    /* ---- Display mode ---- */
                    <>
                      <div className="flex items-center justify-between mb-2">
                        <h3 className="font-medium text-gray-900 capitalize">{meal.meal_type}</h3>
                        <div className="flex items-center gap-3">
                          <span className="text-sm text-gray-500">
                            {meal.total_calories} kcal &middot; P:{meal.total_protein}g F:{meal.total_fat}g C:{meal.total_carbs}g
                          </span>
                          <button
                            onClick={() => startEdit(meal)}
                            className="text-xs text-blue-500 hover:text-blue-700"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => handleDelete(meal.id)}
                            className="text-xs text-red-500 hover:text-red-700"
                          >
                            Delete
                          </button>
                        </div>
                      </div>
                      {meal.notes && <p className="text-sm text-gray-500 mb-2">{meal.notes}</p>}
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="text-gray-400 text-xs">
                              <th className="text-left font-normal pb-1">Item</th>
                              <th className="text-right font-normal pb-1">g</th>
                              {MACRO_KEYS.map((m) => (
                                <th key={m} className="text-right font-normal pb-1">{MACRO_LABELS[m]}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {meal.items.map((item, i) => (
                              <tr key={i} className="text-gray-700">
                                <td>{item.name}</td>
                                <td className="text-right">{item.grams}</td>
                                {MACRO_KEYS.map((m) => (
                                  <td key={m} className="text-right">{item[m as keyof typeof item]}</td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}

          <Link
            to="/log"
            className="inline-block mt-4 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700"
          >
            Log Meal
          </Link>
        </>
      ) : null}
    </div>
  )
}

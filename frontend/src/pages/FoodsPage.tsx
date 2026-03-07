import { useEffect, useState, type ChangeEvent, type FormEvent } from 'react'
import { getFoods, createFood, updateFood, deleteFood, foodMacroPerServing, importFoodLabel, MACRO_KEYS, MACRO_LABELS, type Food, type FoodImportResult } from '../api'
import ScrollablePage from '../components/ScrollablePage'

const FOOD_MACRO_FIELDS = MACRO_KEYS.map((m) => `${m}_per_serving` as const)

type FormState = Record<string, string>

function emptyForm(): FormState {
  const f: FormState = { name: '', brand: '', serving_size_grams: '100' }
  for (const field of FOOD_MACRO_FIELDS) f[field] = ''
  return f
}

function foodToForm(food: Food): FormState {
  const f: FormState = { name: food.name, brand: food.brand ?? '', serving_size_grams: String(food.serving_size_grams) }
  for (const field of FOOD_MACRO_FIELDS) f[field] = String(food[field as keyof Food] ?? '')
  return f
}

function importResultToForm(result: FoodImportResult): FormState {
  const f: FormState = {
    name: result.name,
    brand: result.brand ?? '',
    serving_size_grams: String(result.serving_size_grams),
  }
  for (const field of FOOD_MACRO_FIELDS) {
    f[field] = String(result[field as keyof FoodImportResult] ?? 0)
  }
  return f
}

export default function FoodsPage() {
  const [foods, setFoods] = useState<Food[]>([])
  const [search, setSearch] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm())
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)

  const load = async () => { setFoods(await getFoods(search || undefined)) }
  useEffect(() => {
    let cancelled = false
    getFoods(search || undefined)
      .then((nextFoods) => {
        if (!cancelled) setFoods(nextFoods)
      })
      .catch(() => {
        if (!cancelled) setFoods([])
      })

    return () => {
      cancelled = true
    }
  }, [search])

  const resetForm = () => { setForm(emptyForm()); setEditId(null); setShowForm(false); setImportError(null) }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    const data: Record<string, unknown> = {
      name: form.name,
      brand: form.brand || null,
      serving_size_grams: parseFloat(form.serving_size_grams) || 100,
    }
    for (const field of FOOD_MACRO_FIELDS) data[field] = parseFloat(form[field]) || 0
    if (editId) {
      await updateFood(editId, data as Partial<Food>)
    } else {
      await createFood(data as Omit<Food, 'id' | 'source'>)
    }
    resetForm()
    load()
  }

  const startEdit = (food: Food) => { setForm(foodToForm(food)); setEditId(food.id); setShowForm(true) }

  const handleImportImage = async (e: ChangeEvent<HTMLInputElement>) => {
    const target = e.target
    const file = target.files?.[0]
    target.value = ''
    if (!file) return

    setImportError(null)
    setImporting(true)
    try {
      const imported = await importFoodLabel(file)
      setForm(importResultToForm(imported))
      setEditId(null)
      setShowForm(true)
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Failed to import nutrition label')
    } finally {
      setImporting(false)
    }
  }

  return (
    <ScrollablePage>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-gray-900">Foods</h1>
        <div className="flex items-center gap-2">
          <label
            className={`px-4 py-2 bg-emerald-600 text-white text-sm font-medium rounded-md ${
              importing ? 'opacity-60 cursor-not-allowed' : 'hover:bg-emerald-700 cursor-pointer'
            }`}
          >
            <input
              type="file"
              accept="image/*"
              capture="environment"
              onChange={handleImportImage}
              className="sr-only"
              disabled={importing}
            />
            {importing ? 'Importing...' : 'Import Label'}
          </label>
          <button onClick={() => { resetForm(); setShowForm(!showForm) }}
            className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700">
            {showForm ? 'Cancel' : 'Add Food'}
          </button>
        </div>
      </div>

      {importError && (
        <div className="mb-4 px-3 py-2 rounded-md border border-red-200 bg-red-50 text-sm text-red-700">
          {importError}
        </div>
      )}

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-white p-4 rounded-lg border border-gray-200 mb-6">
          <div className="flex gap-3 mb-3">
            <input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="flex-1 px-3 py-2 border border-gray-300 rounded-md text-sm" required />
            <input placeholder="Brand" value={form.brand} onChange={(e) => setForm({ ...form, brand: e.target.value })}
              className="w-36 px-3 py-2 border border-gray-300 rounded-md text-sm" />
            <input placeholder="Serving (g)" type="number" step="any"
              value={form.serving_size_grams} onChange={(e) => setForm({ ...form, serving_size_grams: e.target.value })}
              className="w-28 px-3 py-2 border border-gray-300 rounded-md text-sm" required />
          </div>
          <div className="grid grid-cols-4 gap-2">
            {MACRO_KEYS.map((m) => {
              const field = `${m}_per_serving`
              return (
                <input key={field} placeholder={`${MACRO_LABELS[m]}/serv`} type="number" step="any"
                  value={form[field]} onChange={(e) => setForm({ ...form, [field]: e.target.value })}
                  className="px-3 py-2 border border-gray-300 rounded-md text-sm" />
              )
            })}
          </div>
          <button type="submit"
            className="mt-3 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700">
            {editId ? 'Update' : 'Add'}
          </button>
        </form>
      )}

      <input placeholder="Search foods..." value={search} onChange={(e) => setSearch(e.target.value)}
        className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm mb-4" />

      <div className="bg-white rounded-lg border border-gray-200 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-gray-500 text-xs">
              <th className="text-left font-medium px-3 py-2">Name</th>
              <th className="text-left font-medium px-3 py-2">Brand</th>
              <th className="text-right font-medium px-3 py-2">Serving</th>
              {MACRO_KEYS.map((m) => (
                <th key={m} className="text-right font-medium px-3 py-2">{MACRO_LABELS[m]}</th>
              ))}
              <th className="text-right font-medium px-3 py-2">Source</th>
              <th className="text-right font-medium px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {foods.map((food) => (
              <tr key={food.id} className="border-t border-gray-100 text-gray-700">
                <td className="px-3 py-2">{food.name}</td>
                <td className="px-3 py-2 text-gray-500">{food.brand ?? ''}</td>
                <td className="px-3 py-2 text-right">{food.serving_size_grams}g</td>
                {MACRO_KEYS.map((m) => (
                  <td key={m} className="px-3 py-2 text-right">
                    {foodMacroPerServing(food, m)}
                  </td>
                ))}
                <td className="px-3 py-2 text-right text-gray-400">{food.source}</td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  <button onClick={() => startEdit(food)} className="text-blue-500 hover:text-blue-700 mr-2">Edit</button>
                  <button onClick={async () => { await deleteFood(food.id); load() }} className="text-red-500 hover:text-red-700">Delete</button>
                </td>
              </tr>
            ))}
            {foods.length === 0 && (
              <tr><td colSpan={MACRO_KEYS.length + 5} className="px-3 py-8 text-center text-gray-400">No foods found</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </ScrollablePage>
  )
}

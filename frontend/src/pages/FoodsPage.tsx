import { useEffect, useState, type ChangeEvent, type FormEvent } from 'react'
import {
  getFoods, createFood, updateFood, deleteFood, foodMacroPerServing,
  importFoodLabel, mergeFoods, auditFoods, MACRO_KEYS, MACRO_LABELS,
  type Food, type FoodImportResult, type FoodAuditResponse, type FoodAuditSummary,
} from '../api'
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
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [mergeModal, setMergeModal] = useState<{ a: Food; b: Food } | null>(null)
  const [merging, setMerging] = useState(false)
  const [showAudit, setShowAudit] = useState(false)
  const [audit, setAudit] = useState<FoodAuditResponse | null>(null)
  const [auditLoading, setAuditLoading] = useState(false)

  const load = async () => { setFoods(await getFoods(search || undefined)) }
  const refreshAudit = async () => {
    setAuditLoading(true)
    try {
      setAudit(await auditFoods())
    } finally {
      setAuditLoading(false)
    }
  }

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

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const beginMerge = () => {
    if (selected.size !== 2) return
    const ids = Array.from(selected)
    const a = foods.find((f) => f.id === ids[0])
    const b = foods.find((f) => f.id === ids[1])
    if (a && b) setMergeModal({ a, b })
  }

  const confirmMerge = async (keep: Food, remove: Food) => {
    setMerging(true)
    try {
      const result = await mergeFoods(remove.id, keep.id)
      alert(
        `Merged "${remove.name}" into "${keep.name}".\n` +
        `Reassigned ${result.merged_meal_items} meal items, ` +
        `${result.merged_recipe_components} recipe components.`,
      )
      setSelected(new Set())
      setMergeModal(null)
      await load()
      if (showAudit) await refreshAudit()
    } catch (err) {
      alert(`Merge failed: ${err instanceof Error ? err.message : err}`)
    } finally {
      setMerging(false)
    }
  }

  const beginAuditMerge = (a: FoodAuditSummary, b: FoodAuditSummary) => {
    const fa = foods.find((f) => f.id === a.id)
    const fb = foods.find((f) => f.id === b.id)
    if (fa && fb) setMergeModal({ a: fa, b: fb })
  }

  return (
    <ScrollablePage>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-2">
        <h1 className="text-2xl font-semibold text-gray-900">Foods</h1>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={() => {
              const next = !showAudit
              setShowAudit(next)
              if (next && !audit) refreshAudit()
            }}
            className={`px-3 py-2 text-sm font-medium rounded-md border ${
              showAudit ? 'bg-amber-100 border-amber-300 text-amber-900' : 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50'
            }`}
          >
            {showAudit ? 'Hide Audit' : 'Audit'}
          </button>
          <button
            disabled={selected.size !== 2}
            onClick={beginMerge}
            className={`px-3 py-2 text-sm font-medium rounded-md ${
              selected.size === 2
                ? 'bg-amber-600 text-white hover:bg-amber-700'
                : 'bg-gray-200 text-gray-400 cursor-not-allowed'
            }`}
            title="Select exactly 2 foods to merge"
          >
            Merge ({selected.size})
          </button>
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

      {showAudit && (
        <div className="mb-6 bg-amber-50 border border-amber-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold text-amber-900">Food Audit</h2>
            <button
              onClick={refreshAudit}
              className="text-xs px-2 py-1 bg-white border border-amber-300 rounded hover:bg-amber-100"
            >
              {auditLoading ? 'Refreshing...' : 'Refresh'}
            </button>
          </div>
          {!audit ? (
            <p className="text-sm text-amber-800">{auditLoading ? 'Loading...' : 'No data'}</p>
          ) : (
            <div className="space-y-4 text-sm">
              <div>
                <h3 className="font-medium text-amber-900 mb-1">
                  Possible Duplicates ({audit.duplicate_groups.length} groups)
                </h3>
                {audit.duplicate_groups.length === 0 ? (
                  <p className="text-gray-600">None detected.</p>
                ) : (
                  <ul className="space-y-2">
                    {audit.duplicate_groups.map((group, i) => (
                      <li key={i} className="bg-white border border-amber-200 rounded p-2">
                        <div className="flex flex-wrap gap-2">
                          {group.foods.map((f) => (
                            <span
                              key={f.id}
                              className="px-2 py-0.5 bg-gray-100 rounded text-xs"
                              title={`${f.usage_count} uses (${f.meal_item_count} meal items + ${f.recipe_component_count} components)`}
                            >
                              {f.name}{f.brand ? ` · ${f.brand}` : ''} ({f.usage_count})
                            </span>
                          ))}
                        </div>
                        {group.foods.length === 2 && (
                          <button
                            onClick={() => beginAuditMerge(group.foods[0], group.foods[1])}
                            className="mt-2 text-xs px-2 py-1 bg-amber-600 text-white rounded hover:bg-amber-700"
                          >
                            Merge these two
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <h3 className="font-medium text-amber-900 mb-1">
                  Missing Macros ({audit.missing_macros.length})
                </h3>
                {audit.missing_macros.length === 0 ? (
                  <p className="text-gray-600">None.</p>
                ) : (
                  <ul className="text-xs flex flex-wrap gap-2">
                    {audit.missing_macros.slice(0, 30).map((f) => (
                      <li key={f.id} className="px-2 py-0.5 bg-white border border-amber-200 rounded">
                        {f.name}{f.brand ? ` · ${f.brand}` : ''}
                      </li>
                    ))}
                    {audit.missing_macros.length > 30 && (
                      <li className="text-gray-500">...and {audit.missing_macros.length - 30} more</li>
                    )}
                  </ul>
                )}
              </div>
              <div>
                <h3 className="font-medium text-amber-900 mb-1">
                  Unused ({audit.unused.length})
                </h3>
                {audit.unused.length === 0 ? (
                  <p className="text-gray-600">None.</p>
                ) : (
                  <ul className="text-xs flex flex-wrap gap-2">
                    {audit.unused.slice(0, 30).map((f) => (
                      <li key={f.id} className="px-2 py-0.5 bg-white border border-amber-200 rounded flex items-center gap-1">
                        <span>{f.name}{f.brand ? ` · ${f.brand}` : ''}</span>
                        <button
                          onClick={async () => {
                            if (confirm(`Delete unused food "${f.name}"?`)) {
                              await deleteFood(f.id)
                              await load()
                              await refreshAudit()
                            }
                          }}
                          className="text-red-500 hover:text-red-700"
                          title="Delete"
                        >
                          ×
                        </button>
                      </li>
                    ))}
                    {audit.unused.length > 30 && (
                      <li className="text-gray-500">...and {audit.unused.length - 30} more</li>
                    )}
                  </ul>
                )}
              </div>
            </div>
          )}
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
              <th className="text-center font-medium px-2 py-2 w-8"></th>
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
                <td className="px-2 py-2 text-center">
                  <input
                    type="checkbox"
                    checked={selected.has(food.id)}
                    onChange={() => toggleSelect(food.id)}
                  />
                </td>
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
              <tr><td colSpan={MACRO_KEYS.length + 6} className="px-3 py-8 text-center text-gray-400">No foods found</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {mergeModal && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-lg w-full p-5">
            <h2 className="text-lg font-semibold mb-2">Merge Foods</h2>
            <p className="text-sm text-gray-600 mb-4">
              Choose which food to keep. The other will be deleted, and all
              meal items, recipe components, and overrides referencing it
              will be reassigned to the kept food.
            </p>
            <div className="grid grid-cols-2 gap-3 mb-4">
              {[mergeModal.a, mergeModal.b].map((f, i) => (
                <button
                  key={f.id}
                  disabled={merging}
                  onClick={() => {
                    const other = i === 0 ? mergeModal.b : mergeModal.a
                    confirmMerge(f, other)
                  }}
                  className="border border-gray-300 rounded-md p-3 hover:border-blue-500 hover:bg-blue-50 text-left"
                >
                  <div className="font-medium text-sm">{f.name}</div>
                  {f.brand && <div className="text-xs text-gray-500">{f.brand}</div>}
                  <div className="text-xs text-gray-500 mt-1">
                    {f.calories_per_serving} cal / {f.serving_size_grams}g
                  </div>
                  <div className="text-xs text-blue-700 mt-2">Keep this →</div>
                </button>
              ))}
            </div>
            <button
              onClick={() => setMergeModal(null)}
              disabled={merging}
              className="px-3 py-1.5 text-sm bg-gray-200 rounded hover:bg-gray-300"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </ScrollablePage>
  )
}

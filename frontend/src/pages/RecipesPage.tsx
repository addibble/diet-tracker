import { useEffect, useState } from 'react'
import { getFoods, getRecipes, createRecipe, deleteRecipe, MACRO_KEYS, MACRO_LABELS, type Food, type Recipe } from '../api'

interface ComponentForm {
  food_id: string
  amount_grams: string
}

export default function RecipesPage() {
  const [recipes, setRecipes] = useState<Recipe[]>([])
  const [foods, setFoods] = useState<Food[]>([])
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [components, setComponents] = useState<ComponentForm[]>([{ food_id: '', amount_grams: '' }])
  const [expanded, setExpanded] = useState<number | null>(null)

  const load = async () => {
    const [r, f] = await Promise.all([getRecipes(), getFoods()])
    setRecipes(r)
    setFoods(f)
  }

  useEffect(() => { load() }, [])

  const addComponent = () => setComponents([...components, { food_id: '', amount_grams: '' }])
  const removeComponent = (i: number) => setComponents(components.filter((_, j) => j !== i))
  const updateComponent = (i: number, field: keyof ComponentForm, value: string) => {
    const updated = [...components]
    updated[i] = { ...updated[i], [field]: value }
    setComponents(updated)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    await createRecipe({
      name,
      components: components
        .filter((c) => c.food_id && c.amount_grams)
        .map((c) => ({ food_id: parseInt(c.food_id), amount_grams: parseFloat(c.amount_grams) })),
    })
    setName('')
    setComponents([{ food_id: '', amount_grams: '' }])
    setShowForm(false)
    load()
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-gray-900">Recipes</h1>
        <button onClick={() => setShowForm(!showForm)}
          className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700">
          {showForm ? 'Cancel' : 'New Recipe'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-white p-4 rounded-lg border border-gray-200 mb-6">
          <input placeholder="Recipe name" value={name} onChange={(e) => setName(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm mb-3" required />
          <p className="text-xs text-gray-500 mb-2">Components:</p>
          {components.map((comp, i) => (
            <div key={i} className="flex gap-2 mb-2">
              <select value={comp.food_id} onChange={(e) => updateComponent(i, 'food_id', e.target.value)}
                className="flex-1 px-3 py-2 border border-gray-300 rounded-md text-sm" required>
                <option value="">Select food...</option>
                {foods.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
              </select>
              <input placeholder="Grams" type="number" step="any" value={comp.amount_grams}
                onChange={(e) => updateComponent(i, 'amount_grams', e.target.value)}
                className="w-24 px-3 py-2 border border-gray-300 rounded-md text-sm" required />
              {components.length > 1 && (
                <button type="button" onClick={() => removeComponent(i)}
                  className="text-red-500 text-sm hover:text-red-700">Remove</button>
              )}
            </div>
          ))}
          <div className="flex gap-2 mt-2">
            <button type="button" onClick={addComponent}
              className="text-sm text-blue-600 hover:text-blue-800">+ Add component</button>
            <button type="submit"
              className="ml-auto px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700">
              Create Recipe
            </button>
          </div>
        </form>
      )}

      <div className="space-y-3">
        {recipes.map((recipe) => (
          <div key={recipe.id} className="bg-white rounded-lg border border-gray-200">
            <div className="flex items-center justify-between p-4 cursor-pointer"
              onClick={() => setExpanded(expanded === recipe.id ? null : recipe.id)}>
              <div>
                <h3 className="font-medium text-gray-900">{recipe.name}</h3>
                <p className="text-xs text-gray-400">
                  {recipe.total_grams}g &middot; {recipe.total_calories} kcal &middot;
                  P:{recipe.total_protein}g F:{recipe.total_fat}g C:{recipe.total_carbs}g
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={(e) => { e.stopPropagation(); deleteRecipe(recipe.id).then(load) }}
                  className="text-xs text-red-500 hover:text-red-700">Delete</button>
                <span className="text-gray-400 text-xs">{expanded === recipe.id ? '▲' : '▼'}</span>
              </div>
            </div>
            {expanded === recipe.id && (
              <div className="px-4 pb-4 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-gray-400 text-xs">
                      <th className="text-left font-normal">Ingredient</th>
                      <th className="text-right font-normal">g</th>
                      {MACRO_KEYS.map((m) => (
                        <th key={m} className="text-right font-normal">{MACRO_LABELS[m]}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {recipe.components.map((c) => (
                      <tr key={c.id} className="text-gray-700">
                        <td>{c.food_name}</td>
                        <td className="text-right">{c.amount_grams}</td>
                        {MACRO_KEYS.map((m) => (
                          <td key={m} className="text-right">{c[m]}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}
        {recipes.length === 0 && <p className="text-gray-400 text-center py-8">No recipes yet</p>}
      </div>
    </div>
  )
}

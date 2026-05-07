import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import FoodsPage from './FoodsPage'
import RecipesPage from './RecipesPage'
import TissueAdminPage from './TissueAdminPage'

type Tab = 'tissues' | 'exercises' | 'foods' | 'recipes'

const TABS: { id: Tab; label: string }[] = [
  { id: 'tissues', label: 'Tissues' },
  { id: 'exercises', label: 'Exercises' },
  { id: 'foods', label: 'Foods' },
  { id: 'recipes', label: 'Recipes' },
]

export default function DatabasePage() {
  const [params, setParams] = useSearchParams()
  const initial = (params.get('tab') as Tab) || 'tissues'
  const [tab, setTab] = useState<Tab>(
    TABS.some((t) => t.id === initial) ? initial : 'tissues',
  )

  useEffect(() => {
    if (params.get('tab') !== tab) {
      const next = new URLSearchParams(params)
      next.set('tab', tab)
      setParams(next, { replace: true })
    }
  }, [tab])

  return (
    <div className="h-full flex flex-col gap-3 min-h-0">
      <div className="flex gap-1 border-b border-gray-200 shrink-0">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm font-medium -mb-px border-b-2 transition-colors ${
              tab === t.id
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-600 hover:text-gray-900'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0">
        {tab === 'tissues' ? <TissueAdminPage mode="tissues" /> : null}
        {tab === 'exercises' ? <TissueAdminPage mode="exercises" /> : null}
        {tab === 'foods' ? <FoodsPage /> : null}
        {tab === 'recipes' ? <RecipesPage /> : null}
      </div>
    </div>
  )
}

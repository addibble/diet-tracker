import { useEffect, useMemo, useState } from 'react'
import ScrollablePage from '../components/ScrollablePage'
import {
  applyExerciseMappingWarning,
  getExercises,
  getTissues,
  updateExercise,
  type WkExercise,
  type WkExerciseTissueMapping,
  type WkTissue,
} from '../api'

// ── Style helpers ──

const TYPE_BADGE: Record<string, string> = {
  muscle: 'bg-green-100 text-green-700',
  tendon: 'bg-orange-100 text-orange-700',
  joint: 'bg-red-100 text-red-700',
}

const ROLE_COLORS: Record<string, string> = {
  primary: 'bg-emerald-100 text-emerald-800',
  secondary: 'bg-sky-100 text-sky-800',
  stabilizer: 'bg-gray-100 text-gray-600',
}

type View = 'tissues' | 'exercises'

interface TissueWithExercises extends WkTissue {
  exercises: {
    exercise_id: number
    exercise_name: string
    role: string
    loading_factor: number
    routing_factor: number
  }[]
}

// ── Tissue list panel ──

function TissuePanel({ tissues, exercises }: { tissues: WkTissue[]; exercises: WkExercise[] }) {
  const tissuesWithExercises: TissueWithExercises[] = useMemo(() => {
    const map = new Map<number, TissueWithExercises['exercises']>()
    for (const t of tissues) map.set(t.id, [])
    for (const ex of exercises) {
      for (const tm of ex.tissues) {
        const bucket = map.get(tm.tissue_id)
        if (!bucket) continue
        bucket.push({
          exercise_id: ex.id,
          exercise_name: ex.name,
          role: tm.role,
          loading_factor: tm.loading_factor,
          routing_factor: tm.routing_factor,
        })
      }
    }
    return tissues.map((t) => ({ ...t, exercises: map.get(t.id) ?? [] }))
  }, [tissues, exercises])

  const sorted = useMemo(
    () =>
      [...tissuesWithExercises].sort((a, b) => {
        const ra = a.region ?? ''
        const rb = b.region ?? ''
        if (ra !== rb) return ra.localeCompare(rb)
        return a.display_name.localeCompare(b.display_name)
      }),
    [tissuesWithExercises],
  )

  return (
    <div className="space-y-3">
      {sorted.map((t) => (
        <section key={t.id} className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold text-gray-900">{t.display_name}</h3>
              <span
                className={`px-2 py-0.5 text-[10px] rounded-full ${
                  TYPE_BADGE[t.type] ?? 'bg-gray-100 text-gray-600'
                }`}
              >
                {t.type}
              </span>
              {t.region && (
                <span className="px-2 py-0.5 text-[10px] rounded-full bg-slate-100 text-slate-700">
                  {t.region}
                </span>
              )}
            </div>
            <span className="text-xs text-gray-500">
              recovery ~{t.recovery_hours}h
            </span>
          </div>
          {t.exercises.length > 0 ? (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {t.exercises
                .slice()
                .sort((a, b) => b.loading_factor - a.loading_factor)
                .map((ex) => (
                  <li
                    key={ex.exercise_id}
                    className={`text-[11px] px-2 py-0.5 rounded-full ${
                      ROLE_COLORS[ex.role] ?? 'bg-gray-100 text-gray-600'
                    }`}
                    title={`role=${ex.role} loading=${ex.loading_factor.toFixed(2)}`}
                  >
                    {ex.exercise_name}
                  </li>
                ))}
            </ul>
          ) : (
            <p className="text-xs text-gray-400 mt-1">No exercises mapped.</p>
          )}
        </section>
      ))}
    </div>
  )
}

// ── Exercise editor ──

function ExerciseEditor({
  exercise,
  tissues,
  onSaved,
  onCancel,
}: {
  exercise: WkExercise
  tissues: WkTissue[]
  onSaved: (updated: WkExercise) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(exercise.name)
  const [equipment, setEquipment] = useState(exercise.equipment ?? '')
  const [laterality, setLaterality] = useState(exercise.laterality)
  const [notes, setNotes] = useState(exercise.notes ?? '')
  const [mappings, setMappings] = useState<WkExerciseTissueMapping[]>(exercise.tissues)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const tissueById = useMemo(() => {
    const m = new Map<number, WkTissue>()
    for (const t of tissues) m.set(t.id, t)
    return m
  }, [tissues])

  const availableToAdd = useMemo(() => {
    const used = new Set(mappings.map((m) => m.tissue_id))
    return tissues
      .filter((t) => !used.has(t.id))
      .sort((a, b) => a.display_name.localeCompare(b.display_name))
  }, [tissues, mappings])

  const updateMapping = (idx: number, patch: Partial<WkExerciseTissueMapping>) => {
    setMappings((prev) => prev.map((m, i) => (i === idx ? { ...m, ...patch } : m)))
  }

  const removeMapping = (idx: number) => {
    setMappings((prev) => prev.filter((_, i) => i !== idx))
  }

  const addMapping = (tissueId: number) => {
    const t = tissueById.get(tissueId)
    if (!t) return
    setMappings((prev) => [
      ...prev,
      {
        tissue_id: t.id,
        tissue_name: t.name,
        tissue_display_name: t.display_name,
        tissue_type: t.type,
        role: 'secondary',
        loading_factor: 0.5,
        routing_factor: 1.0,
        fatigue_factor: 1.0,
        joint_strain_factor: 0,
        tendon_strain_factor: 0,
        laterality_mode: 'bilateral_equal',
      },
    ])
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const updated = await updateExercise(exercise.id, {
        name,
        equipment: equipment || null,
        laterality,
        notes: notes || null,
        tissues: mappings.map((m) => ({
          tissue_id: m.tissue_id,
          role: m.role,
          loading_factor: m.loading_factor,
          routing_factor: m.routing_factor,
          fatigue_factor: m.fatigue_factor,
          joint_strain_factor: m.joint_strain_factor,
          tendon_strain_factor: m.tendon_strain_factor,
          laterality_mode: m.laterality_mode,
        })),
      })
      onSaved(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-gray-900">Edit exercise</h3>
        <button
          type="button"
          onClick={onCancel}
          className="text-sm text-gray-500 hover:text-gray-700"
        >
          Close
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="flex flex-col text-xs text-gray-600">
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 px-2 py-1.5 border border-gray-300 rounded text-sm"
          />
        </label>
        <label className="flex flex-col text-xs text-gray-600">
          Equipment
          <input
            value={equipment}
            onChange={(e) => setEquipment(e.target.value)}
            className="mt-1 px-2 py-1.5 border border-gray-300 rounded text-sm"
          />
        </label>
        <label className="flex flex-col text-xs text-gray-600">
          Laterality
          <select
            value={laterality}
            onChange={(e) => setLaterality(e.target.value as WkExercise['laterality'])}
            className="mt-1 px-2 py-1.5 border border-gray-300 rounded text-sm"
          >
            <option value="bilateral">bilateral</option>
            <option value="unilateral">unilateral</option>
            <option value="either">either</option>
          </select>
        </label>
      </div>

      <label className="flex flex-col text-xs text-gray-600">
        Notes
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          className="mt-1 px-2 py-1.5 border border-gray-300 rounded text-sm"
        />
      </label>

      <div>
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-sm font-semibold text-gray-800">Tissue mappings</h4>
          {availableToAdd.length > 0 && (
            <select
              defaultValue=""
              onChange={(e) => {
                const id = Number(e.target.value)
                if (id) addMapping(id)
                e.currentTarget.value = ''
              }}
              className="text-xs px-2 py-1 border border-gray-300 rounded"
            >
              <option value="">+ Add tissue…</option>
              {availableToAdd.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.display_name}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="space-y-1.5">
          {mappings.map((m, idx) => (
            <div
              key={m.tissue_id}
              className="flex items-center gap-2 text-xs bg-gray-50 rounded px-2 py-1.5"
            >
              <span className="flex-1 font-medium text-gray-800 truncate">
                {m.tissue_display_name}
              </span>
              <label className="flex items-center gap-1">
                role
                <select
                  value={m.role}
                  onChange={(e) => updateMapping(idx, { role: e.target.value })}
                  className="px-1 py-0.5 border border-gray-300 rounded"
                >
                  <option value="primary">primary</option>
                  <option value="secondary">secondary</option>
                  <option value="stabilizer">stabilizer</option>
                </select>
              </label>
              <label className="flex items-center gap-1">
                load
                <input
                  type="number"
                  step="0.05"
                  min="0"
                  max="1"
                  value={m.loading_factor}
                  onChange={(e) =>
                    updateMapping(idx, { loading_factor: Number(e.target.value) })
                  }
                  className="w-16 px-1 py-0.5 border border-gray-300 rounded"
                />
              </label>
              <button
                type="button"
                onClick={() => removeMapping(idx)}
                className="text-red-600 hover:text-red-800"
              >
                ✕
              </button>
            </div>
          ))}
          {mappings.length === 0 && (
            <p className="text-xs text-gray-400">No tissues mapped.</p>
          )}
        </div>
      </div>

      {exercise.mapping_warnings.length > 0 && (
        <div className="border border-amber-200 bg-amber-50 rounded p-2 space-y-1">
          <h4 className="text-xs font-semibold text-amber-900">Mapping suggestions</h4>
          {exercise.mapping_warnings.map((w) => (
            <div
              key={`${w.code}-${w.source_tissue_id}-${w.target_tissue_id}`}
              className="flex items-center justify-between text-xs text-amber-900"
            >
              <span>{w.message}</span>
              <button
                type="button"
                onClick={async () => {
                  try {
                    const upd = await applyExerciseMappingWarning(exercise.id, {
                      code: w.code,
                      source_tissue_id: w.source_tissue_id,
                      target_tissue_id: w.target_tissue_id,
                    })
                    onSaved(upd)
                  } catch (err) {
                    setError(err instanceof Error ? err.message : String(err))
                  }
                }}
                className="text-xs px-2 py-0.5 bg-amber-600 text-white rounded hover:bg-amber-700"
              >
                Apply
              </button>
            </div>
          ))}
        </div>
      )}

      {error && <p className="text-xs text-red-600">{error}</p>}

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="text-sm px-3 py-1.5 text-gray-600 hover:text-gray-800"
          disabled={saving}
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="text-sm px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}

function ExercisePanel({
  exercises,
  tissues,
  onExerciseUpdated,
}: {
  exercises: WkExercise[]
  tissues: WkTissue[]
  onExerciseUpdated: (ex: WkExercise) => void
}) {
  const [filter, setFilter] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const base = q
      ? exercises.filter((e) => e.name.toLowerCase().includes(q))
      : exercises
    return [...base].sort((a, b) => a.name.localeCompare(b.name))
  }, [filter, exercises])

  return (
    <div className="space-y-3">
      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter exercises…"
        className="w-full sm:w-64 px-3 py-1.5 border border-gray-300 rounded text-sm"
      />
      {filtered.map((ex) =>
        editingId === ex.id ? (
          <ExerciseEditor
            key={ex.id}
            exercise={ex}
            tissues={tissues}
            onSaved={(upd) => {
              onExerciseUpdated(upd)
              setEditingId(null)
            }}
            onCancel={() => setEditingId(null)}
          />
        ) : (
          <section
            key={ex.id}
            className="bg-white border border-gray-200 rounded-xl p-3"
          >
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-gray-900">{ex.name}</h3>
                <p className="text-xs text-gray-500">
                  {ex.equipment || 'no equipment'} · {ex.laterality}
                  {ex.mapping_warnings.length > 0 && (
                    <span className="ml-2 text-amber-700">
                      ⚠ {ex.mapping_warnings.length} suggestion
                      {ex.mapping_warnings.length === 1 ? '' : 's'}
                    </span>
                  )}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setEditingId(ex.id)}
                className="text-xs px-2 py-1 bg-gray-100 hover:bg-gray-200 rounded"
              >
                Edit
              </button>
            </div>
            {ex.tissues.length > 0 && (
              <ul className="mt-2 flex flex-wrap gap-1">
                {ex.tissues
                  .slice()
                  .sort((a, b) => b.loading_factor - a.loading_factor)
                  .map((t) => (
                    <li
                      key={t.tissue_id}
                      className={`text-[11px] px-2 py-0.5 rounded-full ${
                        ROLE_COLORS[t.role] ?? 'bg-gray-100 text-gray-600'
                      }`}
                      title={`loading=${t.loading_factor.toFixed(2)}`}
                    >
                      {t.tissue_display_name}
                    </li>
                  ))}
              </ul>
            )}
          </section>
        ),
      )}
    </div>
  )
}

// ── Main page ──

export default function TissueAdminPage() {
  const [tissues, setTissues] = useState<WkTissue[]>([])
  const [exercises, setExercises] = useState<WkExercise[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<View>('tissues')

  useEffect(() => {
    let cancelled = false
    Promise.all([getTissues(), getExercises()])
      .then(([t, e]) => {
        if (cancelled) return
        setTissues(t)
        setExercises(e)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleExerciseUpdated = (updated: WkExercise) => {
    setExercises((prev) => prev.map((e) => (e.id === updated.id ? updated : e)))
  }

  return (
    <ScrollablePage className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-2xl font-semibold text-gray-900">Tissues & Exercises</h1>
        <div className="inline-flex rounded-lg border border-gray-300 bg-white p-0.5 text-sm">
          <button
            type="button"
            onClick={() => setView('tissues')}
            className={`px-3 py-1 rounded ${
              view === 'tissues' ? 'bg-blue-600 text-white' : 'text-gray-700'
            }`}
          >
            Tissues
          </button>
          <button
            type="button"
            onClick={() => setView('exercises')}
            className={`px-3 py-1 rounded ${
              view === 'exercises' ? 'bg-blue-600 text-white' : 'text-gray-700'
            }`}
          >
            Exercises
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : view === 'tissues' ? (
        <TissuePanel tissues={tissues} exercises={exercises} />
      ) : (
        <ExercisePanel
          exercises={exercises}
          tissues={tissues}
          onExerciseUpdated={handleExerciseUpdated}
        />
      )}
    </ScrollablePage>
  )
}

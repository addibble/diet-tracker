import { useEffect, useMemo, useState } from 'react'
import {
  getExerciseMenu,
  prescribeAllSets,
  type ExerciseMenuItem,
  type PrescribeAllResponse,
} from '../api'

interface StrengthPlannerCardProps {
  onSave: (exercises: SelectedExercise[]) => void
  collapseWhenPlanned: boolean
}

export interface SelectedExercise {
  exercise_id: number
  name: string
  allow_heavy_loading: boolean
  is_bodyweight: boolean
  load_input_mode: string
  prescription?: PrescribeAllResponse
}

export default function StrengthPlannerCard({
  onSave,
  collapseWhenPlanned,
}: StrengthPlannerCardProps) {
  const [menu, setMenu] = useState<ExerciseMenuItem[]>([])
  const [loading, setLoading] = useState(true)
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set())
  const [expanded, setExpanded] = useState(true)
  const [saving, setSaving] = useState(false)
  const [prescriptions, setPrescriptions] = useState<Map<number, PrescribeAllResponse>>(new Map())
  const [loadingPrescriptions, setLoadingPrescriptions] = useState<Set<number>>(new Set())

  useEffect(() => {
    let cancelled = false
    getExerciseMenu().then(data => {
      if (cancelled) return
      setMenu(data)
      setLoading(false)
    }).catch(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (collapseWhenPlanned) setExpanded(false)
  }, [collapseWhenPlanned])

  const toggleExercise = (exerciseId: number) => {
    setCheckedIds(prev => {
      const next = new Set(prev)
      if (next.has(exerciseId)) {
        next.delete(exerciseId)
      } else {
        next.add(exerciseId)
        // Fetch prescription preview when selected
        if (!prescriptions.has(exerciseId)) {
          fetchPrescription(exerciseId)
        }
      }
      return next
    })
  }

  const fetchPrescription = async (exerciseId: number) => {
    setLoadingPrescriptions(prev => new Set(prev).add(exerciseId))
    try {
      const result = await prescribeAllSets({ exercise_id: exerciseId, set_number: 1 })
      setPrescriptions(prev => new Map(prev).set(exerciseId, result))
    } catch {
      // Prescription unavailable — not critical
    } finally {
      setLoadingPrescriptions(prev => {
        const next = new Set(prev)
        next.delete(exerciseId)
        return next
      })
    }
  }

  const weighted = useMemo(() => menu.filter(e => !e.is_bodyweight), [menu])
  const bodyweight = useMemo(() => menu.filter(e => e.is_bodyweight), [menu])

  const handleSave = async () => {
    setSaving(true)
    const selected: SelectedExercise[] = menu
      .filter(e => checkedIds.has(e.exercise_id))
      .map(e => ({
        exercise_id: e.exercise_id,
        name: e.name,
        allow_heavy_loading: e.allow_heavy_loading,
        is_bodyweight: e.is_bodyweight,
        load_input_mode: e.load_input_mode,
        prescription: prescriptions.get(e.exercise_id),
      }))
    onSave(selected)
    setSaving(false)
  }

  if (loading) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <h3 className="text-sm font-semibold text-gray-900">Strength Planner</h3>
        <div className="mt-3 animate-pulse space-y-2">
          <div className="h-4 w-2/3 rounded bg-gray-200" />
          <div className="h-4 w-full rounded bg-gray-200" />
          <div className="h-4 w-3/4 rounded bg-gray-200" />
        </div>
      </div>
    )
  }

  if (menu.length === 0) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <h3 className="text-sm font-semibold text-gray-900">Strength Planner</h3>
        <p className="mt-2 text-sm text-gray-500">
          No exercises with recent RPE data. Log some sets with RPE to get started.
        </p>
      </div>
    )
  }

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-gray-900">Strength Planner</h3>
            {collapseWhenPlanned && (
              <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-700">
                workout planned
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-gray-500">
            Select exercises ordered by freshness. The curve model prescribes progressive sets.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-lg bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
            {checkedIds.size} selected
          </span>
          <button
            type="button"
            onClick={() => setExpanded(v => !v)}
            className="rounded-lg border border-gray-200 px-2.5 py-1 text-[11px] font-medium text-gray-600 transition-colors hover:border-gray-300 hover:text-gray-800"
          >
            {expanded ? 'Collapse' : 'Expand'}
          </button>
        </div>
      </div>

      {!expanded && (
        <div className="mt-3 text-xs text-gray-500">
          {collapseWhenPlanned
            ? 'Workout is planned. Re-open to adjust exercise selection.'
            : `${checkedIds.size} exercises selected. Expand to review.`}
        </div>
      )}

      {expanded && (
        <>
          {weighted.length > 0 && (
            <div className="mt-4">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                Weighted Exercises
              </h4>
              <div className="space-y-1.5">
                {weighted.map(ex => (
                  <ExerciseMenuRow
                    key={ex.exercise_id}
                    item={ex}
                    checked={checkedIds.has(ex.exercise_id)}
                    onToggle={() => toggleExercise(ex.exercise_id)}
                    prescription={prescriptions.get(ex.exercise_id)}
                    loadingPrescription={loadingPrescriptions.has(ex.exercise_id)}
                  />
                ))}
              </div>
            </div>
          )}

          {bodyweight.length > 0 && (
            <div className="mt-4">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                Bodyweight Exercises
              </h4>
              <div className="space-y-1.5">
                {bodyweight.map(ex => (
                  <ExerciseMenuRow
                    key={ex.exercise_id}
                    item={ex}
                    checked={checkedIds.has(ex.exercise_id)}
                    onToggle={() => toggleExercise(ex.exercise_id)}
                  />
                ))}
              </div>
            </div>
          )}

          <div className="mt-4 flex items-center justify-between">
            <p className={`text-xs ${
              checkedIds.size >= 5 && checkedIds.size <= 10
                ? 'text-emerald-600'
                : checkedIds.size > 0
                  ? 'text-amber-600'
                  : 'text-gray-400'
            }`}>
              {checkedIds.size === 0
                ? 'Select exercises to build your workout.'
                : `${checkedIds.size} exercise${checkedIds.size !== 1 ? 's' : ''} selected.`}
            </p>
          </div>

          <button
            type="button"
            onClick={handleSave}
            disabled={checkedIds.size === 0 || saving}
            className="mt-3 w-full rounded-xl bg-gray-900 py-2 text-xs font-medium text-white transition-colors hover:bg-gray-800 disabled:opacity-40"
          >
            {saving
              ? 'Saving...'
              : `Plan Workout (${checkedIds.size} exercise${checkedIds.size !== 1 ? 's' : ''})`}
          </button>
        </>
      )}
    </div>
  )
}

function ExerciseMenuRow({
  item,
  checked,
  onToggle,
  prescription,
  loadingPrescription,
}: {
  item: ExerciseMenuItem
  checked: boolean
  onToggle: () => void
  prescription?: PrescribeAllResponse
  loadingPrescription?: boolean
}) {
  const freshnessColor = item.days_since_trained === null
    ? 'text-gray-400'
    : item.days_since_trained >= 5
      ? 'text-emerald-600'
      : item.days_since_trained >= 3
        ? 'text-gray-600'
        : 'text-amber-600'

  return (
    <button
      type="button"
      onClick={onToggle}
      className={`w-full rounded-xl border p-3 text-left transition-all ${
        checked
          ? 'border-gray-300 bg-white shadow-sm'
          : 'border-gray-200 bg-gray-50/60 hover:border-gray-300'
      }`}
    >
      <div className="flex items-start gap-2.5">
        <div className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border-2 transition-colors ${
          checked ? 'border-gray-900 bg-gray-900' : 'border-gray-300 bg-white'
        }`}>
          {checked && (
            <svg className="h-2.5 w-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-medium text-gray-900">{item.name}</span>
            {item.allow_heavy_loading && !item.is_bodyweight && (
              <span className="rounded-full bg-indigo-100 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700">
                heavy OK
              </span>
            )}
            {item.has_curve_fit && (
              <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                curve fit
              </span>
            )}
            {!item.has_curve_fit && !item.is_bodyweight && (
              <span className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
                no curve
              </span>
            )}
            {item.is_bodyweight && (
              <span className="rounded-full bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-700">
                bodyweight
              </span>
            )}
          </div>

          <div className="mt-0.5 flex flex-wrap items-center gap-3 text-[11px]">
            <span className={freshnessColor}>
              {item.days_since_trained === null
                ? 'never trained'
                : item.days_since_trained === 0
                  ? 'trained today'
                  : `${item.days_since_trained}d ago`}
            </span>
            {item.recent_rpe_sets > 0 && (
              <span className="text-gray-500">
                {item.recent_rpe_sets} RPE set{item.recent_rpe_sets !== 1 ? 's' : ''} (30d)
              </span>
            )}
          </div>

          {/* Prescription preview when checked */}
          {checked && loadingPrescription && (
            <div className="mt-1.5 text-[10px] text-gray-400 italic">Loading prescription...</div>
          )}
          {checked && prescription && !loadingPrescription && prescription.sets && (
            <div className="mt-1.5 flex flex-wrap gap-2">
              {prescription.sets.map(s => (
                <span key={s.set_number} className="rounded-md bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-600">
                  Set {s.set_number}: {s.proposed_weight != null ? `${Math.round(s.proposed_weight)} lb` : '—'} × {s.target_reps} @ RPE {s.target_rpe}
                </span>
              ))}
            </div>
          )}
          {checked && prescription && !loadingPrescription && prescription.is_bodyweight && prescription.suggestion && (
            <div className="mt-1.5 text-[10px] text-gray-600">
              {prescription.suggestion.sets} × {prescription.suggestion.reps_per_set} reps
              {prescription.suggestion.notes && ` — ${prescription.suggestion.notes}`}
            </div>
          )}
          {checked && prescription && !loadingPrescription && !prescription.has_curve && !prescription.is_bodyweight && prescription.fallback_weight != null && (
            <div className="mt-1.5 text-[10px] text-amber-600">
              No curve — last weight: {prescription.fallback_weight} lb
            </div>
          )}
        </div>
      </div>
    </button>
  )
}

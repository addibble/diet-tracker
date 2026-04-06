import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import SymptomSeverityRow from './SymptomSeverityRow'
import {
  symptomDbToSeverity,
  symptomSeverityToDb,
} from './symptomSeverity'
import {
  addPlanExercise,
  addWorkoutSet,
  deleteWorkoutSet,
  getExercises,
  getTrackedTissueReadiness,
  getWorkoutSession,
  removePlanExercise,
  reorderPlanExercises,
  updateProgramDayExercise,
  updateWorkoutSet,
  type SavedPlanExercise,
  type TrackedTissueReadiness,
  type WkExercise,
  type WkSession,
  type WkSetTissueFeedback,
  type WorkoutSetUpdateInput,
} from '../api'
import { formatSchemeHistorySummary } from '../lib/workoutSchemes'

// ── Types ────────────────────────────────────────────────────────────

export interface WorkoutSetEditorProps {
  mode: 'plan' | 'log'
  /** Plan mode: editing ProgramDayExercise prescriptions */
  planExercises?: SavedPlanExercise[]
  onPlanChanged?: () => void
  /** Log mode: editing WorkoutSet records for a session */
  sessionId?: number
  session?: WkSession
  onSessionChanged?: () => void
  /** Compact layout (e.g., in chat bubble) */
  compact?: boolean
  /** Local date string (YYYY-MM-DD) for planner as_of parameter */
  asOf?: string
}

interface EditableSet {
  id: number
  exercise_id: number
  exercise_name: string
  set_order: number
  performed_side: 'left' | 'right' | 'center' | 'bilateral' | null
  reps: number | null
  weight: number | null
  duration_secs: number | null
  distance_steps: number | null
  started_at: string | null
  completed_at: string | null
  rpe: number | null
  rep_completion: string | null
  notes: string | null
  scheme_history?: SavedPlanExercise['scheme_history']
  tissue_feedback: WkSetTissueFeedback[]
  load_input_mode?: string
  set_metric_mode?: string
  saving?: boolean
}

type ExerciseGroup = {
  exercise?: WkExercise
  exercise_id: number
  exercise_name: string
  equipment: string | null
  load_input_mode: string
  set_metric_mode: string
  laterality: 'bilateral' | 'unilateral' | 'either'
  sets: EditableSet[]
  scheme_history?: SavedPlanExercise['scheme_history']
  // Plan targets (if available)
  target_sets?: number
  target_reps?: string
  target_weight?: number | null
}

type SetSide = 'left' | 'right' | 'center' | 'bilateral' | null
type SetPatch = WorkoutSetUpdateInput

// ── Debounce hook ────────────────────────────────────────────────────

function useDebouncedCallback<T extends (...args: never[]) => unknown>(
  fn: T,
  delayMs: number,
) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const latest = useRef(fn)

  useEffect(() => {
    latest.current = fn
  })

  return useCallback(
    (...args: Parameters<T>) => {
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => latest.current(...args), delayMs)
    },
    [delayMs],
  ) as T
}

function weightFieldLabel(loadInputMode: string): string {
  if (loadInputMode === 'assisted_bodyweight') return 'Assist'
  if (loadInputMode === 'mixed') return 'Load'
  if (loadInputMode === 'carry') return 'Carry'
  return 'Weight'
}

function usesWeightInput(loadInputMode: string): boolean {
  return loadInputMode !== 'bodyweight'
}

function primaryMetricFlags(setMetricMode: string) {
  return {
    showReps: setMetricMode === 'reps' || setMetricMode === 'hybrid',
    showDuration: setMetricMode === 'duration' || setMetricMode === 'hybrid',
    showDistance: setMetricMode === 'distance' || setMetricMode === 'hybrid',
  }
}

function oppositeSide(side: SetSide): 'left' | 'right' | null {
  if (side === 'left') return 'right'
  if (side === 'right') return 'left'
  return null
}

function trackedTissueNeedsFeedback(row: TrackedTissueReadiness): boolean {
  return !!row.active_rehab_plan || (!!row.condition && row.condition.status !== 'healthy')
}

function mappingAppliesToTrackedSide(
  lateralityMode: string,
  trackedSide: 'left' | 'right' | 'center',
  performedSide: SetSide,
) {
  if (trackedSide === 'center') return true
  if (!performedSide || performedSide === 'bilateral' || performedSide === 'center') return true
  if (lateralityMode === 'selected_side_only' || lateralityMode === 'selected_side_primary') {
    return trackedSide === performedSide
  }
  if (lateralityMode === 'contralateral_carryover') {
    return trackedSide === oppositeSide(performedSide)
  }
  return true
}

function relevantTrackedTissuesForSet(
  exercise: WkExercise | undefined,
  trackedReadiness: TrackedTissueReadiness[],
  performedSide: SetSide,
) {
  if (!exercise) return []
  const significantMappings = exercise.tissues.filter(
    (mapping) => mapping.loading_factor > 0 || mapping.routing_factor > 0,
  )
  return trackedReadiness.filter((row) => {
    if (!trackedTissueNeedsFeedback(row)) return false
    return significantMappings.some(
      (mapping) =>
        mapping.tissue_id === row.tracked_tissue.tissue_id
        && mappingAppliesToTrackedSide(
          mapping.laterality_mode,
          row.tracked_tissue.side,
          performedSide,
        ),
    )
  })
}

function formatTimestamp(timestamp: string | null): string {
  if (!timestamp) return '—'
  return new Date(timestamp).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function toDateTimeLocalValue(timestamp: string | null): string {
  if (!timestamp) return ''
  const date = new Date(timestamp)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function fromDateTimeLocalValue(value: string): string | null {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString()
}

function normalizeFeedbackForUpdate(feedback: WkSetTissueFeedback[]): NonNullable<WorkoutSetUpdateInput['tissue_feedback']> {
  return feedback.map((entry) => ({
    tracked_tissue_id: entry.tracked_tissue_id,
    pain_0_10: entry.pain_0_10,
    symptom_note: entry.symptom_note ?? null,
  }))
}

function optimisticFeedbackEntries(
  feedback: NonNullable<WorkoutSetUpdateInput['tissue_feedback']>,
  previous: WkSetTissueFeedback[],
): WkSetTissueFeedback[] {
  const previousById = new Map(previous.map((entry) => [entry.tracked_tissue_id, entry]))
  return feedback.map((entry) => ({
    tracked_tissue_id: entry.tracked_tissue_id,
    tracked_tissue_display_name: previousById.get(entry.tracked_tissue_id)?.tracked_tissue_display_name,
    pain_0_10: entry.pain_0_10,
    symptom_note: entry.symptom_note ?? null,
    recorded_at: previousById.get(entry.tracked_tissue_id)?.recorded_at,
    above_threshold: previousById.get(entry.tracked_tissue_id)?.above_threshold,
  }))
}

// ── Main component ───────────────────────────────────────────────────

export default function WorkoutSetEditor({
  mode,
  planExercises,
  onPlanChanged,
  sessionId,
  session: prefetchedSession,
  onSessionChanged,
  compact,
  asOf,
}: WorkoutSetEditorProps) {
  if (mode === 'plan') {
    return (
      <PlanEditor
        exercises={planExercises ?? []}
        onChanged={onPlanChanged}
        compact={compact}
        asOf={asOf}
      />
    )
  }
  return (
    <LogEditor
      sessionId={sessionId}
      prefetchedSession={prefetchedSession}
      onChanged={onSessionChanged}
      compact={compact}
    />
  )
}

// ── Plan Mode ────────────────────────────────────────────────────────

function PlanEditor({
  exercises,
  onChanged,
  compact,
  asOf,
}: {
  exercises: SavedPlanExercise[]
  onChanged?: () => void
  compact?: boolean
  asOf?: string
}) {
  const [saving, setSaving] = useState<Record<number, boolean>>({})
  const [localExercises, setLocalExercises] = useState(exercises)
  const [showPicker, setShowPicker] = useState(false)
  const [allExercises, setAllExercises] = useState<WkExercise[]>([])
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null)

  useEffect(() => { setLocalExercises(exercises) }, [exercises])
  useEffect(() => {
    getExercises().then(setAllExercises).catch(() => {})
  }, [])

  const handleUpdate = useCallback(
    async (pdeId: number, field: string, value: number | string | null) => {
      setSaving((p) => ({ ...p, [pdeId]: true }))
      try {
        await updateProgramDayExercise(pdeId, { [field]: value })
        onChanged?.()
      } catch {
        // optimistic UI — silently fail, parent refresh will correct
      } finally {
        setSaving((p) => ({ ...p, [pdeId]: false }))
      }
    },
    [onChanged],
  )

  const debouncedUpdate = useDebouncedCallback(handleUpdate, 1000)

  const handleRemove = useCallback(
    async (exerciseId: number) => {
      setLocalExercises((prev) => prev.filter((e) => e.exercise_id !== exerciseId))
      try {
        await removePlanExercise(exerciseId, asOf)
        onChanged?.()
      } catch {
        setLocalExercises(exercises) // rollback
      }
    },
    [exercises, onChanged, asOf],
  )

  const handleAdd = useCallback(
    async (ex: WkExercise) => {
      setShowPicker(false)
      try {
        await addPlanExercise([{
          exercise_id: ex.id,
          target_sets: 3,
          target_reps: '8-12',
          rep_scheme: 'medium',
        }], asOf)
        onChanged?.()
      } catch {
        // ignore
      }
    },
    [onChanged, asOf],
  )

  const handleDragEnd = useCallback(
    async (fromIdx: number, toIdx: number) => {
      if (fromIdx === toIdx) return
      const reordered = [...localExercises]
      const [moved] = reordered.splice(fromIdx, 1)
      reordered.splice(toIdx, 0, moved)
      setLocalExercises(reordered)
      setDragIdx(null)
      setDragOverIdx(null)
      try {
        await reorderPlanExercises(reordered.map((e) => e.pde_id), asOf)
        onChanged?.()
      } catch {
        setLocalExercises(exercises) // rollback
      }
    },
    [localExercises, exercises, onChanged, asOf],
  )

  const existingIds = useMemo(
    () => new Set(localExercises.map((e) => e.exercise_id)),
    [localExercises],
  )

  if (localExercises.length === 0 && !showPicker) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-gray-400 italic">No exercises in plan</p>
        <button
          onClick={() => setShowPicker(true)}
          className="w-full py-1.5 text-xs text-gray-500 hover:text-gray-700
                     border border-dashed border-gray-300 rounded-lg
                     hover:border-gray-400 transition-colors"
        >
          + Add Exercise
        </button>
      </div>
    )
  }

  return (
    <div className={`space-y-2 ${compact ? 'text-xs' : 'text-sm'}`}>
      {localExercises.map((ex, i) => (
        <div
          key={ex.pde_id}
          draggable
          onDragStart={() => setDragIdx(i)}
          onDragOver={(e) => { e.preventDefault(); setDragOverIdx(i) }}
          onDrop={() => { if (dragIdx !== null) handleDragEnd(dragIdx, i) }}
          onDragEnd={() => { setDragIdx(null); setDragOverIdx(null) }}
          className={`transition-all ${
            dragOverIdx === i && dragIdx !== null && dragIdx !== i
              ? 'border-t-2 border-blue-400'
              : ''
          } ${dragIdx === i ? 'opacity-40' : ''}`}
        >
          <PlanExerciseRow
            ex={ex}
            saving={saving[ex.pde_id]}
            onUpdate={(field, value) => debouncedUpdate(ex.pde_id, field, value)}
            onRemove={() => handleRemove(ex.exercise_id)}
          />
        </div>
      ))}

      {/* Add Exercise */}
      {showPicker ? (
        <ExercisePicker
          exercises={allExercises}
          existingIds={existingIds}
          onSelect={handleAdd}
          onCancel={() => setShowPicker(false)}
        />
      ) : (
        <button
          onClick={() => setShowPicker(true)}
          className="w-full py-1.5 text-xs text-gray-500 hover:text-gray-700
                     border border-dashed border-gray-300 rounded-lg
                     hover:border-gray-400 transition-colors"
        >
          + Add Exercise
        </button>
      )}
    </div>
  )
}

function PlanExerciseRow({
  ex,
  saving,
  onUpdate,
  onRemove,
}: {
  ex: SavedPlanExercise
  saving?: boolean
  onUpdate: (field: string, value: number | string | null) => void
  onRemove: () => void
}) {
  const schemeHistorySummary = formatSchemeHistorySummary(ex.scheme_history)
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-2.5">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="cursor-grab text-gray-300 hover:text-gray-500 text-xs select-none"
          title="Drag to reorder">⠿</span>
        <span className="text-sm font-medium text-gray-900 truncate flex-1">
          {ex.exercise_name}
        </span>
        {ex.equipment && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500">
            {ex.equipment}
          </span>
        )}
        {saving && (
          <span className="text-[10px] text-gray-400">saving…</span>
        )}
        <button
          onClick={onRemove}
          className="text-gray-300 hover:text-red-500 text-sm
                     leading-none px-0.5 transition-colors"
          title="Remove exercise"
        >🗑</button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1 text-xs text-gray-500">
          Sets
          <NumberInput
            value={ex.target_sets}
            min={1}
            max={20}
            onChange={(v) => onUpdate('target_sets', v)}
            className="w-12"
          />
        </label>
        <label className="flex items-center gap-1 text-xs text-gray-500">
          Reps
          <NumberInput
            value={ex.target_rep_min}
            min={1}
            onChange={(v) => onUpdate('target_rep_min', v)}
            className="w-12"
          />
          <span>–</span>
          <NumberInput
            value={ex.target_rep_max}
            min={1}
            onChange={(v) => onUpdate('target_rep_max', v)}
            className="w-12"
          />
        </label>
        {(ex.load_input_mode === 'external_weight' || ex.load_input_mode === 'assisted_bodyweight') && (
          <label className="flex items-center gap-1 text-xs text-gray-500">
            {ex.load_input_mode === 'assisted_bodyweight' ? 'Assist' : 'Weight'}
            <NumberInput
              value={ex.target_weight}
              step={2.5}
              min={0}
              onChange={(v) => onUpdate('target_weight', v)}
              className="w-16"
            />
            <span>lb</span>
          </label>
        )}
        {ex.laterality && ex.laterality !== 'bilateral' && (
          <label className="flex items-center gap-1 text-xs text-gray-500">
            Side
            <PerformedSideSelect
              value={ex.performed_side ?? null}
              onChange={(v) => onUpdate('performed_side', v)}
            />
          </label>
        )}
      </div>
      {ex.side_explanation && (
        <p className="mt-1.5 text-[11px] text-purple-600">{ex.side_explanation}</p>
      )}
      {ex.selection_note && (
        <p className="mt-1 text-[11px] text-blue-600">{ex.selection_note}</p>
      )}
      {schemeHistorySummary && (
        <p className="mt-1 text-[11px] text-gray-500">Recent: {schemeHistorySummary}</p>
      )}
    </div>
  )
}

// ── Log Mode ─────────────────────────────────────────────────────────

function LogEditor({
  sessionId,
  prefetchedSession,
  onChanged,
  compact,
}: {
  sessionId?: number
  prefetchedSession?: WkSession
  onChanged?: () => void
  compact?: boolean
}) {
  const [session, setSession] = useState<WkSession | null>(
    prefetchedSession ?? null,
  )
  const [exerciseCache, setExerciseCache] = useState<WkExercise[]>([])
  const [trackedReadiness, setTrackedReadiness] = useState<TrackedTissueReadiness[]>([])
  const [loading, setLoading] = useState(!prefetchedSession)
  const [showAddExercise, setShowAddExercise] = useState(false)

  // Load session data if not prefetched
  useEffect(() => {
    if (!sessionId || prefetchedSession) return
    let cancelled = false
    getWorkoutSession(sessionId).then((data) => {
      if (!cancelled) {
        setSession(data)
        setLoading(false)
      }
    })
    return () => { cancelled = true }
  }, [sessionId, prefetchedSession])

  // Load exercises for picker
  useEffect(() => {
    getExercises().then(setExerciseCache).catch(() => {})
    getTrackedTissueReadiness().then(setTrackedReadiness).catch(() => {})
  }, [])

  const exerciseLookup = useMemo(() => {
    const map = new Map<number, WkExercise>()
    for (const ex of exerciseCache) map.set(ex.id, ex)
    return map
  }, [exerciseCache])

  // Group sets by exercise
  const groups: ExerciseGroup[] = useMemo(() => {
    if (!session) return []
    const map = new Map<number, EditableSet[]>()
    const order: number[] = []
    for (const s of session.sets) {
      if (!map.has(s.exercise_id)) {
        map.set(s.exercise_id, [])
        order.push(s.exercise_id)
      }
      map.get(s.exercise_id)!.push({ ...s })
    }
    return order.map((eid) => {
      const sets = map.get(eid)!
      const ex = exerciseLookup.get(eid)
      return {
        exercise: ex,
        exercise_id: eid,
        exercise_name: sets[0].exercise_name,
        equipment: ex?.equipment ?? null,
        load_input_mode: ex?.load_input_mode ?? 'external_weight',
        set_metric_mode: ex?.set_metric_mode ?? 'reps',
        laterality: ex?.laterality ?? 'bilateral',
        sets,
        scheme_history: sets[0]?.scheme_history,
      }
    })
  }, [session, exerciseLookup])

  const refreshSession = useCallback(async () => {
    if (!session) return
    try {
      const fresh = await getWorkoutSession(session.id)
      setSession(fresh)
      onChanged?.()
    } catch {
      // ignore
    }
  }, [session, onChanged])

  const handleUpdateSet = useCallback(
    async (setId: number, patch: SetPatch) => {
      // Optimistic update
      setSession((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          sets: prev.sets.map((s) =>
            s.id === setId
              ? {
                  ...s,
                  ...patch,
                  tissue_feedback: patch.tissue_feedback
                    ? optimisticFeedbackEntries(patch.tissue_feedback, s.tissue_feedback)
                    : s.tissue_feedback,
                }
              : s,
          ),
        }
      })
      try {
        const updated = await updateWorkoutSet(setId, patch)
        setSession((prev) => {
          if (!prev) return prev
          return {
            ...prev,
            sets: prev.sets.map((setRow) => (
              setRow.id === setId ? { ...updated } : setRow
            )),
          }
        })
        onChanged?.()
      } catch {
        refreshSession() // rollback on error
      }
    },
    [onChanged, refreshSession],
  )

  const handleDeleteExercise = useCallback(
    async (exerciseId: number) => {
      if (!session) return
      const setsToDelete = session.sets.filter((s) => s.exercise_id === exerciseId)
      // Optimistic remove
      setSession((prev) => {
        if (!prev) return prev
        return { ...prev, sets: prev.sets.filter((s) => s.exercise_id !== exerciseId) }
      })
      try {
        for (const s of setsToDelete) await deleteWorkoutSet(s.id)
        onChanged?.()
      } catch {
        refreshSession()
      }
    },
    [session, onChanged, refreshSession],
  )

  const handleAddSet = useCallback(
    async (exerciseId: number, templateSet?: EditableSet) => {
      if (!session) return
      try {
        await addWorkoutSet(session.id, {
          exercise_id: exerciseId,
          performed_side: templateSet?.performed_side ?? null,
          reps: templateSet?.reps ?? null,
          weight: templateSet?.weight ?? null,
          duration_secs: templateSet?.duration_secs ?? null,
          distance_steps: templateSet?.distance_steps ?? null,
          tissue_feedback: templateSet?.tissue_feedback?.length
            ? normalizeFeedbackForUpdate(templateSet.tissue_feedback)
            : undefined,
        })
        await refreshSession()
      } catch {
        // ignore
      }
    },
    [session, refreshSession],
  )

  const handleDeleteSet = useCallback(
    async (setId: number) => {
      // Optimistic remove
      setSession((prev) => {
        if (!prev) return prev
        return { ...prev, sets: prev.sets.filter((s) => s.id !== setId) }
      })
      try {
        await deleteWorkoutSet(setId)
        onChanged?.()
      } catch {
        refreshSession()
      }
    },
    [onChanged, refreshSession],
  )

  const handleAddExercise = useCallback(
    async (exercise: WkExercise) => {
      if (!session) return
      setShowAddExercise(false)
      try {
        await addWorkoutSet(session.id, { exercise_id: exercise.id })
        await refreshSession()
      } catch {
        // ignore
      }
    },
    [session, refreshSession],
  )

  if (loading) {
    return (
      <div className="text-xs text-gray-400 py-2 text-center">Loading…</div>
    )
  }
  if (!session) {
    return (
      <div className="text-xs text-gray-400 py-2 text-center">
        No session data
      </div>
    )
  }

  return (
    <div className={`space-y-2 ${compact ? 'text-xs' : 'text-sm'}`}>
      {groups.length === 0 && (
        <p className="text-xs text-gray-400 italic text-center py-1">
          No sets logged yet
        </p>
      )}

      {groups.map((g) => (
        <LogExerciseGroup
          key={g.exercise_id}
          group={g}
          trackedReadiness={trackedReadiness}
          compact={compact}
          onUpdateSet={handleUpdateSet}
          onAddSet={handleAddSet}
          onDeleteSet={handleDeleteSet}
          onDeleteExercise={handleDeleteExercise}
        />
      ))}

      {/* Add Exercise */}
      {showAddExercise ? (
        <ExercisePicker
          exercises={exerciseCache}
          existingIds={new Set(groups.map((g) => g.exercise_id))}
          onSelect={handleAddExercise}
          onCancel={() => setShowAddExercise(false)}
        />
      ) : (
        <button
          onClick={() => setShowAddExercise(true)}
          className="w-full py-1.5 text-xs text-gray-500 hover:text-gray-700 border border-dashed border-gray-300 rounded-lg hover:border-gray-400 transition-colors"
        >
          + Add Exercise
        </button>
      )}
    </div>
  )
}

// ── Log exercise group ───────────────────────────────────────────────

function LogExerciseGroup({
  group,
  trackedReadiness,
  compact,
  onUpdateSet,
  onAddSet,
  onDeleteSet,
  onDeleteExercise,
}: {
  group: ExerciseGroup
  trackedReadiness: TrackedTissueReadiness[]
  compact?: boolean
  onUpdateSet: (setId: number, patch: SetPatch) => void
  onAddSet: (exerciseId: number, templateSet?: EditableSet) => void
  onDeleteSet: (setId: number) => void
  onDeleteExercise: (exerciseId: number) => void
}) {
  const mode = group.load_input_mode
  const metrics = primaryMetricFlags(group.set_metric_mode)

  // Plan target reference line
  const targetRef = group.target_reps
    ? `${group.target_sets ?? '?'}×${group.target_reps}${group.target_weight != null ? ` @ ${group.target_weight} lb` : ''}`
    : null
  const schemeHistorySummary = formatSchemeHistorySummary(group.scheme_history)

  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      {/* Header */}
      <div className="px-2.5 py-1.5 border-b border-gray-100 flex items-center justify-between">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={`font-medium text-gray-900 truncate ${compact ? 'text-xs' : 'text-sm'}`}
          >
            {group.exercise_name}
          </span>
          {group.equipment && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500 shrink-0">
              {group.equipment}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {targetRef && (
            <span className="text-[10px] text-gray-400">
              Target: {targetRef}
            </span>
          )}
          <button
            onClick={() => onDeleteExercise(group.exercise_id)}
            className="text-gray-300 hover:text-red-500 text-sm leading-none
                       px-0.5 transition-colors"
            title="Remove exercise"
          >🗑</button>
        </div>
      </div>
      {schemeHistorySummary && (
        <div className="px-2.5 py-1 text-[10px] text-gray-500 border-b border-gray-100">
          Recent: {schemeHistorySummary}
        </div>
      )}

      {/* Column headers */}
      <div className="px-2.5 pt-1 pb-0.5 flex items-center gap-1 text-[10px] text-gray-400 uppercase tracking-wider">
        <span className="w-6 text-center">#</span>
        {usesWeightInput(mode) && (
          <span className="w-16 text-center">{weightFieldLabel(mode)}</span>
        )}
        {metrics.showReps && (
          <span className="w-12 text-center">Reps</span>
        )}
        {metrics.showDuration && (
          <span className="w-16 text-center">Secs</span>
        )}
        {metrics.showDistance && (
          <span className="w-16 text-center">Steps</span>
        )}
        {group.laterality !== 'bilateral' && (
          <span className="w-14 text-center">Side</span>
        )}
        <span className="w-14 text-center">RPE</span>
        <span className="w-14 text-center">Status</span>
        <span className="w-16 text-center">Done</span>
        <span className="w-5" />
      </div>

      {/* Set rows */}
      <div className="px-1.5 pb-1.5 space-y-0.5">
        {group.sets.map((s, i) => (
          <SetRow
            key={s.id}
            set={s}
            index={i + 1}
            mode={mode}
            setMetricMode={group.set_metric_mode}
            laterality={group.laterality}
            exercise={group.exercise}
            trackedReadiness={trackedReadiness}
            onUpdate={(patch) => onUpdateSet(s.id, patch)}
            onDelete={() => onDeleteSet(s.id)}
          />
        ))}

        {/* Add set */}
        <button
          onClick={() =>
            onAddSet(
              group.exercise_id,
              group.sets[group.sets.length - 1],
            )
          }
          className="w-full py-1 text-[11px] text-gray-400 hover:text-gray-600 transition-colors"
        >
          + set
        </button>
      </div>
    </div>
  )
}

// ── Individual set row ───────────────────────────────────────────────

function SetRow({
  set,
  index,
  mode,
  setMetricMode,
  laterality,
  exercise,
  trackedReadiness,
  onUpdate,
  onDelete,
}: {
  set: EditableSet
  index: number
  mode: string
  setMetricMode: string
  laterality: 'bilateral' | 'unilateral' | 'either'
  exercise?: WkExercise
  trackedReadiness: TrackedTissueReadiness[]
  onUpdate: (patch: SetPatch) => void
  onDelete: () => void
}) {
  const metrics = primaryMetricFlags(setMetricMode)
  const feedbackTargets = relevantTrackedTissuesForSet(exercise, trackedReadiness, set.performed_side)
  const feedbackByTrackedId = useMemo(
    () => new Map(set.tissue_feedback.map((entry) => [entry.tracked_tissue_id, entry])),
    [set.tissue_feedback],
  )

  const updateFeedback = (trackedTissueId: number, patch: Partial<WkSetTissueFeedback>) => {
    const current = feedbackByTrackedId.get(trackedTissueId)
    const next = new Map(feedbackByTrackedId)
    next.set(trackedTissueId, {
      tracked_tissue_id: trackedTissueId,
      tracked_tissue_display_name:
        current?.tracked_tissue_display_name
        ?? feedbackTargets.find((row) => row.tracked_tissue.id === trackedTissueId)?.tracked_tissue.display_name,
      pain_0_10: patch.pain_0_10 ?? current?.pain_0_10 ?? 0,
      symptom_note: patch.symptom_note ?? current?.symptom_note ?? null,
      above_threshold: current?.above_threshold,
      recorded_at: current?.recorded_at,
    })
    onUpdate({
      tissue_feedback: normalizeFeedbackForUpdate(Array.from(next.values())),
    })
  }

  return (
    <div className="space-y-1 rounded-md px-1 py-1 group hover:bg-gray-50">
      <div className="flex items-center gap-1">
        <span className="w-6 text-center text-[11px] text-gray-400 tabular-nums">
          {index}
        </span>

        {usesWeightInput(mode) && (
          <NumberInput
            value={set.weight}
            step={2.5}
            min={0}
            onChange={(v) => onUpdate({ weight: v })}
            className="w-16"
            placeholder={weightFieldLabel(mode).toLowerCase()}
          />
        )}

        {metrics.showReps && (
          <NumberInput
            value={set.reps}
            min={0}
            onChange={(v) => onUpdate({ reps: v })}
            className="w-12"
            placeholder="reps"
          />
        )}

        {metrics.showDuration && (
          <NumberInput
            value={set.duration_secs}
            min={0}
            onChange={(v) => onUpdate({ duration_secs: v })}
            className="w-16"
            placeholder="sec"
          />
        )}

        {metrics.showDistance && (
          <NumberInput
            value={set.distance_steps}
            min={0}
            onChange={(v) => onUpdate({ distance_steps: v })}
            className="w-16"
            placeholder="steps"
          />
        )}

        {laterality !== 'bilateral' && (
          <PerformedSideSelect
            value={set.performed_side}
            onChange={(v) => onUpdate({ performed_side: v })}
          />
        )}

        <NumberInput
          value={set.rpe}
          step={0.5}
          min={1}
          max={10}
          onChange={(v) => onUpdate({ rpe: v })}
          className="w-14"
          placeholder="RPE"
        />

        <RepCompletionSelect
          value={set.rep_completion}
          onChange={(v) => onUpdate({ rep_completion: v })}
        />

        <DateTimeInput
          value={set.completed_at}
          onChange={(value) => onUpdate({ completed_at: value, started_at: set.started_at ?? value })}
          className="w-32"
        />

        <button
          onClick={onDelete}
          className="w-5 h-5 flex items-center justify-center text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
          title="Delete set"
        >
          ×
        </button>
      </div>

      <div className="ml-7 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-gray-500">
        <span>Started {formatTimestamp(set.started_at)}</span>
        <span>Finished {formatTimestamp(set.completed_at)}</span>
      </div>

      {feedbackTargets.length > 0 && (
        <div className="ml-7 grid gap-1 rounded-md border border-amber-100 bg-amber-50/60 px-2 py-2">
          <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-amber-700">
            Tissue check
          </div>
          {feedbackTargets.map((tracked) => {
            const feedback = feedbackByTrackedId.get(tracked.tracked_tissue.id)
            return (
              <div
                key={tracked.tracked_tissue.id}
                className="grid gap-2 rounded-md border border-amber-200 bg-white/80 px-2 py-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-medium text-amber-900">
                    {tracked.tracked_tissue.display_name}
                  </span>
                  {feedback?.above_threshold && (
                    <span className="rounded-full bg-red-100 px-2 py-0.5 text-[10px] font-medium text-red-700">
                      Above threshold
                    </span>
                  )}
                </div>
                <SymptomSeverityRow
                  label="Pain"
                  value={symptomDbToSeverity(feedback?.pain_0_10 ?? 0)}
                  onChange={(value) => updateFeedback(
                    tracked.tracked_tissue.id,
                    { pain_0_10: symptomSeverityToDb(value) },
                  )}
                  showDescription={false}
                />
                <input
                  type="text"
                  value={feedback?.symptom_note ?? ''}
                  onChange={(event) =>
                    updateFeedback(tracked.tracked_tissue.id, { symptom_note: event.target.value || null })}
                  placeholder="symptom note"
                  className={`rounded border px-2 py-1 text-[11px] ${
                    feedback?.above_threshold
                      ? 'border-red-300 bg-red-50 text-red-700'
                      : 'border-amber-200 bg-white text-gray-700'
                  }`}
                />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function DateTimeInput({
  value,
  onChange,
  className,
}: {
  value: string | null
  onChange: (value: string | null) => void
  className?: string
}) {
  const [local, setLocal] = useState(toDateTimeLocalValue(value))

  useEffect(() => {
    setLocal(toDateTimeLocalValue(value))
  }, [value])

  return (
    <input
      type="datetime-local"
      value={local}
      onChange={(event) => setLocal(event.target.value)}
      onBlur={() => onChange(fromDateTimeLocalValue(local))}
      className={`rounded border border-gray-200 bg-white px-1 py-0.5 text-[11px] text-gray-700 focus:outline-none focus:ring-1 focus:ring-teal-500 ${className ?? ''}`}
    />
  )
}

function PerformedSideSelect({
  value,
  onChange,
}: {
  value: 'left' | 'right' | 'center' | 'bilateral' | null
  onChange: (v: 'left' | 'right' | 'center' | 'bilateral' | null) => void
}) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onChange((e.target.value || null) as 'left' | 'right' | 'center' | 'bilateral' | null)}
      className="w-14 px-1 py-0.5 text-[11px] border border-gray-200 rounded bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-teal-500"
    >
      <option value="">—</option>
      <option value="left">L</option>
      <option value="right">R</option>
      <option value="center">C</option>
      <option value="bilateral">B</option>
    </select>
  )
}

// ── Rep completion dropdown ──────────────────────────────────────────

function RepCompletionSelect({
  value,
  onChange,
}: {
  value: string | null
  onChange: (v: string | null) => void
}) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value || null)}
      className="w-14 px-1 py-0.5 text-[11px] border border-gray-200 rounded bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-teal-500"
    >
      <option value="">—</option>
      <option value="full">✓ full</option>
      <option value="partial">◐ partial</option>
      <option value="failed">✗ failed</option>
    </select>
  )
}

// ── Exercise picker ──────────────────────────────────────────────────

function ExercisePicker({
  exercises,
  existingIds,
  onSelect,
  onCancel,
}: {
  exercises: WkExercise[]
  existingIds: Set<number>
  onSelect: (ex: WkExercise) => void
  onCancel: () => void
}) {
  const [search, setSearch] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    return exercises
      .filter(
        (ex) =>
          !existingIds.has(ex.id) &&
          (ex.name.toLowerCase().includes(q) ||
            (ex.equipment ?? '').toLowerCase().includes(q)),
      )
      .slice(0, 10)
  }, [exercises, existingIds, search])

  return (
    <div className="border border-gray-300 rounded-lg bg-white p-2 space-y-1.5">
      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search exercises…"
          className="flex-1 px-2 py-1 text-xs border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-teal-500"
        />
        <button
          onClick={onCancel}
          className="text-xs text-gray-400 hover:text-gray-600"
        >
          cancel
        </button>
      </div>
      {filtered.length === 0 && (
        <p className="text-[11px] text-gray-400 text-center py-1">
          No matches
        </p>
      )}
      <div className="max-h-36 overflow-y-auto space-y-0.5">
        {filtered.map((ex) => (
          <button
            key={ex.id}
            onClick={() => onSelect(ex)}
            className="w-full text-left px-2 py-1 rounded text-xs hover:bg-gray-50 flex items-center gap-2"
          >
            <span className="text-gray-800">{ex.name}</span>
            {ex.equipment && (
              <span className="text-[10px] text-gray-400">{ex.equipment}</span>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Shared number input ──────────────────────────────────────────────

function NumberInput({
  value,
  onChange,
  step = 1,
  min,
  max,
  className = '',
  placeholder,
}: {
  value: number | null | undefined
  onChange: (v: number | null) => void
  step?: number
  min?: number
  max?: number
  className?: string
  placeholder?: string
}) {
  const [local, setLocal] = useState(value != null ? String(value) : '')
  const [lastSynced, setLastSynced] = useState(value)

  // Sync from parent when the prop value changes externally.
  if (value !== lastSynced) {
    setLastSynced(value)
    setLocal(value != null ? String(value) : '')
  }

  const commit = () => {
    if (local === '') {
      if (lastSynced !== null) {
        setLastSynced(null)
        onChange(null)
      }
    } else {
      const n = parseFloat(local)
      if (!isNaN(n) && n !== lastSynced) {
        setLastSynced(n)
        onChange(n)
      }
    }
  }

  return (
    <input
      type="number"
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') { commit(); (e.target as HTMLInputElement).blur() } }}
      step={step}
      min={min}
      max={max}
      placeholder={placeholder}
      className={`px-1 py-0.5 text-center text-[11px] border border-gray-200 rounded bg-white text-gray-800 tabular-nums focus:outline-none focus:ring-1 focus:ring-teal-500 ${className}`}
    />
  )
}

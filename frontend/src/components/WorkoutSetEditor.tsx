import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  addWorkoutSet,
  deleteWorkoutSet,
  getExercises,
  getWorkoutSession,
  updateProgramDayExercise,
  updateWorkoutSet,
  type SavedPlanExercise,
  type WkExercise,
  type WkSession,
} from '../api'

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
}

interface EditableSet {
  id: number
  exercise_id: number
  exercise_name: string
  set_order: number
  reps: number | null
  weight: number | null
  duration_secs: number | null
  distance_steps: number | null
  rpe: number | null
  rep_completion: string | null
  notes: string | null
  load_input_mode?: string
  saving?: boolean
}

type ExerciseGroup = {
  exercise_id: number
  exercise_name: string
  equipment: string | null
  load_input_mode: string
  sets: EditableSet[]
  // Plan targets (if available)
  target_sets?: number
  target_reps?: string
  target_weight?: number | null
}

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

// ── Main component ───────────────────────────────────────────────────

export default function WorkoutSetEditor({
  mode,
  planExercises,
  onPlanChanged,
  sessionId,
  session: prefetchedSession,
  onSessionChanged,
  compact,
}: WorkoutSetEditorProps) {
  if (mode === 'plan') {
    return (
      <PlanEditor
        exercises={planExercises ?? []}
        onChanged={onPlanChanged}
        compact={compact}
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
}: {
  exercises: SavedPlanExercise[]
  onChanged?: () => void
  compact?: boolean
}) {
  const [saving, setSaving] = useState<Record<number, boolean>>({})

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

  const debouncedUpdate = useDebouncedCallback(handleUpdate, 500)

  if (exercises.length === 0) {
    return <p className="text-xs text-gray-400 italic">No exercises in plan</p>
  }

  return (
    <div className={`space-y-2 ${compact ? 'text-xs' : 'text-sm'}`}>
      {exercises.map((ex) => (
        <PlanExerciseRow
          key={ex.pde_id}
          ex={ex}
          saving={saving[ex.pde_id]}
          onUpdate={(field, value) => debouncedUpdate(ex.pde_id, field, value)}
        />
      ))}
    </div>
  )
}

function PlanExerciseRow({
  ex,
  saving,
  onUpdate,
}: {
  ex: SavedPlanExercise
  saving?: boolean
  onUpdate: (field: string, value: number | string | null) => void
}) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-2.5">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-sm font-medium text-gray-900 truncate">
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
        {ex.load_input_mode === 'external_weight' && (
          <label className="flex items-center gap-1 text-xs text-gray-500">
            Weight
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
      </div>
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
        exercise_id: eid,
        exercise_name: sets[0].exercise_name,
        equipment: ex?.equipment ?? null,
        load_input_mode: ex?.load_input_mode ?? 'external_weight',
        sets,
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
    async (setId: number, field: string, value: number | string | null) => {
      // Optimistic update
      setSession((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          sets: prev.sets.map((s) =>
            s.id === setId ? { ...s, [field]: value } : s,
          ),
        }
      })
      try {
        await updateWorkoutSet(setId, { [field]: value })
        onChanged?.()
      } catch {
        refreshSession() // rollback on error
      }
    },
    [onChanged, refreshSession],
  )

  const debouncedUpdate = useDebouncedCallback(handleUpdateSet, 400)

  const handleAddSet = useCallback(
    async (exerciseId: number, templateSet?: EditableSet) => {
      if (!session) return
      try {
        await addWorkoutSet(session.id, {
          exercise_id: exerciseId,
          reps: templateSet?.reps ?? null,
          weight: templateSet?.weight ?? null,
          duration_secs: templateSet?.duration_secs ?? null,
          distance_steps: templateSet?.distance_steps ?? null,
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
          compact={compact}
          onUpdateSet={debouncedUpdate}
          onAddSet={handleAddSet}
          onDeleteSet={handleDeleteSet}
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
  compact,
  onUpdateSet,
  onAddSet,
  onDeleteSet,
}: {
  group: ExerciseGroup
  compact?: boolean
  onUpdateSet: (setId: number, field: string, value: number | string | null) => void
  onAddSet: (exerciseId: number, templateSet?: EditableSet) => void
  onDeleteSet: (setId: number) => void
}) {
  const mode = group.load_input_mode

  // Plan target reference line
  const targetRef = group.target_reps
    ? `${group.target_sets ?? '?'}×${group.target_reps}${group.target_weight != null ? ` @ ${group.target_weight} lb` : ''}`
    : null

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
        {targetRef && (
          <span className="text-[10px] text-gray-400 shrink-0 ml-2">
            Target: {targetRef}
          </span>
        )}
      </div>

      {/* Column headers */}
      <div className="px-2.5 pt-1 pb-0.5 flex items-center gap-1 text-[10px] text-gray-400 uppercase tracking-wider">
        <span className="w-6 text-center">#</span>
        {(mode === 'external_weight' || mode === 'bodyweight') && (
          <>
            {mode === 'external_weight' && (
              <span className="w-16 text-center">Weight</span>
            )}
            <span className="w-12 text-center">Reps</span>
          </>
        )}
        {mode === 'timed_hold' && (
          <span className="w-16 text-center">Secs</span>
        )}
        {mode === 'distance' && (
          <span className="w-16 text-center">Steps</span>
        )}
        <span className="w-14 text-center">RPE</span>
        <span className="w-14 text-center">Status</span>
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
            onUpdate={(field, value) => onUpdateSet(s.id, field, value)}
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
  onUpdate,
  onDelete,
}: {
  set: EditableSet
  index: number
  mode: string
  onUpdate: (field: string, value: number | string | null) => void
  onDelete: () => void
}) {
  return (
    <div className="flex items-center gap-1 py-0.5 group">
      <span className="w-6 text-center text-[11px] text-gray-400 tabular-nums">
        {index}
      </span>

      {(mode === 'external_weight' || mode === 'bodyweight') && (
        <>
          {mode === 'external_weight' && (
            <NumberInput
              value={set.weight}
              step={2.5}
              min={0}
              onChange={(v) => onUpdate('weight', v)}
              className="w-16"
              placeholder="lb"
            />
          )}
          <NumberInput
            value={set.reps}
            min={0}
            onChange={(v) => onUpdate('reps', v)}
            className="w-12"
            placeholder="reps"
          />
        </>
      )}

      {mode === 'timed_hold' && (
        <NumberInput
          value={set.duration_secs}
          min={0}
          onChange={(v) => onUpdate('duration_secs', v)}
          className="w-16"
          placeholder="sec"
        />
      )}

      {mode === 'distance' && (
        <NumberInput
          value={set.distance_steps}
          min={0}
          onChange={(v) => onUpdate('distance_steps', v)}
          className="w-16"
          placeholder="steps"
        />
      )}

      <NumberInput
        value={set.rpe}
        step={0.5}
        min={1}
        max={10}
        onChange={(v) => onUpdate('rpe', v)}
        className="w-14"
        placeholder="RPE"
      />

      <RepCompletionSelect
        value={set.rep_completion}
        onChange={(v) => onUpdate('rep_completion', v)}
      />

      <button
        onClick={onDelete}
        className="w-5 h-5 flex items-center justify-center text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
        title="Delete set"
      >
        ×
      </button>
    </div>
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
  // Using functional state comparisons to avoid ref-in-render.
  if (value !== lastSynced) {
    setLastSynced(value)
    setLocal(value != null ? String(value) : '')
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value
    setLocal(raw)
    if (raw === '') {
      setLastSynced(null)
      onChange(null)
    } else {
      const n = parseFloat(raw)
      if (!isNaN(n)) {
        setLastSynced(n)
        onChange(n)
      }
    }
  }

  return (
    <input
      type="number"
      value={local}
      onChange={handleChange}
      step={step}
      min={min}
      max={max}
      placeholder={placeholder}
      className={`px-1 py-0.5 text-center text-[11px] border border-gray-200 rounded bg-white text-gray-800 tabular-nums focus:outline-none focus:ring-1 focus:ring-teal-500 ${className}`}
    />
  )
}

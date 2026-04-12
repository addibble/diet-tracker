import { useCallback, useEffect, useRef, useState } from 'react'
import WorkoutSetEditor from './WorkoutSetEditor'
import {
  addPlanExercise,
  addWorkoutSet,
  completePlan,
  deleteWorkoutSession,
  getExerciseMenu,
  getWorkoutSession,
  prescribeNext,
  type ExerciseMenuItem,
  type PrescribeNextResponse,
  type WkSetDetail,
} from '../api'

function today() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

// ── Types ──

interface LoggedSet {
  id: number
  weight: number
  reps: number
  rir: number
}

interface ExerciseState {
  exercise_id: number
  name: string
  allow_heavy_loading: boolean
  is_bodyweight: boolean
  sets: LoggedSet[]
  prescription: PrescribeNextResponse | null
  prescribing: boolean
  complete: boolean
  inflection_detected: boolean | null
  estimated_1rm: number | null
}

interface ActiveWorkoutCardProps {
  sessionId: number
  exercises: ExerciseMenuItem[]
  onFinish: () => void
  onCancel: () => void
}

// ── Component ──

export default function ActiveWorkoutCard({
  sessionId,
  exercises,
  onFinish,
  onCancel,
}: ActiveWorkoutCardProps) {
  const [exStates, setExStates] = useState<ExerciseState[]>([])
  const [activeIdx, setActiveIdx] = useState(0)
  const [completing, setCompleting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [showAddExercise, setShowAddExercise] = useState(false)
  const [availableExercises, setAvailableExercises] = useState<ExerciseMenuItem[]>([])
  const [addingExercise, setAddingExercise] = useState(false)
  const [editing, setEditing] = useState(false)
  const initRef = useRef(false)

  // Initialize exercise states and load any existing sets from session
  useEffect(() => {
    if (initRef.current) return
    initRef.current = true

    const init = async () => {
      // Load existing session sets (for resume after refresh)
      let existingSets: WkSetDetail[] = []
      try {
        const session = await getWorkoutSession(sessionId)
        existingSets = session.sets || []
      } catch { /* new session, no sets yet */ }

      const states: ExerciseState[] = exercises.map(ex => {
        const mySets = existingSets
          .filter(s => s.exercise_id === ex.exercise_id)
          .sort((a, b) => a.set_order - b.set_order)
          .map(s => ({
            id: s.id,
            weight: s.weight ?? 0,
            reps: s.reps ?? 0,
            rir: s.rpe != null ? Math.round(10 - s.rpe) : 3,
          }))

        return {
          exercise_id: ex.exercise_id,
          name: ex.name,
          allow_heavy_loading: ex.allow_heavy_loading,
          is_bodyweight: ex.is_bodyweight,
          sets: mySets,
          prescription: null,
          prescribing: false,
          complete: false,
          inflection_detected: null,
          estimated_1rm: null,
        }
      })
      setExStates(states)

      // Find first incomplete exercise
      const firstIncomplete = states.findIndex(s => !s.complete)
      if (firstIncomplete >= 0) setActiveIdx(firstIncomplete)
    }
    init()
  }, [sessionId, exercises])

  // Rebuild exercise states from session after edits (clears prescriptions to re-trigger)
  const rebuildFromSession = useCallback(async () => {
    try {
      const session = await getWorkoutSession(sessionId)
      const freshSets = session.sets || []
      setExStates(prev => {
        // Preserve exercise order; update sets and clear prescription
        const exerciseIds = new Set(prev.map(s => s.exercise_id))
        // Also pick up any exercises added via the editor
        for (const s of freshSets) {
          if (!exerciseIds.has(s.exercise_id)) exerciseIds.add(s.exercise_id)
        }
        const updated: ExerciseState[] = []
        for (const old of prev) {
          const mySets = freshSets
            .filter(s => s.exercise_id === old.exercise_id)
            .sort((a, b) => a.set_order - b.set_order)
            .map(s => ({
              id: s.id,
              weight: s.weight ?? 0,
              reps: s.reps ?? 0,
              rir: s.rpe != null ? Math.round(10 - s.rpe) : 3,
            }))
          updated.push({ ...old, sets: mySets, prescription: null, prescribing: false })
        }
        return updated
      })
    } catch { /* ignore */ }
  }, [sessionId])

  // Fetch prescription for a specific exercise index
  const fetchingRef = useRef<number | null>(null)

  const fetchPrescription = (idx: number, states: typeof exStates) => {
    const ex = states[idx]
    if (!ex || ex.complete || ex.prescription) return
    if (fetchingRef.current === ex.exercise_id) return

    fetchingRef.current = ex.exercise_id
    const exerciseId = ex.exercise_id

    setExStates(prev => prev.map((s, i) => i === idx ? { ...s, prescribing: true } : s))

    const priorSets = ex.sets.map(s => ({
      weight: s.weight,
      reps: s.reps,
      rpe: 10 - s.rir,
    }))

    prescribeNext({ exercise_id: exerciseId, prior_sets: priorSets })
      .then(rx => {
        fetchingRef.current = null
        setExStates(prev => prev.map((s, i) => {
          if (i !== idx || s.exercise_id !== exerciseId) return s
          return {
            ...s,
            prescription: rx,
            prescribing: false,
            complete: rx.exercise_complete ?? false,
            inflection_detected: rx.inflection_detected ?? null,
            estimated_1rm: rx.estimated_1rm ?? null,
          }
        }))
      })
      .catch(() => {
        fetchingRef.current = null
        setExStates(prev => prev.map((s, i) =>
          i === idx ? { ...s, prescribing: false } : s
        ))
      })
  }

  // Auto-trigger prescription fetch when active exercise needs one
  const activeEx = exStates[activeIdx]
  const needsRx = activeEx && !activeEx.complete && !activeEx.prescription && !activeEx.prescribing
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async data fetch triggered by state change
    if (needsRx) fetchPrescription(activeIdx, exStates)
  })

  const handleAddExercise = async (exerciseId: number) => {
    setAddingExercise(true)
    try {
      await addPlanExercise([{ exercise_id: exerciseId }], today())
      const chosen = availableExercises.find(e => e.exercise_id === exerciseId)
      if (chosen) {
        const newState: ExerciseState = {
          exercise_id: chosen.exercise_id,
          name: chosen.name,
          allow_heavy_loading: chosen.allow_heavy_loading,
          is_bodyweight: chosen.is_bodyweight ?? false,
          sets: [],
          prescription: null,
          prescribing: false,
          complete: false,
          inflection_detected: null,
          estimated_1rm: null,
        }
        setExStates(prev => [...prev, newState])
        setActiveIdx(exStates.length) // switch to the newly added exercise
      }
    } catch { /* best effort */ }
    setAddingExercise(false)
    setShowAddExercise(false)
  }

  const handleOpenAddExercise = async () => {
    if (showAddExercise) {
      setShowAddExercise(false)
      return
    }
    setShowAddExercise(true)
    try {
      const menu = await getExerciseMenu()
      // Filter out exercises already in this session
      const currentIds = new Set(exStates.map(s => s.exercise_id))
      setAvailableExercises(menu.filter(e => !currentIds.has(e.exercise_id)))
    } catch {
      setAvailableExercises([])
    }
  }

  const handleFinish = async () => {
    setCompleting(true)
    try {
      await completePlan(today())
    } catch { /* may not have a planned session record */ }
    onFinish()
  }

  const handleCancel = async () => {
    setCancelling(true)
    try {
      await deleteWorkoutSession(sessionId)
    } catch { /* best effort */ }
    onCancel()
  }

  const allComplete = exStates.length > 0 && exStates.every(s => s.complete)

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Active Workout</h3>
          <p className="mt-0.5 text-xs text-gray-500">
            {exStates.filter(s => s.complete).length}/{exStates.length} exercises done
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => {
              if (editing) rebuildFromSession()
              setEditing(e => !e)
            }}
            className={`text-[10px] transition-colors ${
              editing
                ? 'font-medium text-blue-600 hover:text-blue-800'
                : 'text-gray-400 hover:text-gray-600'
            }`}
          >
            {editing ? 'done editing' : 'edit sets'}
          </button>
          {!allComplete && (
            <button
              type="button"
              onClick={handleCancel}
              disabled={cancelling}
              className="text-[10px] text-red-400 transition-colors hover:text-red-600 disabled:opacity-50"
            >
              {cancelling ? 'cancelling...' : 'cancel'}
            </button>
          )}
        </div>
      </div>

      {editing ? (
        /* Set editor mode */
        <WorkoutSetEditor
          mode="log"
          sessionId={sessionId}
          onSessionChanged={() => { /* live updates; rebuild happens on "done editing" */ }}
          compact
        />
      ) : (
        <>
          {/* Exercise tabs */}
          <div className="mb-4 flex gap-1.5 overflow-x-auto pb-1">
            {exStates.map((ex, i) => (
              <button
                key={ex.exercise_id}
                type="button"
                onClick={() => setActiveIdx(i)}
                className={`shrink-0 rounded-lg px-2.5 py-1.5 text-[11px] font-medium transition-all ${
                  i === activeIdx
                    ? 'bg-gray-900 text-white'
                    : ex.complete
                      ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {ex.complete && '✓ '}
                {ex.name}
              </button>
            ))}
            <button
              type="button"
              onClick={handleOpenAddExercise}
              disabled={addingExercise}
              className="shrink-0 rounded-lg bg-blue-50 px-2.5 py-1.5 text-[11px] font-medium text-blue-600 transition-all hover:bg-blue-100 disabled:opacity-50"
            >
              + Add
            </button>
          </div>

          {/* Add exercise dropdown */}
          {showAddExercise && (
            <div className="mb-4 max-h-48 overflow-y-auto rounded-lg border border-gray-200 bg-gray-50 p-2">
              {availableExercises.length === 0 ? (
                <p className="py-2 text-center text-xs text-gray-400">Loading...</p>
              ) : (
                availableExercises.map(ex => (
                  <button
                    key={ex.exercise_id}
                    type="button"
                    onClick={() => handleAddExercise(ex.exercise_id)}
                    disabled={addingExercise}
                    className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs text-gray-700 transition-colors hover:bg-white disabled:opacity-50"
                  >
                    <span className="font-medium">{ex.name}</span>
                    <span className="text-[10px] text-gray-400">
                      {ex.days_since_trained != null
                        ? `${ex.days_since_trained}d ago`
                        : 'never'}
                    </span>
                  </button>
                ))
              )}
            </div>
          )}

          {/* Active exercise card */}
          {exStates.length > 0 && exStates[activeIdx] && (
            <ExerciseWorkout
              key={exStates[activeIdx].exercise_id}
              sessionId={sessionId}
              state={exStates[activeIdx]}
              onSetLogged={(loggedSet) => {
                const idx = activeIdx
                setExStates(prev => prev.map((s, i) => {
                  if (i !== idx) return s
                  return { ...s, sets: [...s.sets, loggedSet], prescription: null }
                }))
              }}
              onMarkComplete={() => {
                setExStates(prev => prev.map((s, i) =>
                  i === activeIdx ? { ...s, complete: true } : s
                ))
                // Auto-advance to next incomplete
                const nextIdx = exStates.findIndex((s, i) => i > activeIdx && !s.complete)
                if (nextIdx >= 0) setActiveIdx(nextIdx)
              }}
            />
          )}
        </>
      )}

      {/* Finish / Complete button */}
      <button
        type="button"
        onClick={handleFinish}
        disabled={completing}
        className={`mt-4 w-full rounded-xl py-2.5 text-xs font-medium text-white transition-colors disabled:opacity-40 ${
          allComplete
            ? 'bg-emerald-600 hover:bg-emerald-700'
            : 'bg-gray-600 hover:bg-gray-700'
        }`}
      >
        {completing ? 'Finishing...' : allComplete ? '✓ Complete Workout' : 'Finish Workout Early'}
      </button>
    </div>
  )
}

// ── Per-exercise workout view ──

function ExerciseWorkout({
  sessionId,
  state,
  onSetLogged,
  onMarkComplete,
}: {
  sessionId: number
  state: ExerciseState
  onSetLogged: (set: LoggedSet) => void
  onMarkComplete: () => void
}) {
  const [weight, setWeight] = useState('')
  const [reps, setReps] = useState('')
  const [rir, setRir] = useState('')
  const [logging, setLogging] = useState(false)
  const [adjusting, setAdjusting] = useState(false)
  const adjustTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Pre-fill from prescription
  useEffect(() => {
    if (state.prescription?.next_set) {
      const ns = state.prescription.next_set
      if (ns.proposed_weight != null) setWeight(String(Math.round(ns.proposed_weight)))
      if (ns.target_reps != null) setReps(String(ns.target_reps))
      if (ns.target_rir != null) setRir(String(ns.target_rir))
    }
  }, [state.prescription])

  // Clean up debounce timer on unmount
  useEffect(() => () => { if (adjustTimer.current) clearTimeout(adjustTimer.current) }, [])

  // Re-prescribe when user enters a different weight
  const handleWeightChange = (value: string) => {
    setWeight(value)

    const rx = state.prescription
    if (!rx?.has_curve || !rx?.next_set) return

    const w = parseFloat(value)
    if (isNaN(w) || w <= 0) return

    // Skip if weight matches original prescription
    if (rx.next_set.proposed_weight != null
        && Math.abs(Math.round(rx.next_set.proposed_weight) - Math.round(w)) < 0.5) return

    if (adjustTimer.current) clearTimeout(adjustTimer.current)
    adjustTimer.current = setTimeout(async () => {
      setAdjusting(true)
      try {
        const priorSets = state.sets.map(s => ({
          weight: s.weight,
          reps: s.reps,
          rpe: 10 - s.rir,
        }))
        const adjusted = await prescribeNext({
          exercise_id: state.exercise_id,
          prior_sets: priorSets,
          actual_weight: w,
        })
        if (adjusted.next_set) {
          setReps(String(adjusted.next_set.target_reps))
          setRir(String(adjusted.next_set.target_rir))
        }
      } catch { /* ignore */ }
      finally { setAdjusting(false) }
    }, 500)
  }

  const handleLogSet = async () => {
    const w = parseFloat(weight)
    const r = parseInt(reps, 10)
    const ri = parseFloat(rir)
    if (isNaN(w) || isNaN(r) || isNaN(ri)) return

    setLogging(true)
    try {
      const result = await addWorkoutSet(sessionId, {
        exercise_id: state.exercise_id,
        weight: w,
        reps: r,
        rir: ri,
      })
      onSetLogged({ id: result.id, weight: w, reps: r, rir: ri })
      // Clear fields for next set
      setWeight('')
      setReps('')
      setRir('')
    } catch {
      // TODO: show error
    } finally {
      setLogging(false)
    }
  }

  const rx = state.prescription

  return (
    <div className="space-y-3">
      {/* Logged sets summary */}
      {state.sets.length > 0 && (
        <div className="space-y-1">
          {state.sets.map((s, i) => (
            <div key={s.id} className="flex items-center gap-3 rounded-lg bg-gray-50 px-3 py-1.5 text-xs text-gray-700">
              <span className="font-medium text-gray-500">Set {i + 1}</span>
              <span>{s.weight} lb × {s.reps} reps</span>
              <span className="text-gray-400">RIR {s.rir}</span>
            </div>
          ))}
        </div>
      )}

      {/* Inflection result */}
      {state.complete && state.inflection_detected && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3">
          <p className="text-xs font-medium text-emerald-800">
            ✓ Inflection detected — est. 1RM: {state.estimated_1rm != null ? `${Math.round(state.estimated_1rm)} lb` : '—'}
          </p>
          <p className="mt-0.5 text-[10px] text-emerald-600">
            Exercise complete. Strength curve is decelerating at your working weight.
          </p>
        </div>
      )}

      {state.complete && !state.inflection_detected && state.sets.length >= 3 && (
        <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
          <p className="text-xs font-medium text-gray-800">
            ✓ Exercise complete ({state.sets.length} sets)
          </p>
        </div>
      )}

      {/* Prescription / Next set guidance */}
      {!state.complete && (
        <>
          {state.prescribing && (
            <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
              <p className="text-xs italic text-gray-400">Computing prescription...</p>
            </div>
          )}

          {rx && !state.prescribing && rx.next_set && !rx.is_bodyweight && (
            <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-blue-800">
                  Set {rx.next_set.set_number} — suggested
                </p>
                <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-medium text-blue-700">
                  RIR {rx.next_set.target_rir}
                </span>
              </div>
              <p className="mt-1 text-sm font-semibold text-blue-900">
                {rx.next_set.proposed_weight != null ? `${Math.round(rx.next_set.proposed_weight)} lb` : '—'} × {rx.next_set.target_reps} reps
              </p>
              <p className="mt-0.5 text-[10px] text-blue-600">
                Range: {rx.next_set.acceptable_rep_min}–{rx.next_set.acceptable_rep_max} reps
              </p>
            </div>
          )}

          {rx && !state.prescribing && !rx.has_curve && !rx.is_bodyweight && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
              <p className="text-xs text-amber-800">
                {rx.message || 'No curve data available.'}
                {rx.fallback_weight != null && ` Last weight: ${rx.fallback_weight} lb`}
              </p>
            </div>
          )}

          {rx && rx.is_bodyweight && rx.suggestion && !rx.exercise_complete && (
            <div className="rounded-xl border border-sky-200 bg-sky-50 px-4 py-3">
              <p className="text-xs font-medium text-sky-800">
                Bodyweight — Set {state.sets.length + 1} of {rx.suggestion.sets}
              </p>
              <p className="mt-0.5 text-xs text-sky-700">
                {rx.suggestion.reps_per_set} reps
              </p>
            </div>
          )}

          {/* Input fields */}
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <label className="block text-[10px] font-medium text-gray-500">Weight (lb)</label>
              <input
                type="number"
                inputMode="decimal"
                value={weight}
                onChange={e => handleWeightChange(e.target.value)}
                className="mt-0.5 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm tabular-nums focus:border-gray-500 focus:ring-1 focus:ring-gray-400"
                placeholder="0"
              />
            </div>
            <div className="w-20">
              <label className="block text-[10px] font-medium text-gray-500">
                Reps{adjusting && <span className="ml-1 text-blue-400">…</span>}
              </label>
              <input
                type="number"
                inputMode="numeric"
                value={reps}
                onChange={e => setReps(e.target.value)}
                className={`mt-0.5 w-full rounded-lg border px-3 py-2 text-sm tabular-nums focus:border-gray-500 focus:ring-1 focus:ring-gray-400 ${
                  adjusting ? 'border-blue-300 bg-blue-50' : 'border-gray-300'
                }`}
                placeholder="0"
              />
            </div>
            <div className="w-16">
              <label className="block text-[10px] font-medium text-gray-500">
                RIR{adjusting && <span className="ml-1 text-blue-400">…</span>}
              </label>
              <input
                type="number"
                inputMode="numeric"
                value={rir}
                onChange={e => setRir(e.target.value)}
                min={0}
                max={5}
                className={`mt-0.5 w-full rounded-lg border px-3 py-2 text-sm tabular-nums focus:border-gray-500 focus:ring-1 focus:ring-gray-400 ${
                  adjusting ? 'border-blue-300 bg-blue-50' : 'border-gray-300'
                }`}
                placeholder="0"
              />
            </div>
            <button
              type="button"
              onClick={handleLogSet}
              disabled={logging || !weight || !reps || rir === ''}
              className="rounded-lg bg-gray-900 px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-gray-800 disabled:opacity-40"
            >
              {logging ? '...' : 'Log'}
            </button>
          </div>

          {/* Skip / Mark done */}
          <div className="flex justify-end">
            <button
              type="button"
              onClick={onMarkComplete}
              className="text-[10px] text-gray-400 transition-colors hover:text-gray-600"
            >
              skip / mark done
            </button>
          </div>
        </>
      )}
    </div>
  )
}

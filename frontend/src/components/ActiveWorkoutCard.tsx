import type { FocusEvent } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import WorkoutSetEditor from './WorkoutSetEditor'
import CurvePane, { type ConfirmedRir } from './CurvePane'
import { snapWeight } from '../lib/weight_grid'
import {
  addPlanExercise,
  addWorkoutSet,
  completeActivePlan,
  deleteWorkoutSession,
  getExerciseMenu,
  getWeeklyMenu,
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

// ── Skip persistence (per workout session) ──

function skippedKey(sessionId: number) {
  return `workout:${sessionId}:skipped`
}

function loadSkipped(sessionId: number): Set<number> {
  try {
    const raw = localStorage.getItem(skippedKey(sessionId))
    if (!raw) return new Set()
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) return new Set(parsed.filter((n): n is number => typeof n === 'number'))
  } catch { /* ignore */ }
  return new Set()
}

function saveSkipped(sessionId: number, skipped: Set<number>) {
  try {
    if (skipped.size === 0) localStorage.removeItem(skippedKey(sessionId))
    else localStorage.setItem(skippedKey(sessionId), JSON.stringify([...skipped]))
  } catch { /* ignore */ }
}

function addSkipped(sessionId: number, exerciseId: number) {
  const s = loadSkipped(sessionId)
  s.add(exerciseId)
  saveSkipped(sessionId, s)
}

function removeSkipped(sessionId: number, exerciseId: number) {
  const s = loadSkipped(sessionId)
  if (s.delete(exerciseId)) saveSkipped(sessionId, s)
}

function clearSkipped(sessionId: number) {
  try { localStorage.removeItem(skippedKey(sessionId)) } catch { /* ignore */ }
}

// Move the caret to the end of the current value on focus. Using rAF avoids
// mobile browsers (iOS Safari, Android Chrome) overriding the selection when
// they auto-select all text on first tap of a text input.
function moveCursorToEnd(e: FocusEvent<HTMLInputElement>) {
  const input = e.currentTarget
  const len = input.value.length
  requestAnimationFrame(() => {
    try { input.setSelectionRange(len, len) } catch { /* ignore */ }
  })
}

// ── Types ──

interface LoggedSet {
  id: number
  weight: number
  reps: number
  rir: number
  duration_secs: number | null
}

interface ExerciseState {
  exercise_id: number
  name: string
  allow_heavy_loading: boolean
  is_bodyweight: boolean
  heavy_available: boolean
  heavy_blocked_reason: string | null
  load_input_mode: string
  set_metric_mode: string
  target_sets: number
  training_mode: 'heavy' | 'volume'
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
      const skipped = loadSkipped(sessionId)
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
            duration_secs: s.duration_secs,
          }))

        // Resume mode from saved sets; otherwise default to heavy when it's
        // available (so the user doesn't miss the opportunity), else volume.
        const savedMode = existingSets.find(
          s => s.exercise_id === ex.exercise_id && s.training_mode
        )?.training_mode as 'heavy' | 'volume' | undefined

        const heavyAvailable = ex.heavy_available ?? false
        const defaultMode: 'heavy' | 'volume' =
          savedMode ?? (heavyAvailable && ex.allow_heavy_loading ? 'heavy' : 'volume')

        const targetSets = ex.target_sets ?? 3
        const isComplete = mySets.length >= targetSets || skipped.has(ex.exercise_id)

        return {
          exercise_id: ex.exercise_id,
          name: ex.name,
          allow_heavy_loading: ex.allow_heavy_loading,
          is_bodyweight: ex.is_bodyweight,
          heavy_available: heavyAvailable,
          heavy_blocked_reason: ex.heavy_blocked_reason ?? null,
          load_input_mode: ex.load_input_mode || 'external_weight',
          set_metric_mode: ex.set_metric_mode || 'reps',
          target_sets: targetSets,
          training_mode: defaultMode,
          sets: mySets,
          prescription: null,
          prescribing: false,
          complete: isComplete,
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
      const skipped = loadSkipped(sessionId)
      setExStates(prev => {
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
              duration_secs: s.duration_secs,
            }))
          const isComplete =
            mySets.length >= old.target_sets || skipped.has(old.exercise_id)
          updated.push({
            ...old,
            sets: mySets,
            prescription: null,
            prescribing: false,
            complete: isComplete,
          })
        }
        return updated
      })
    } catch { /* ignore */ }
  }, [sessionId])

  // Fetch prescription for a specific exercise index
  const fetchingRef = useRef<number | null>(null)

  const fetchPrescription = (idx: number, states: typeof exStates) => {
    const ex = states[idx]
    if (!ex || ex.prescription) return
    // Non-rep metric exercises have no strength curve; skip prescription.
    if (ex.set_metric_mode && ex.set_metric_mode !== 'reps' && ex.set_metric_mode !== 'hybrid') return
    if (fetchingRef.current === ex.exercise_id) return

    fetchingRef.current = ex.exercise_id
    const exerciseId = ex.exercise_id

    setExStates(prev => prev.map((s, i) => i === idx ? { ...s, prescribing: true } : s))

    const priorSets = ex.sets.map(s => ({
      weight: s.weight,
      reps: s.reps,
      rpe: 10 - s.rir,
    }))

    prescribeNext({ exercise_id: exerciseId, prior_sets: priorSets, training_mode: ex.training_mode })
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
  const needsRx = activeEx && !activeEx.prescription && !activeEx.prescribing
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
        const heavyAvailable = chosen.heavy_available ?? false
        const newState: ExerciseState = {
          exercise_id: chosen.exercise_id,
          name: chosen.name,
          allow_heavy_loading: chosen.allow_heavy_loading,
          is_bodyweight: chosen.is_bodyweight ?? false,
          heavy_available: heavyAvailable,
          heavy_blocked_reason: chosen.heavy_blocked_reason ?? null,
          load_input_mode: chosen.load_input_mode || 'external_weight',
          set_metric_mode: chosen.set_metric_mode || 'reps',
          target_sets: chosen.target_sets ?? 3,
          training_mode: heavyAvailable && chosen.allow_heavy_loading ? 'heavy' : 'volume',
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
      const [weekly, fullMenu] = await Promise.all([
        getWeeklyMenu(),
        getExerciseMenu(sessionId),
      ])
      const currentIds = new Set(exStates.map(s => s.exercise_id))

      // Build exercise_id -> group lookup from the weekly menu.
      const groupByExerciseId = new Map<number, string>()
      for (const g of weekly.groups) {
        for (const ex of g.exercises) groupByExerciseId.set(ex.exercise_id, g.name)
      }

      // Only suggest exercises from groups represented in the *current* session,
      // so an in-progress Pull/Legs day won't surface Shoulder/Core exercises.
      const activeSessionGroups = new Set<string>()
      for (const ex of exStates) {
        const g = groupByExerciseId.get(ex.exercise_id)
        if (g) activeSessionGroups.add(g)
      }

      // Merge heavy_available/target_sets from fullMenu into weekly items so
      // the add-exercise click has the same fields as the quick-start path.
      const fullById = new Map(fullMenu.map(e => [e.exercise_id, e]))
      const filtered = fullMenu
        .filter(e => {
          if (currentIds.has(e.exercise_id)) return false
          const g = groupByExerciseId.get(e.exercise_id)
          // If the current session has no recognizable groups yet (edge case),
          // fall back to showing everything rather than an empty list.
          if (activeSessionGroups.size === 0) return true
          return g != null && activeSessionGroups.has(g)
        })
        .map(e => ({ ...fullById.get(e.exercise_id), ...e }))
      // Sort by days since last trained, ascending; never-trained last.
      filtered.sort((a, b) => {
        const ad = a.days_since_trained
        const bd = b.days_since_trained
        if (ad == null && bd == null) return 0
        if (ad == null) return 1
        if (bd == null) return -1
        return ad - bd
      })
      setAvailableExercises(filtered)
    } catch {
      setAvailableExercises([])
    }
  }

  const handleFinish = async () => {
    setCompleting(true)
    // Mark the plan as completed on the backend so that a tab switch or
    // refresh doesn't re-restore this (partially-skipped) session. If the
    // call fails we still clear local state — the user's intent is clear.
    try {
      await completeActivePlan(today())
    } catch { /* best effort */ }
    clearSkipped(sessionId)
    onFinish()
  }

  const handleCancel = async () => {
    setCancelling(true)
    try {
      await deleteWorkoutSession(sessionId)
    } catch { /* best effort */ }
    clearSkipped(sessionId)
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
                    <span className="flex items-center gap-1.5 font-medium">
                      {ex.name}
                      {ex.heavy_available && (
                        <span className="text-[11px]" title="Heavy available">🔥</span>
                      )}
                    </span>
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
                // Logging a new set un-skips this exercise
                removeSkipped(sessionId, exStates[idx].exercise_id)
                setExStates(prev => prev.map((s, i) => {
                  if (i !== idx) return s
                  const nextSets = [...s.sets, loggedSet]
                  return {
                    ...s,
                    sets: nextSets,
                    prescription: null,
                    complete: nextSets.length >= s.target_sets,
                  }
                }))
              }}
              onMarkComplete={() => {
                const ex = exStates[activeIdx]
                if (ex) addSkipped(sessionId, ex.exercise_id)
                setExStates(prev => prev.map((s, i) =>
                  i === activeIdx ? { ...s, complete: true } : s
                ))
                // Auto-advance to next incomplete
                const nextIdx = exStates.findIndex((s, i) => i > activeIdx && !s.complete)
                if (nextIdx >= 0) setActiveIdx(nextIdx)
              }}
              onModeChange={(mode) => {
                setExStates(prev => prev.map((s, i) =>
                  i === activeIdx ? { ...s, training_mode: mode, prescription: null } : s
                ))
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
  onModeChange,
}: {
  sessionId: number
  state: ExerciseState
  onSetLogged: (set: LoggedSet) => void
  onMarkComplete: () => void
  onModeChange: (mode: 'heavy' | 'volume') => void
}) {
  const metricMode = state.set_metric_mode || 'reps'
  const loadMode = state.load_input_mode || 'external_weight'
  const showSecs = metricMode === 'duration' || metricMode === 'hybrid'
  const showReps = metricMode === 'reps' || metricMode === 'hybrid'
  const showWeight = loadMode !== 'bodyweight'
  const repsOnlyMode = metricMode === 'reps'

  const [weight, setWeight] = useState('')
  const [reps, setReps] = useState('')
  const [secs, setSecs] = useState('')
  const [rir, setRir] = useState('')
  const [logging, setLogging] = useState(false)
  const [adjusting, setAdjusting] = useState(false)
  const adjustTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Pre-fill from prescription (reps-based curve only)
  useEffect(() => {
    if (!repsOnlyMode) return
    if (state.prescription?.next_set) {
      const ns = state.prescription.next_set
      if (ns.proposed_weight != null) setWeight(String(Math.round(ns.proposed_weight)))
      if (ns.target_reps != null) setReps(String(ns.target_reps))
      if (ns.target_rir != null) setRir(String(ns.target_rir))
    }
  }, [state.prescription, repsOnlyMode])

  // Pre-fill secs from the last logged set (simple "repeat last" hint)
  useEffect(() => {
    if (!showSecs) return
    const last = state.sets[state.sets.length - 1]
    if (last?.duration_secs != null && last.duration_secs > 0) {
      setSecs(prev => prev === '' ? String(last.duration_secs) : prev)
    }
  }, [state.sets, showSecs])

  // Clean up debounce timer on unmount
  useEffect(() => () => { if (adjustTimer.current) clearTimeout(adjustTimer.current) }, [])

  // Re-prescribe when user enters a different weight (reps-only exercises)
  const handleWeightChange = (value: string) => {
    setWeight(value)
    if (!repsOnlyMode) return

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
          training_mode: state.training_mode,
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
    const w = showWeight ? parseFloat(weight) : 0
    const r = showReps ? parseInt(reps, 10) : 0
    const d = showSecs ? parseInt(secs, 10) : null
    const ri = parseFloat(rir)
    if (showWeight && isNaN(w)) return
    if (showReps && isNaN(r)) return
    if (showSecs && (d == null || isNaN(d))) return
    if (isNaN(ri)) return

    // Derive rep_completion from prescription range (reps-based only)
    const minReps = rx?.next_set?.acceptable_rep_min
    const repCompletion = repsOnlyMode && minReps != null
      ? (r >= minReps ? 'full' : 'partial')
      : 'full'

    setLogging(true)
    try {
      const payload: Parameters<typeof addWorkoutSet>[1] = {
        exercise_id: state.exercise_id,
        weight: showWeight ? w : null,
        reps: showReps ? r : null,
        duration_secs: showSecs ? d : null,
        rir: ri,
        training_mode: state.training_mode,
        rep_completion: repCompletion,
      }
      const result = await addWorkoutSet(sessionId, payload)
      onSetLogged({
        id: result.id,
        weight: showWeight ? w : 0,
        reps: showReps ? r : 0,
        rir: ri,
        duration_secs: showSecs ? d : null,
      })
      // Clear fields for next set
      setWeight('')
      setReps('')
      setSecs('')
      setRir('')
    } catch {
      // TODO: show error
    } finally {
      setLogging(false)
    }
  }

  const rx = state.prescription
  const canToggleMode = state.allow_heavy_loading && state.sets.length === 0
  const canLog =
    !logging
    && (!showWeight || weight !== '')
    && (!showReps || reps !== '')
    && (!showSecs || secs !== '')
    && rir !== ''

  // ── Curve-first UI state (only for reps + external-weight exercises) ──
  const useCurvePane = repsOnlyMode && !rx?.is_bodyweight && !state.complete
  const [curveMode, setCurveMode] = useState<'pre' | 'logging'>('pre')
  const [sparkWeight, setSparkWeight] = useState(0)
  const [sparkReps, setSparkReps] = useState(0)

  // Seed spark whenever prescription changes (new set / refit).
  useEffect(() => {
    if (!useCurvePane || !rx) return
    let w = 0
    let r = 0
    if (rx.next_set?.proposed_weight != null) {
      w = snapWeight(rx.next_set.proposed_weight)
      r = rx.next_set.target_reps
    } else if (rx.fallback_weight != null) {
      w = snapWeight(rx.fallback_weight)
      r = rx.scheme?.target_reps ?? 15
    } else if (rx.scheme?.target_reps != null) {
      w = 0
      r = rx.scheme.target_reps
    }
    setSparkWeight(w)
    setSparkReps(r)
    setCurveMode('pre')
  }, [rx, useCurvePane])

  const curveFit = useMemo(() => (
    rx?.curve ? { M: rx.curve.M, k: rx.curve.k, gamma: rx.curve.gamma } : null
  ), [rx?.curve])
  const priorCurveFit = useMemo(() => (
    rx?.curve_prior
      ? { M: rx.curve_prior.M, k: rx.curve_prior.k, gamma: rx.curve_prior.gamma }
      : null
  ), [rx?.curve_prior])
  const completedSetsData = useMemo(() => (
    state.sets.map(s => ({ weight: s.weight, reps: s.reps, rir: s.rir }))
  ), [state.sets])
  const useCompletedCurvePane = (
    repsOnlyMode && !rx?.is_bodyweight && state.complete && !!rx?.curve
  )
  const observations = rx?.observations ?? []
  const schemeRir = rx?.next_set?.target_rir ?? rx?.scheme?.target_rir ?? 3
  const schemeSetNumber = (
    rx?.next_set?.set_number
    ?? rx?.scheme?.set_number
    ?? state.sets.length + 1
  )
  const bootstrapTargetReps = rx?.scheme?.target_reps ?? rx?.next_set?.target_reps ?? 15

  const handleSparkChange = useCallback((w: number, r: number) => {
    setSparkWeight(w)
    setSparkReps(r)
  }, [])

  const handleGo = useCallback(() => {
    // In bootstrap mode reps are the scheme target until the user drags them;
    // in curve mode the Y is already the predicted reps.
    if (!curveFit && sparkReps <= 0) setSparkReps(bootstrapTargetReps)
    // Solve a cleaner starting Y using the curve at the snapped weight.
    if (curveFit && sparkWeight > 0) {
      // Keep the rep target at what the curve predicts at the chosen weight
      // minus the scheme RIR, so the user's first logged-reps estimate is honest.
      // We don't overwrite sparkReps here; CurvePane already kept it in sync.
    }
    setCurveMode('logging')
  }, [curveFit, sparkWeight, sparkReps, bootstrapTargetReps])

  const handleConfirmRir = useCallback(async (rirVal: ConfirmedRir) => {
    if (logging) return
    const w = snapWeight(sparkWeight)
    const r = Math.max(1, Math.round(sparkReps))
    const minReps = rx?.next_set?.acceptable_rep_min ?? rx?.scheme?.acceptable_rep_min
    const repCompletion = minReps != null
      ? (r >= minReps ? 'full' : 'partial')
      : 'full'
    setLogging(true)
    try {
      const result = await addWorkoutSet(sessionId, {
        exercise_id: state.exercise_id,
        weight: w,
        reps: r,
        duration_secs: null,
        rir: rirVal,
        training_mode: state.training_mode,
        rep_completion: repCompletion,
      })
      onSetLogged({
        id: result.id,
        weight: w,
        reps: r,
        rir: rirVal,
        duration_secs: null,
      })
      // Switch back to pre for the next set; the new rx will reseed the spark.
      setCurveMode('pre')
    } catch {
      // best effort — stay in logging mode so the user can retry
    } finally {
      setLogging(false)
    }
  }, [sparkWeight, sparkReps, rx, sessionId, state.exercise_id, state.training_mode, logging, onSetLogged])
  return (
    <div className="space-y-3">
      {/* Training mode toggle (only before first set on allow_heavy exercises) */}
      {state.allow_heavy_loading && (
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
            <button
              type="button"
              disabled={!canToggleMode}
              onClick={() => onModeChange('volume')}
              className={`rounded-md px-3 py-1 text-[11px] font-medium transition-colors ${
                state.training_mode === 'volume'
                  ? 'bg-white text-gray-900 shadow-sm'
                  : canToggleMode
                    ? 'text-gray-500 hover:text-gray-700'
                    : 'text-gray-300'
              }`}
            >
              Volume
            </button>
            <button
              type="button"
              disabled={!canToggleMode || !state.heavy_available}
              onClick={() => onModeChange('heavy')}
              className={`rounded-md px-3 py-1 text-[11px] font-medium transition-colors ${
                state.training_mode === 'heavy'
                  ? 'bg-red-600 text-white shadow-sm'
                  : canToggleMode && state.heavy_available
                    ? 'text-gray-500 hover:text-gray-700'
                    : 'text-gray-300'
              }`}
            >
              Heavy
            </button>
          </div>
          {state.training_mode === 'heavy' && (
            <span className="text-[10px] font-medium text-red-600">🔥 Heavy mode</span>
          )}
          {!state.heavy_available && state.allow_heavy_loading && state.training_mode !== 'heavy' && (
            <span className="text-[10px] text-gray-400" title={state.heavy_blocked_reason ?? ''}>
              {state.heavy_blocked_reason ?? 'Heavy unavailable'}
            </span>
          )}
          {!canToggleMode && state.sets.length > 0 && (
            <span className="text-[10px] text-gray-400">Mode locked after first set</span>
          )}
        </div>
      )}
      {/* Logged sets summary (hidden when the completed curve pane handles it) */}
      {state.sets.length > 0 && !useCompletedCurvePane && (
        <div className="space-y-1">
          {state.sets.map((s, i) => {
            const parts: string[] = []
            if (showWeight && s.weight > 0) parts.push(`${s.weight} lb`)
            if (showSecs && s.duration_secs != null && s.duration_secs > 0) parts.push(`${s.duration_secs}s`)
            if (showReps && s.reps > 0) parts.push(`${s.reps} reps`)
            return (
              <div key={s.id} className="flex items-center gap-3 rounded-lg bg-gray-50 px-3 py-1.5 text-xs text-gray-700">
                <span className="font-medium text-gray-500">Set {i + 1}</span>
                <span>{parts.join(' × ') || '—'}</span>
                <span className="text-gray-400">RIR {s.rir}</span>
              </div>
            )
          })}
        </div>
      )}

      {/* Completed curve pane — colored sparks, today's + prior curves, history */}
      {useCompletedCurvePane && (
        <CurvePane
          mode="completed"
          curve={curveFit}
          priorCurve={priorCurveFit}
          bootstrapTargetReps={bootstrapTargetReps}
          observations={observations}
          sparkWeight={0}
          sparkReps={0}
          schemeRir={schemeRir}
          schemeSetNumber={schemeSetNumber}
          onSparkChange={() => {}}
          onGo={() => {}}
          onConfirmRir={() => {}}
          completedSets={completedSetsData}
        />
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

      {state.complete && !state.inflection_detected && state.sets.length >= 3 && !useCompletedCurvePane && (
        <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
          <p className="text-xs font-medium text-gray-800">
            ✓ Exercise complete ({state.sets.length} sets)
          </p>
        </div>
      )}

      {/* Prescription / Next set guidance */}
      {!state.complete && (
        <>
          {state.prescribing && !rx && (
            <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
              <p className="text-xs italic text-gray-400">Computing prescription...</p>
            </div>
          )}

          {rx && useCurvePane ? (
            <CurvePane
              mode={curveMode}
              curve={curveFit}
              bootstrapTargetReps={bootstrapTargetReps}
              observations={observations}
              sparkWeight={sparkWeight}
              sparkReps={sparkReps}
              schemeRir={schemeRir}
              schemeSetNumber={schemeSetNumber}
              onSparkChange={handleSparkChange}
              onGo={handleGo}
              onConfirmRir={handleConfirmRir}
              submitting={logging}
            />
          ) : (
            <>
              {rx && rx.is_bodyweight && rx.suggestion && !rx.exercise_complete && repsOnlyMode && (
                <div className="rounded-xl border border-sky-200 bg-sky-50 px-4 py-3">
                  <p className="text-xs font-medium text-sky-800">
                    Bodyweight — Set {state.sets.length + 1} of {rx.suggestion.sets}
                  </p>
                  <p className="mt-0.5 text-xs text-sky-700">
                    {rx.suggestion.reps_per_set} reps
                  </p>
                </div>
              )}

              {!repsOnlyMode && (
                <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                  <p className="text-xs text-gray-700">
                    {metricMode === 'duration'
                      ? `Timed exercise — Set ${state.sets.length + 1} of ${state.target_sets}. Log duration in seconds.`
                      : `Set ${state.sets.length + 1} of ${state.target_sets}.`}
                  </p>
                </div>
              )}

              {/* Input fields */}
              <div className="flex items-end gap-2">
                {showWeight && (
                  <div className="flex-1">
                    <label className="block text-[10px] font-medium text-gray-500">Weight (lb)</label>
                    <input
                      type="text"
                      inputMode="decimal"
                      pattern="[0-9]*\.?[0-9]*"
                      value={weight}
                      onChange={e => handleWeightChange(e.target.value)}
                      onFocus={moveCursorToEnd}
                      className="mt-0.5 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm tabular-nums focus:border-gray-500 focus:ring-1 focus:ring-gray-400"
                      placeholder="0"
                    />
                  </div>
                )}
                {showSecs && (
                  <div className="w-20">
                    <label className="block text-[10px] font-medium text-gray-500">Secs</label>
                    <input
                      type="text"
                      inputMode="numeric"
                      pattern="[0-9]*"
                      value={secs}
                      onChange={e => setSecs(e.target.value)}
                      onFocus={moveCursorToEnd}
                      className="mt-0.5 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm tabular-nums focus:border-gray-500 focus:ring-1 focus:ring-gray-400"
                      placeholder="0"
                    />
                  </div>
                )}
                {showReps && (
                  <div className="w-20">
                    <label className="block text-[10px] font-medium text-gray-500">
                      Reps{adjusting && <span className="ml-1 text-blue-400">…</span>}
                    </label>
                    <input
                      type="text"
                      inputMode="numeric"
                      pattern="[0-9]*"
                      value={reps}
                      onChange={e => setReps(e.target.value)}
                      onFocus={moveCursorToEnd}
                      className={`mt-0.5 w-full rounded-lg border px-3 py-2 text-sm tabular-nums focus:border-gray-500 focus:ring-1 focus:ring-gray-400 ${
                        adjusting ? 'border-blue-300 bg-blue-50' : 'border-gray-300'
                      }`}
                      placeholder="0"
                    />
                  </div>
                )}
                <div className="w-16">
                  <label className="block text-[10px] font-medium text-gray-500">
                    RIR{adjusting && <span className="ml-1 text-blue-400">…</span>}
                  </label>
                  <input
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    value={rir}
                    onChange={e => setRir(e.target.value)}
                    onFocus={moveCursorToEnd}
                    className={`mt-0.5 w-full rounded-lg border px-3 py-2 text-sm tabular-nums focus:border-gray-500 focus:ring-1 focus:ring-gray-400 ${
                      adjusting ? 'border-blue-300 bg-blue-50' : 'border-gray-300'
                    }`}
                    placeholder="0"
                  />
                </div>
                <button
                  type="button"
                  onClick={handleLogSet}
                  disabled={!canLog}
                  className="rounded-lg bg-gray-900 px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-gray-800 disabled:opacity-40"
                >
                  {logging ? '...' : 'Log'}
                </button>
              </div>
            </>
          )}

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

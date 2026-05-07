import type { FocusEvent } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import WorkoutSetEditor from './WorkoutSetEditor'
import { type ConfirmedRir } from './CurvePane'
import CurvePaneWithFatigue from './CurvePaneWithFatigue'
import { snapWeight } from '../lib/weight_grid'
import {
  asEntered,
  asRepsDone,
  asRir,
  type EnteredWeightLb,
  type RepsDone,
  type Rir,
} from '../lib/units'
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
  type WkSession,
  type WkSetDetail,
} from '../api'
import { ExerciseReadinessSparkline, ReadinessBanner } from './ReadinessBanner'

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

// ── Duration timer ──
//
// 5-second countdown ("get into position") then a count-up clock for
// duration-mode sets (e.g., weighted plank). On Stop we hand the elapsed
// seconds back to the parent via ``onCommit``; the parent owns the actual
// secs input field so the user can still hand-edit afterwards.

const COUNTDOWN_SECS = 5

// ── Audio cue helper ────────────────────────────────────────────────────
//
// Plays sine-wave beeps via the Web Audio API so the user can keep their
// phone holstered during a duration set and get audible cues through their
// headphones / earbuds.
//
//   • 5 s pre-roll: 3 mid-pitch beeps then 1 high-pitch "go" beep.
//   • Every 10 s during the run: 1 mid-pitch beep.
//   • At the prescribed target time: 3 fast high-pitch beeps.
//   • After the target: continue the every-10-s mid-pitch beeps until stop.
//
// Browsers require AudioContext to be created from a user gesture, so we
// instantiate it on the first Start click and cache it for the lifetime
// of the component.
function makeAudioPlayer() {
  type AudioCtxCtor = typeof AudioContext
  const Ctx: AudioCtxCtor | undefined =
    typeof window !== 'undefined'
      ? (window.AudioContext
          ?? (window as unknown as { webkitAudioContext?: AudioCtxCtor })
            .webkitAudioContext)
      : undefined
  if (!Ctx) return null
  const ctx = new Ctx()

  const beep = (freq: number, durMs: number, atOffsetMs = 0, gain = 0.18) => {
    const t0 = ctx.currentTime + atOffsetMs / 1000
    const osc = ctx.createOscillator()
    const env = ctx.createGain()
    osc.type = 'sine'
    osc.frequency.value = freq
    // Short attack/decay envelope to avoid clicks.
    env.gain.setValueAtTime(0, t0)
    env.gain.linearRampToValueAtTime(gain, t0 + 0.01)
    env.gain.linearRampToValueAtTime(gain, t0 + durMs / 1000 - 0.02)
    env.gain.linearRampToValueAtTime(0, t0 + durMs / 1000)
    osc.connect(env).connect(ctx.destination)
    osc.start(t0)
    osc.stop(t0 + durMs / 1000 + 0.05)
  }

  return {
    resume: () => { if (ctx.state === 'suspended') void ctx.resume() },
    close: () => { void ctx.close() },
    // Mid-pitch tick beep used for countdown ticks 3..1 and every-10s marks.
    tick: () => beep(660, 110),
    // High-pitch "go" / target beep — an octave above tick.
    chirp: (atOffsetMs = 0) => beep(1320, 140, atOffsetMs, 0.22),
    // Three fast high-pitch beeps signalling the prescribed target was hit.
    triple: () => {
      beep(1320, 90, 0, 0.22)
      beep(1320, 90, 140, 0.22)
      beep(1320, 90, 280, 0.22)
    },
  }
}

type AudioPlayer = NonNullable<ReturnType<typeof makeAudioPlayer>>

function DurationTimer({
  onCommit,
  targetSecs,
}: {
  onCommit: (secs: number) => void
  /** Prescribed target in seconds. When the run timer crosses this value,
   *  the audio scheduler plays the "target reached" trill. */
  targetSecs?: number | null
}) {
  // Single timeline: startedAt = when the user pressed Start.
  // First COUNTDOWN_SECS seconds = countdown; everything after = run time.
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const audioRef = useRef<AudioPlayer | null>(null)
  // Cue-deduplication: each cue has a stable id ("pre-3", "run-10",
  // "target", "post-10") so the scheduler doesn't fire twice if a render
  // straddles the trigger time.
  const playedRef = useRef<Set<string>>(new Set())
  // Track whether the target-reached trill has fired so we know when to
  // start emitting "post-10" beeps relative to the target.
  const targetHitRef = useRef(false)

  useEffect(() => {
    if (startedAt == null) return
    // High-frequency tick (50 ms) so audio cues fire close to their
    // intended sample times even when the tab is backgrounded briefly.
    tickRef.current = setInterval(() => setNow(Date.now()), 50)
    return () => { if (tickRef.current) clearInterval(tickRef.current) }
  }, [startedAt])

  // Schedule audio cues based on elapsed time since startedAt.
  useEffect(() => {
    if (startedAt == null) return
    const audio = audioRef.current
    if (!audio) return
    const elapsed = (now - startedAt) / 1000
    const played = playedRef.current

    const fire = (id: string, action: () => void) => {
      if (played.has(id)) return
      played.add(id)
      action()
    }

    // ── Pre-roll cues: ticks at countdown=4,3,2 and "go" at countdown=1 ──
    //
    // Countdown displays Math.ceil(COUNTDOWN_SECS - elapsed); we want the
    // beep to land exactly when the digit changes, i.e. at
    // elapsed = 1, 2, 3 s for the three mid ticks, and elapsed = 4 s for
    // the high "go" beep that announces the imminent run start.
    if (elapsed >= 1 && elapsed < COUNTDOWN_SECS) fire('pre-3', audio.tick)
    if (elapsed >= 2 && elapsed < COUNTDOWN_SECS) fire('pre-2', audio.tick)
    if (elapsed >= 3 && elapsed < COUNTDOWN_SECS) fire('pre-1', audio.tick)
    if (elapsed >= 4 && elapsed < COUNTDOWN_SECS) fire('pre-go', audio.chirp)

    if (elapsed < COUNTDOWN_SECS) return

    // ── Run cues: every 10 s and target-reached trill ──
    const runSec = elapsed - COUNTDOWN_SECS
    const target = targetSecs != null && targetSecs > 0 ? targetSecs : null

    // Every-10-s tick before target: 10, 20, 30, ... up to (but not
    // straddling) the target. After target we resume on the same
    // 10-s grid relative to the target itself.
    if (target == null || runSec < target) {
      const k = Math.floor(runSec / 10)
      if (k >= 1) fire(`run-${k * 10}`, audio.tick)
    }

    if (target != null && runSec >= target && !targetHitRef.current) {
      targetHitRef.current = true
      fire('target', audio.triple)
    }

    if (target != null && runSec >= target) {
      const post = runSec - target
      const k = Math.floor(post / 10)
      if (k >= 1) fire(`post-${k * 10}`, audio.tick)
    }
  }, [now, startedAt, targetSecs])

  const start = () => {
    // Lazy-create the AudioContext on the first user gesture so browsers
    // permit playback. Reuse across start/stop cycles.
    if (audioRef.current == null) audioRef.current = makeAudioPlayer()
    audioRef.current?.resume()
    playedRef.current = new Set()
    targetHitRef.current = false
    const t = Date.now()
    setNow(t)
    setStartedAt(t)
  }

  const stop = () => {
    if (startedAt != null) {
      const elapsedMs = Date.now() - startedAt
      const runMs = elapsedMs - COUNTDOWN_SECS * 1000
      if (runMs > 0) {
        const secs = Math.max(0, Math.round(runMs / 1000))
        onCommit(secs)
      }
    }
    setStartedAt(null)
  }

  // ── Render ──

  if (startedAt == null) {
    return (
      <button
        type="button"
        onClick={start}
        className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-800 transition-colors hover:bg-emerald-100"
        title="5 s countdown with audio cues, then count up"
      >
        ▶ Start
      </button>
    )
  }

  const elapsed = (now - startedAt) / 1000
  const isCountdown = elapsed < COUNTDOWN_SECS
  const runSec = Math.floor(elapsed - COUNTDOWN_SECS)
  const targetReached =
    targetSecs != null && targetSecs > 0 && !isCountdown && runSec >= targetSecs
  const display = isCountdown
    ? String(Math.max(0, Math.ceil(COUNTDOWN_SECS - elapsed)))
    : String(runSec)

  // Fullscreen overlay so the timer is huge and easy to see/touch from
  // across the room. The Stop button takes up the bottom half of the
  // screen for one-tap-with-oily-hands operation.
  const overlayBg = isCountdown
    ? 'bg-amber-500'
    : targetReached
      ? 'bg-rose-600'
      : 'bg-emerald-600'
  const heading = isCountdown
    ? 'Get ready'
    : targetReached
      ? `Target ${targetSecs}s — keep going or stop`
      : targetSecs != null && targetSecs > 0
        ? `Target ${targetSecs}s`
        : 'Hold'

  return (
    <div
      className={`fixed inset-0 z-50 flex flex-col items-center justify-between p-6 text-white ${overlayBg}`}
      style={{ touchAction: 'manipulation' }}
    >
      <div className="pt-8 text-center">
        <p className="text-base font-semibold uppercase tracking-widest opacity-80">
          {heading}
        </p>
      </div>
      <div className="flex flex-1 items-center justify-center">
        <span
          className="select-none tabular-nums font-bold leading-none"
          style={{ fontSize: 'clamp(8rem, 35vw, 22rem)' }}
        >
          {display}
          {!isCountdown && <span className="ml-2 text-3xl opacity-70">s</span>}
        </span>
      </div>
      <button
        type="button"
        onClick={stop}
        className="mb-6 w-full max-w-md rounded-2xl bg-white/15 py-8 text-2xl font-bold text-white shadow-lg ring-2 ring-white/40 transition-colors hover:bg-white/25 active:bg-white/30"
      >
        ⏹ Stop
      </button>
    </div>
  )
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
  burnout_available: boolean
  burnout_blocked_reason: string | null
  load_input_mode: string
  set_metric_mode: string
  target_sets: number
  training_mode: 'heavy' | 'volume' | 'burnout'
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
  // Latest WkSession including readiness β fields. Drives the readiness
  // banner above the exercise tabs and the per-exercise sparkline.
  const [wkSession, setWkSession] = useState<WkSession | null>(null)
  const initRef = useRef(false)
  const readinessTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const refreshWkSession = useCallback(async () => {
    try {
      const s = await getWorkoutSession(sessionId)
      setWkSession(s)
    } catch { /* ignore */ }
  }, [sessionId])

  // After a set log, the backend refits readiness β in a BackgroundTask.
  // Wait briefly so the refit has committed, then re-pull the session so
  // the banner reflects the fresh β. Debounced across rapid set logs.
  const scheduleReadinessRefresh = useCallback(() => {
    if (readinessTimerRef.current) clearTimeout(readinessTimerRef.current)
    readinessTimerRef.current = setTimeout(() => {
      readinessTimerRef.current = null
      refreshWkSession()
    }, 1500)
  }, [refreshWkSession])

  useEffect(() => {
    return () => {
      if (readinessTimerRef.current) clearTimeout(readinessTimerRef.current)
    }
  }, [])

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
        setWkSession(session)
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
        )?.training_mode as 'heavy' | 'volume' | 'burnout' | undefined

        const heavyAvailable = ex.heavy_available ?? false
        const burnoutAvailable = ex.burnout_available ?? false
        const defaultMode: 'heavy' | 'volume' | 'burnout' =
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
          burnout_available: burnoutAvailable,
          burnout_blocked_reason: ex.burnout_blocked_reason ?? null,
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
      setWkSession(session)
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
    if (fetchingRef.current === ex.exercise_id) return

    fetchingRef.current = ex.exercise_id
    const exerciseId = ex.exercise_id

    setExStates(prev => prev.map((s, i) => i === idx ? { ...s, prescribing: true } : s))

    const priorSets = ex.sets.map(s => ({
      weight: s.weight,
      // For rep exercises ``reps`` carries the y-axis quantity; for
      // duration/distance the backend also accepts ``endurance_value``.
      // We send both so either wire contract works.
      reps: s.reps,
      endurance_value: s.duration_secs != null ? s.duration_secs : s.reps,
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
        const burnoutAvailable = chosen.burnout_available ?? false
        const newState: ExerciseState = {
          exercise_id: chosen.exercise_id,
          name: chosen.name,
          allow_heavy_loading: chosen.allow_heavy_loading,
          is_bodyweight: chosen.is_bodyweight ?? false,
          heavy_available: heavyAvailable,
          heavy_blocked_reason: chosen.heavy_blocked_reason ?? null,
          burnout_available: burnoutAvailable,
          burnout_blocked_reason: chosen.burnout_blocked_reason ?? null,
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
          {/* Readiness β banner — live multiplicative readiness from
              today's RPE-tagged sets, plus a 14-day session-level trend. */}
          {wkSession && (
            <div className="mb-3">
              <ReadinessBanner session={wkSession} title="Today" />
            </div>
          )}

          {/* Exercise tabs */}
          <div className="mb-4 flex flex-wrap gap-1.5 pb-1">
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
              wkSession={wkSession}
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
                // Pull the freshly-refit β a moment later so the banner
                // updates without slowing down the prescribe-next call.
                scheduleReadinessRefresh()
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
  wkSession,
  state,
  onSetLogged,
  onMarkComplete,
  onModeChange,
}: {
  sessionId: number
  wkSession: WkSession | null
  state: ExerciseState
  onSetLogged: (set: LoggedSet) => void
  onMarkComplete: () => void
  onModeChange: (mode: 'heavy' | 'volume' | 'burnout') => void
}) {
  const metricMode = state.set_metric_mode || 'reps'
  const loadMode = state.load_input_mode || 'external_weight'
  const showSecs = metricMode === 'duration'
  const showReps = metricMode === 'reps'
  const showDistance = metricMode === 'distance'
  const showWeight = loadMode !== 'bodyweight'
  // Reps/distance use integer endurance — both can drive the strength curve.
  // Duration also rides the curve but stores seconds.
  const repsOnlyMode = metricMode === 'reps'

  const [weight, setWeight] = useState('')
  const [reps, setReps] = useState('')
  const [secs, setSecs] = useState('')
  const [dist, setDist] = useState('')
  const [rir, setRir] = useState('')
  const [logging, setLogging] = useState(false)
  const [adjusting, setAdjusting] = useState(false)
  const adjustTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Pre-fill from prescription. For reps-based exercises this fills
  // weight + reps + RIR; for duration/distance it fills weight, the
  // endurance target into the relevant field, and RIR.
  useEffect(() => {
    if (!state.prescription?.next_set) return
    const ns = state.prescription.next_set
    if (ns.proposed_weight != null) setWeight(String(Math.round(ns.proposed_weight)))
    if (ns.target_rir != null) setRir(String(ns.target_rir))
    const target = ns.target_endurance ?? ns.target_reps
    if (target != null) {
      if (showReps) setReps(String(Math.round(target)))
      else if (showSecs) setSecs(String(Math.round(target)))
      else if (showDistance) setDist(String(Math.round(target)))
    }
  }, [state.prescription, showReps, showSecs, showDistance])

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
    const ds = showDistance ? parseInt(dist, 10) : null
    const ri = parseFloat(rir)
    if (showWeight && isNaN(w)) return
    if (showReps && isNaN(r)) return
    if (showSecs && (d == null || isNaN(d))) return
    if (showDistance && (ds == null || isNaN(ds))) return
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
        distance_steps: showDistance ? ds : null,
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
      setDist('')
      setRir('')
    } catch {
      // TODO: show error
    } finally {
      setLogging(false)
    }
  }

  const rx = state.prescription
  const canToggleMode = state.sets.length === 0
    && (state.allow_heavy_loading || state.burnout_available)
  const canLog =
    !logging
    && (!showWeight || weight !== '')
    && (!showReps || reps !== '')
    && (!showSecs || secs !== '')
    && (!showDistance || dist !== '')
    && rir !== ''

  // ── Curve-first UI state (only for reps + external-weight exercises) ──
  const useCurvePane = repsOnlyMode && !rx?.is_bodyweight && !state.complete
  const [curveMode, setCurveMode] = useState<'pre' | 'logging'>('pre')
  const [sparkWeight, setSparkWeight] = useState<EnteredWeightLb>(asEntered(0))
  const [sparkReps, setSparkReps] = useState<RepsDone>(asRepsDone(0))

  // Seed spark whenever prescription changes (new set / refit).
  useEffect(() => {
    if (!useCurvePane || !rx) return
    let w: EnteredWeightLb = asEntered(0)
    let r: RepsDone = asRepsDone(0)
    if (rx.next_set?.proposed_weight != null) {
      w = snapWeight(rx.next_set.proposed_weight)
      r = asRepsDone(rx.next_set.target_reps)
    } else if (rx.fallback_weight != null) {
      w = snapWeight(rx.fallback_weight)
      r = asRepsDone(rx.scheme?.target_reps ?? 15)
    } else if (rx.scheme?.target_reps != null) {
      w = asEntered(0)
      r = asRepsDone(rx.scheme.target_reps)
    }
    setSparkWeight(w)
    setSparkReps(r)
    setCurveMode('pre')
  }, [rx, useCurvePane])

  const curveFit = useMemo(() => (
    rx?.curve ? {
      M: rx.curve.M,
      k: rx.curve.k,
      gamma: rx.curve.gamma,
      delta: rx.curve.delta,
      weight_space: rx.curve.weight_space,
      bw_offset: rx.curve.bw_offset,
      ext_mult: rx.curve.ext_mult,
      max_observed_weight: rx.curve.max_observed_weight,
    } : null
  ), [rx?.curve])
  const priorCurveFit = useMemo(() => (
    rx?.curve_prior ? {
      M: rx.curve_prior.M,
      k: rx.curve_prior.k,
      gamma: rx.curve_prior.gamma,
      delta: rx.curve_prior.delta,
      weight_space: rx.curve_prior.weight_space,
      bw_offset: rx.curve_prior.bw_offset,
      ext_mult: rx.curve_prior.ext_mult,
      max_observed_weight: rx.curve_prior.max_observed_weight,
    } : null
  ), [rx?.curve_prior])
  const completedSetsData = useMemo(() => (
    state.sets.map(s => ({
      weight: asEntered(s.weight),
      reps: asRepsDone(s.reps),
      rir: asRir(s.rir),
    }))
  ), [state.sets])
  const useCompletedCurvePane = (
    repsOnlyMode && !rx?.is_bodyweight && state.complete && !!rx?.curve
  )
  const observations = useMemo(() => (
    (rx?.observations ?? []).map(o => ({
      weight: asEntered(o.weight),
      reps: asRepsDone(o.reps),
      rir: o.rir == null ? undefined : asRir(o.rir),
      age_days: o.age_days,
    }))
  ), [rx?.observations])
  const schemeRir: Rir = asRir(rx?.next_set?.target_rir ?? rx?.scheme?.target_rir ?? 3)
  const schemeSetNumber = (
    rx?.next_set?.set_number
    ?? rx?.scheme?.set_number
    ?? state.sets.length + 1
  )
  const bootstrapTargetReps: RepsDone = asRepsDone(
    rx?.scheme?.target_reps ?? rx?.next_set?.target_reps ?? 15,
  )

  const handleSparkChange = useCallback((w: EnteredWeightLb, r: RepsDone) => {
    setSparkWeight(w)
    setSparkReps(r)
  }, [])

  const handleGo = useCallback(() => {
    // In bootstrap mode reps are the scheme target until the user drags them;
    // in curve mode the Y is already the predicted reps.
    if (!curveFit && (sparkReps as number) <= 0) setSparkReps(bootstrapTargetReps)
    // Solve a cleaner starting Y using the curve at the snapped weight.
    if (curveFit && (sparkWeight as number) > 0) {
      // Keep the rep target at what the curve predicts at the chosen weight
      // minus the scheme RIR, so the user's first logged-reps estimate is honest.
      // We don't overwrite sparkReps here; CurvePane already kept it in sync.
    }
    setCurveMode('logging')
  }, [curveFit, sparkWeight, sparkReps, bootstrapTargetReps])

  const handleConfirmRir = useCallback(async (rirVal: ConfirmedRir) => {
    if (logging) return
    const w = snapWeight(sparkWeight)
    // sparkReps is maintained in reps_done units by CurvePane (β-shifted
    // reps expected for the current set). Store it directly; the athlete's
    // reported RIR is stored separately. Because sparkReps is typed
    // `RepsDone` (branded), this line cannot accidentally be fed an rtf
    // value — the whole point of the brand.
    const r: RepsDone = asRepsDone(Math.max(1, Math.round(sparkReps as number)))
    const minReps = rx?.next_set?.acceptable_rep_min ?? rx?.scheme?.acceptable_rep_min
    const repCompletion = minReps != null
      ? ((r as number) >= minReps ? 'full' : 'partial')
      : 'full'
    setLogging(true)
    try {
      const result = await addWorkoutSet(sessionId, {
        exercise_id: state.exercise_id,
        weight: w as number,
        reps: r as number,
        duration_secs: null,
        rir: rirVal,
        training_mode: state.training_mode,
        rep_completion: repCompletion,
      })
      onSetLogged({
        id: result.id,
        weight: w as number,
        reps: r as number,
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
      {/* Per-exercise readiness β trend sparkline */}
      {wkSession && (
        <ExerciseReadinessSparkline
          session={wkSession}
          exerciseId={state.exercise_id}
          exerciseName={state.name}
        />
      )}
      {/* Training mode toggle (only before first set when heavy or burnout is available) */}
      {(state.allow_heavy_loading || state.burnout_available) && (
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
            {state.allow_heavy_loading && (
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
            )}
            <button
              type="button"
              disabled={!canToggleMode || !state.burnout_available}
              onClick={() => onModeChange('burnout')}
              className={`rounded-md px-3 py-1 text-[11px] font-medium transition-colors ${
                state.training_mode === 'burnout'
                  ? 'bg-orange-500 text-white shadow-sm'
                  : canToggleMode && state.burnout_available
                    ? 'text-gray-500 hover:text-gray-700'
                    : 'text-gray-300'
              }`}
              title={state.burnout_blocked_reason ?? 'One AMRAP set at ~½ recent max to anchor the light side of the curve'}
            >
              Burnout
            </button>
          </div>
          {state.training_mode === 'heavy' && (
            <span className="text-[10px] font-medium text-red-600">🔥 Heavy mode</span>
          )}
          {state.training_mode === 'burnout' && (
            <span className="text-[10px] font-medium text-orange-600">💥 Burnout mode</span>
          )}
          {!state.heavy_available && state.allow_heavy_loading && state.training_mode !== 'heavy' && state.training_mode !== 'burnout' && (
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

      {/* Completed curve pane — colored sparks, today's + prior curves, history.
          CurvePaneWithFatigue draws the fatigue band as a companion envelope
          on the same chart, replacing what used to be a separate FatiguePane. */}
      {useCompletedCurvePane && (
        <CurvePaneWithFatigue
          exerciseId={state.exercise_id}
          mode="completed"
          curve={curveFit}
          priorCurve={priorCurveFit}
          bootstrapTargetReps={bootstrapTargetReps}
          observations={observations}
          sparkWeight={asEntered(0)}
          sparkReps={asRepsDone(0)}
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
            <CurvePaneWithFatigue
              exerciseId={state.exercise_id}
              mode={curveMode}
              curve={curveFit}
              bootstrapTargetReps={bootstrapTargetReps}
              observations={observations}
              sparkWeight={sparkWeight}
              sparkReps={sparkReps}
              schemeRir={schemeRir}
              schemeSetNumber={schemeSetNumber}
              acceptableRepMin={
                (rx?.next_set?.acceptable_rep_min
                 ?? rx?.scheme?.acceptable_rep_min) != null
                  ? asRepsDone(rx?.next_set?.acceptable_rep_min
                      ?? rx?.scheme?.acceptable_rep_min as number)
                  : undefined
              }
              acceptableRepMax={
                (rx?.next_set?.acceptable_rep_max
                 ?? rx?.scheme?.acceptable_rep_max) != null
                  ? asRepsDone(rx?.next_set?.acceptable_rep_max
                      ?? rx?.scheme?.acceptable_rep_max as number)
                  : undefined
              }
              onSparkChange={handleSparkChange}
              onGo={handleGo}
              onBack={() => setCurveMode('pre')}
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
                  <div className="flex items-end gap-2">
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
                    <DurationTimer
                      onCommit={(s) => setSecs(String(s))}
                      targetSecs={(() => {
                        const n = Number(secs)
                        return Number.isFinite(n) && n > 0 ? n : null
                      })()}
                    />
                  </div>
                )}
                {showDistance && (
                  <div className="w-20">
                    <label className="block text-[10px] font-medium text-gray-500">
                      Steps{adjusting && <span className="ml-1 text-blue-400">…</span>}
                    </label>
                    <input
                      type="text"
                      inputMode="numeric"
                      pattern="[0-9]*"
                      value={dist}
                      onChange={e => setDist(e.target.value)}
                      onFocus={moveCursorToEnd}
                      className={`mt-0.5 w-full rounded-lg border px-3 py-2 text-sm tabular-nums focus:border-gray-500 focus:ring-1 focus:ring-gray-400 ${
                        adjusting ? 'border-blue-300 bg-blue-50' : 'border-gray-300'
                      }`}
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

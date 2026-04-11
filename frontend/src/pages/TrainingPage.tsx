import { useCallback, useEffect, useState } from 'react'
import ScrollablePage from '../components/ScrollablePage'
import ActiveWorkoutCard from '../components/ActiveWorkoutCard'
import StrengthPlannerCard from '../components/StrengthPlannerCard'
import {
  getActivePlan,
  getWorkoutSession,
  quickStart,
  type ExerciseMenuItem,
} from '../api'

function today() {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}


function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-3 rounded-2xl border border-gray-200 bg-white p-5">
      <div className="h-5 w-32 animate-pulse rounded bg-gray-200" />
      {Array.from({ length: lines }).map((_, index) => (
        <div key={index} className={`h-4 animate-pulse rounded bg-gray-200 ${index === lines - 1 ? 'w-2/3' : 'w-full'}`} />
      ))}
    </div>
  )
}

export default function TrainingPage() {
  const [loading, setLoading] = useState(true)
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null)
  const [activeExercises, setActiveExercises] = useState<ExerciseMenuItem[]>([])
  const [starting, setStarting] = useState(false)

  // Restore active workout on mount (e.g., after page refresh)
  useEffect(() => {
    let cancelled = false
    getActivePlan(today()).then(plan => {
      if (cancelled) return
      if (plan && plan.status === 'in_progress' && plan.workout_session_id) {
        // Resume existing workout
        setActiveSessionId(plan.workout_session_id)
        // Reconstruct exercise list from session sets
        getWorkoutSession(plan.workout_session_id).then(session => {
          if (cancelled) return
          const exerciseMap = new Map<number, ExerciseMenuItem>()
          for (const s of session.sets) {
            if (!exerciseMap.has(s.exercise_id)) {
              exerciseMap.set(s.exercise_id, {
                exercise_id: s.exercise_id,
                name: s.exercise_name,
                allow_heavy_loading: true,
                is_bodyweight: false,
                load_input_mode: 'external_weight',
                has_curve_fit: false,
                days_since_trained: 0,
                recent_rpe_sets: 0,
              })
            }
          }
          // Also get exercises from plan
          if (plan.exercises) {
            for (const ex of plan.exercises) {
              if (!exerciseMap.has(ex.exercise_id)) {
                exerciseMap.set(ex.exercise_id, {
                  exercise_id: ex.exercise_id,
                  name: ex.exercise_name,
                  allow_heavy_loading: true,
                  is_bodyweight: false,
                  load_input_mode: 'external_weight',
                  has_curve_fit: false,
                  days_since_trained: 0,
                  recent_rpe_sets: 0,
                })
              }
            }
          }
          setActiveExercises(Array.from(exerciseMap.values()))
          setLoading(false)
        }).catch(() => {
          if (!cancelled) setLoading(false)
        })
      } else {
        setLoading(false)
      }
    }).catch(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  const handleStart = useCallback(async (exerciseIds: number[], exercises: ExerciseMenuItem[]) => {
    setStarting(true)
    try {
      const result = await quickStart(exerciseIds)
      setActiveSessionId(result.workout_session_id)
      setActiveExercises(exercises)
    } catch {
      // TODO: show error
    } finally {
      setStarting(false)
    }
  }, [])

  const handleFinish = useCallback(() => {
    setActiveSessionId(null)
    setActiveExercises([])
  }, [])

  const handleCancel = useCallback(() => {
    setActiveSessionId(null)
    setActiveExercises([])
  }, [])

  return (
    <ScrollablePage>
      <div className="space-y-4 pb-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-lg font-bold text-gray-900">Training</h1>
          <span className="text-xs tabular-nums text-gray-400">{today()}</span>
        </div>

        {loading ? (
          <div className="space-y-4">
            <CardSkeleton lines={5} />
            <CardSkeleton lines={8} />
          </div>
        ) : (
          <>
            {activeSessionId && activeExercises.length > 0 && (
              <ActiveWorkoutCard
                sessionId={activeSessionId}
                exercises={activeExercises}
                onFinish={handleFinish}
                onCancel={handleCancel}
              />
            )}

            {starting && (
              <div className="rounded-2xl border border-gray-200 bg-white p-5">
                <p className="text-xs text-gray-500 italic">Starting workout...</p>
              </div>
            )}

            <StrengthPlannerCard
              onStart={handleStart}
              disabled={activeSessionId !== null}
            />
          </>
        )}
      </div>
    </ScrollablePage>
  )
}


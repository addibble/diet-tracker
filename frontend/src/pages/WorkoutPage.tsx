import { useEffect, useMemo, useState } from 'react'
import {
  getTissueReadiness,
  getRoutine,
  getWorkoutSessions,
  getExerciseHistory,
  getExercises,
  type WkTissueReadiness,
  type WkRoutineExercise,
  type WkSession,
  type WkExerciseHistory,
  type WkExercise,
} from '../api'

// ── Helpers ──

function readinessColor(r: WkTissueReadiness): string {
  if (r.condition && (r.condition.status === 'injured' || r.condition.status === 'tender'))
    return 'border-purple-400 bg-purple-50'
  if (r.condition && r.condition.status === 'rehabbing')
    return 'border-purple-300 bg-purple-50/50'
  if (r.recovery_pct >= 100) return 'border-green-400 bg-green-50'
  if (r.recovery_pct >= 75) return 'border-yellow-400 bg-yellow-50'
  return 'border-red-400 bg-red-50'
}

function readinessDot(r: WkTissueReadiness): string {
  if (r.condition && r.condition.status === 'injured') return 'bg-purple-500'
  if (r.condition && r.condition.status === 'tender') return 'bg-purple-400'
  if (r.recovery_pct >= 100) return 'bg-green-500'
  if (r.recovery_pct >= 75) return 'bg-yellow-500'
  return 'bg-red-500'
}

function repDot(completion: string | null): string {
  if (completion === 'full') return 'bg-green-500'
  if (completion === 'partial') return 'bg-yellow-500'
  if (completion === 'failed') return 'bg-red-500'
  return 'bg-gray-300'
}

function hoursLabel(hours: number | null): string {
  if (hours === null) return 'Never'
  if (hours < 1) return '<1h ago'
  if (hours < 24) return `${Math.round(hours)}h ago`
  return `${Math.round(hours / 24)}d ago`
}

function groupSetsByExercise(sets: WkSession['sets']) {
  const map = new Map<string, typeof sets>()
  for (const s of sets) {
    const list = map.get(s.exercise_name) || []
    list.push(s)
    map.set(s.exercise_name, list)
  }
  return map
}

// ── Tissue Readiness Dashboard ──

function TissueReadinessCard({
  readiness,
}: {
  readiness: WkTissueReadiness[]
}) {
  const [expanded, setExpanded] = useState<string | null>(null)

  // Show only top-level groups (no parent) that are tissue_groups
  const topLevel = useMemo(
    () => readiness.filter((r) => r.tissue.parent_id === null && r.tissue.type === 'tissue_group'),
    [readiness],
  )

  const childrenOf = useMemo(() => {
    const map = new Map<number, WkTissueReadiness[]>()
    for (const r of readiness) {
      if (r.tissue.parent_id !== null) {
        const list = map.get(r.tissue.parent_id) || []
        list.push(r)
        map.set(r.tissue.parent_id, list)
      }
    }
    return map
  }, [readiness])

  if (topLevel.length === 0) {
    return (
      <section className="bg-white border border-gray-200 rounded-2xl p-5">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
          Tissue Readiness
        </p>
        <p className="text-sm text-gray-500 mt-2">
          No tissue data yet. Log a workout with tissue mappings to see readiness.
        </p>
      </section>
    )
  }

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
        Tissue Readiness
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2 mt-3">
        {topLevel.map((r) => (
          <div key={r.tissue.id}>
            <button
              onClick={() =>
                setExpanded(expanded === r.tissue.name ? null : r.tissue.name)
              }
              className={`w-full text-left rounded-xl border-2 p-3 transition-colors ${readinessColor(r)}`}
            >
              <div className="flex items-center gap-2">
                <span className={`w-2.5 h-2.5 rounded-full ${readinessDot(r)}`} />
                <span className="text-sm font-medium text-gray-800 truncate">
                  {r.tissue.display_name}
                </span>
              </div>
              <div className="mt-1 text-xs text-gray-500">
                {Math.round(r.recovery_pct)}% recovered
              </div>
              <div className="text-xs text-gray-400">
                {hoursLabel(r.hours_since)}
              </div>
              {r.condition && r.condition.status !== 'healthy' && (
                <span className="inline-block mt-1 text-[10px] font-medium uppercase tracking-wide text-purple-600 bg-purple-100 rounded px-1.5 py-0.5">
                  {r.condition.status}
                </span>
              )}
            </button>
            {expanded === r.tissue.name && (
              <div className="mt-1 ml-2 space-y-1">
                {(childrenOf.get(r.tissue.id) || []).map((child) => (
                  <div
                    key={child.tissue.id}
                    className="flex items-center gap-2 text-xs text-gray-600 py-0.5"
                  >
                    <span className={`w-2 h-2 rounded-full ${readinessDot(child)}`} />
                    <span className="truncate">{child.tissue.display_name}</span>
                    <span className="ml-auto text-gray-400">
                      {Math.round(child.recovery_pct)}%
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="flex gap-4 mt-3 text-[10px] text-gray-400">
        <span className="inline-flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-green-500" /> Ready
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-yellow-500" /> Almost
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-red-500" /> Recovering
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-purple-500" /> Injured
        </span>
      </div>
    </section>
  )
}

// ── Today's Suggested Workout ──

function SuggestedWorkoutCard({
  routine,
  readiness: _readiness,
}: {
  routine: WkRoutineExercise[]
  readiness: WkTissueReadiness[]
}) {
  const active = routine.filter((r) => r.active)

  if (active.length === 0) {
    return (
      <section className="bg-white border border-gray-200 rounded-2xl p-5">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
          Today's Workout
        </p>
        <p className="text-sm text-gray-500 mt-2">
          No routine set. Use the chat to set up your training routine.
        </p>
      </section>
    )
  }

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
        Today's Workout
      </p>
      <div className="mt-3 space-y-2">
        {active.map((re) => {
          const repRange = re.target_rep_min && re.target_rep_max
            ? `${re.target_sets}x${re.target_rep_min}-${re.target_rep_max}`
            : `${re.target_sets} sets`
          const lastWeight = re.last_performance?.sets?.[0]?.weight
          return (
            <div
              key={re.id}
              className="flex items-center gap-3 rounded-xl bg-gray-50 border border-gray-100 px-3 py-2"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-800 truncate">
                  {re.exercise_name}
                </p>
                <p className="text-xs text-gray-500">
                  {repRange}
                  {lastWeight != null && ` @ ${lastWeight} lbs`}
                  {re.notes && ` — ${re.notes}`}
                </p>
              </div>
              {re.last_performance && (
                <div className="flex gap-0.5">
                  {re.last_performance.sets.map((s, i) => (
                    <span
                      key={i}
                      className={`w-2 h-2 rounded-full ${repDot(s.rep_completion)}`}
                    />
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ── Recent Sessions ──

function RecentSessionsCard({ sessions }: { sessions: WkSession[] }) {
  const [expandedId, setExpandedId] = useState<number | null>(null)

  if (sessions.length === 0) {
    return (
      <section className="bg-white border border-gray-200 rounded-2xl p-5">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
          Recent Sessions
        </p>
        <p className="text-sm text-gray-500 mt-2">No workout sessions logged yet.</p>
      </section>
    )
  }

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
        Recent Sessions
      </p>
      <div className="mt-3 space-y-2">
        {sessions.map((ws) => {
          const exerciseMap = groupSetsByExercise(ws.sets)
          const totalVolume = ws.sets.reduce(
            (sum, s) => sum + (s.reps || 0) * (s.weight || 0),
            0,
          )
          const isExpanded = expandedId === ws.id

          return (
            <div key={ws.id} className="rounded-xl border border-gray-200">
              <button
                onClick={() => setExpandedId(isExpanded ? null : ws.id)}
                className="w-full text-left px-3 py-2 flex items-center gap-3"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-800">
                    {new Date(ws.date + 'T12:00:00').toLocaleDateString('en-US', {
                      weekday: 'short',
                      month: 'short',
                      day: 'numeric',
                    })}
                  </p>
                  <p className="text-xs text-gray-500">
                    {exerciseMap.size} exercise{exerciseMap.size !== 1 ? 's' : ''}
                    {totalVolume > 0 && ` · ${Math.round(totalVolume).toLocaleString()} lbs vol`}
                  </p>
                </div>
                <div className="flex gap-0.5">
                  {ws.sets
                    .filter((s) => s.rep_completion)
                    .slice(0, 8)
                    .map((s, i) => (
                      <span
                        key={i}
                        className={`w-2 h-2 rounded-full ${repDot(s.rep_completion)}`}
                      />
                    ))}
                </div>
                <span className="text-gray-400 text-xs">{isExpanded ? '−' : '+'}</span>
              </button>
              {isExpanded && (
                <div className="px-3 pb-3 space-y-2">
                  {Array.from(exerciseMap.entries()).map(([name, sets]) => (
                    <div key={name}>
                      <p className="text-xs font-medium text-gray-700">{name}</p>
                      <div className="flex flex-wrap gap-1 mt-0.5">
                        {sets.map((s) => (
                          <span
                            key={s.id}
                            className="text-[11px] text-gray-500 bg-gray-50 rounded px-1.5 py-0.5"
                          >
                            {s.reps != null && s.weight != null
                              ? `${s.weight}×${s.reps}`
                              : s.duration_secs != null
                                ? `${s.duration_secs}s`
                                : '—'}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                  {ws.notes && (
                    <p className="text-xs text-gray-400 italic">{ws.notes}</p>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ── Exercise Progress Chart ──

function ExerciseProgressCard({
  exercises,
}: {
  exercises: WkExercise[]
}) {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [history, setHistory] = useState<WkExerciseHistory | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (selectedId === null) {
      setHistory(null)
      return
    }
    setLoading(true)
    getExerciseHistory(selectedId, 20)
      .then(setHistory)
      .catch(() => setHistory(null))
      .finally(() => setLoading(false))
  }, [selectedId])

  const chartData = useMemo(() => {
    if (!history || history.sessions.length === 0) return null
    const sessions = [...history.sessions].reverse() // oldest first
    const maxWeight = Math.max(...sessions.map((s) => s.max_weight))
    const maxVolume = Math.max(...sessions.map((s) => s.total_volume))
    return { sessions, maxWeight, maxVolume }
  }, [history])

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
        Exercise Progress
      </p>
      <select
        className="mt-2 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 bg-white"
        value={selectedId ?? ''}
        onChange={(e) =>
          setSelectedId(e.target.value ? Number(e.target.value) : null)
        }
      >
        <option value="">Select an exercise...</option>
        {exercises.map((ex) => (
          <option key={ex.id} value={ex.id}>
            {ex.name}
          </option>
        ))}
      </select>

      {loading && (
        <p className="text-sm text-gray-400 mt-3">Loading...</p>
      )}

      {chartData && (
        <div className="mt-3">
          {/* Max Weight Line Chart */}
          <p className="text-xs text-gray-500 mb-1">Max Weight (lbs)</p>
          <svg viewBox="0 0 340 140" className="w-full h-36">
            <WeightLineChart
              data={chartData.sessions.map((s) => ({
                label: s.date.slice(5),
                value: s.max_weight,
              }))}
              maxValue={chartData.maxWeight}
              width={340}
              height={140}
              color="#0f766e"
            />
          </svg>

          {/* Volume Bar Chart */}
          <p className="text-xs text-gray-500 mt-3 mb-1">Volume (lbs)</p>
          <svg viewBox="0 0 340 120" className="w-full h-28">
            <VolumeBarChart
              data={chartData.sessions.map((s) => ({
                label: s.date.slice(5),
                value: s.total_volume,
              }))}
              maxValue={chartData.maxVolume}
              width={340}
              height={120}
            />
          </svg>
        </div>
      )}

      {selectedId && !loading && !chartData && (
        <p className="text-sm text-gray-400 mt-3">No history for this exercise.</p>
      )}
    </section>
  )
}

// ── SVG Chart Components ──

function WeightLineChart({
  data,
  maxValue,
  width,
  height,
  color,
}: {
  data: { label: string; value: number }[]
  maxValue: number
  width: number
  height: number
  color: string
}) {
  const margin = { top: 12, right: 16, bottom: 24, left: 32 }
  const plotW = width - margin.left - margin.right
  const plotH = height - margin.top - margin.bottom
  const padding = maxValue * 0.15
  const scaledMax = maxValue + padding
  const scaledMin = Math.max(0, Math.min(...data.map((d) => d.value)) - padding)
  const range = scaledMax - scaledMin || 1
  const step = data.length > 1 ? plotW / (data.length - 1) : plotW

  const toX = (i: number) => margin.left + i * step
  const toY = (v: number) => margin.top + ((scaledMax - v) / range) * plotH

  const points = data.map((d, i) => `${toX(i)},${toY(d.value)}`).join(' ')

  // Y-axis guides
  const ySteps = 3
  const guides = Array.from({ length: ySteps + 1 }, (_, i) => {
    const val = scaledMin + (range / ySteps) * i
    return { val, y: toY(val) }
  })

  return (
    <g>
      {guides.map((g, i) => (
        <g key={i}>
          <line
            x1={margin.left}
            x2={width - margin.right}
            y1={g.y}
            y2={g.y}
            stroke="#e5e7eb"
            strokeDasharray="3,3"
          />
          <text x={margin.left - 4} y={g.y + 3} textAnchor="end" className="text-[9px] fill-gray-400">
            {Math.round(g.val)}
          </text>
        </g>
      ))}
      <polyline points={points} fill="none" stroke={color} strokeWidth={2.5} strokeLinejoin="round" />
      {data.map((d, i) => (
        <g key={i}>
          <circle cx={toX(i)} cy={toY(d.value)} r={3} fill={color} />
          {data.length <= 12 && (
            <text
              x={toX(i)}
              y={height - 4}
              textAnchor="middle"
              className="text-[8px] fill-gray-400"
            >
              {d.label}
            </text>
          )}
        </g>
      ))}
    </g>
  )
}

function VolumeBarChart({
  data,
  maxValue,
  width,
  height,
}: {
  data: { label: string; value: number }[]
  maxValue: number
  width: number
  height: number
}) {
  const margin = { top: 8, right: 16, bottom: 20, left: 32 }
  const plotW = width - margin.left - margin.right
  const plotH = height - margin.top - margin.bottom
  const barW = Math.max(4, plotW / data.length - 2)
  const step = plotW / data.length
  const max = maxValue || 1

  return (
    <g>
      <line
        x1={margin.left}
        x2={width - margin.right}
        y1={margin.top + plotH}
        y2={margin.top + plotH}
        stroke="#e5e7eb"
      />
      {data.map((d, i) => {
        const barH = (d.value / max) * plotH
        const x = margin.left + i * step + (step - barW) / 2
        const y = margin.top + plotH - barH
        return (
          <g key={i}>
            <rect x={x} y={y} width={barW} height={barH} rx={2} fill="#3b82f6" opacity={0.7} />
            {data.length <= 12 && (
              <text
                x={margin.left + i * step + step / 2}
                y={height - 4}
                textAnchor="middle"
                className="text-[8px] fill-gray-400"
              >
                {d.label}
              </text>
            )}
          </g>
        )
      })}
      <text x={margin.left - 4} y={margin.top + 6} textAnchor="end" className="text-[9px] fill-gray-400">
        {Math.round(max).toLocaleString()}
      </text>
      <text x={margin.left - 4} y={margin.top + plotH + 3} textAnchor="end" className="text-[9px] fill-gray-400">
        0
      </text>
    </g>
  )
}

// ── Main Page ──

export default function WorkoutPage() {
  const [readiness, setReadiness] = useState<WkTissueReadiness[]>([])
  const [routine, setRoutine] = useState<WkRoutineExercise[]>([])
  const [sessions, setSessions] = useState<WkSession[]>([])
  const [exercises, setExercises] = useState<WkExercise[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const [r, rt, s, ex] = await Promise.all([
          getTissueReadiness(),
          getRoutine(),
          getWorkoutSessions(undefined, undefined, 10),
          getExercises(),
        ])
        setReadiness(r)
        setRoutine(rt)
        setSessions(s)
        setExercises(ex)
      } catch {
        // reset
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-sm text-gray-400">Loading workout data...</p>
      </div>
    )
  }

  return (
    <div className="space-y-4 pb-4 overflow-y-auto h-full">
      <TissueReadinessCard readiness={readiness} />
      <SuggestedWorkoutCard routine={routine} readiness={readiness} />
      <RecentSessionsCard sessions={sessions} />
      <ExerciseProgressCard exercises={exercises} />
    </div>
  )
}

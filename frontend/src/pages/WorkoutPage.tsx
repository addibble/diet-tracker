import React, { useEffect, useMemo, useState } from 'react'
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

// ── Tissue Status Table ──

type SortKey = 'name' | 'status' | 'last_worked' | 'recovery' | 'volume_7d'

function recoveryBarClass(pct: number, status: string | undefined): string {
  if (status === 'injured' || status === 'tender') return 'bg-purple-400'
  if (pct >= 100) return 'bg-emerald-400'
  if (pct >= 75) return 'bg-yellow-400'
  return 'bg-red-400'
}

function statusBadge(condition: WkTissueReadiness['condition']): React.ReactNode {
  if (!condition || condition.status === 'healthy') return null
  const cfg: Record<string, string> = {
    tender:    'bg-amber-100 text-amber-700 border-amber-200',
    injured:   'bg-red-100 text-red-700 border-red-200',
    rehabbing: 'bg-purple-100 text-purple-700 border-purple-200',
  }
  return (
    <span className={`inline-block text-[10px] font-medium px-1.5 py-px rounded border leading-tight ${cfg[condition.status] ?? 'bg-gray-100 text-gray-600 border-gray-200'}`}>
      {condition.status}
    </span>
  )
}

function TissueStatusTable({ readiness }: { readiness: WkTissueReadiness[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('recovery')
  const [sortAsc, setSortAsc] = useState(true)

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc((a) => !a)
    else { setSortKey(key); setSortAsc(key === 'name' || key === 'status') }
  }

  const sorted = useMemo(() => {
    return [...readiness].sort((a, b) => {
      let cmp = 0
      if (sortKey === 'name')       cmp = a.tissue.display_name.localeCompare(b.tissue.display_name)
      else if (sortKey === 'status')  cmp = (a.condition?.status ?? 'healthy').localeCompare(b.condition?.status ?? 'healthy')
      else if (sortKey === 'last_worked') cmp = (a.hours_since ?? Infinity) - (b.hours_since ?? Infinity)
      else if (sortKey === 'recovery')    cmp = a.recovery_pct - b.recovery_pct
      else if (sortKey === 'volume_7d')   cmp = a.volume_7d - b.volume_7d
      return sortAsc ? cmp : -cmp
    })
  }, [readiness, sortKey, sortAsc])

  const SortBtn = ({ k, label }: { k: SortKey; label: string }) => (
    <button
      onClick={() => toggleSort(k)}
      className={`flex items-center gap-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] transition-colors select-none ${sortKey === k ? 'text-gray-700' : 'text-gray-400 hover:text-gray-600'}`}
    >
      {label}
      {sortKey === k && <span className="opacity-60">{sortAsc ? '↑' : '↓'}</span>}
    </button>
  )

  if (readiness.length === 0) {
    return (
      <section className="bg-white border border-gray-200 rounded-2xl p-5">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">Tissue Status</p>
        <p className="text-sm text-gray-500 mt-2">No tissue data yet.</p>
      </section>
    )
  }

  return (
    <section className="bg-white border border-gray-200 rounded-2xl overflow-hidden">
      {/* Header bar */}
      <div className="px-5 pt-5 pb-3 border-b border-gray-100">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">Tissue Status</p>
        {/* Column headers / sort controls */}
        <div className="mt-2 grid items-center" style={{ gridTemplateColumns: '1fr 70px 64px 80px 72px' }}>
          <SortBtn k="name" label="Tissue" />
          <SortBtn k="status" label="Status" />
          <SortBtn k="last_worked" label="Last" />
          <SortBtn k="recovery" label="Recovery" />
          <div className="text-right"><SortBtn k="volume_7d" label="7d Vol" /></div>
        </div>
      </div>

      <div className="divide-y divide-gray-50">
        {sorted.map((r, i) => (
          <TissueRow key={r.tissue.id} r={r} striped={i % 2 === 1} />
        ))}
      </div>
    </section>
  )
}

function TissueRow({
  r,
  striped,
}: {
  r: WkTissueReadiness
  striped: boolean
}) {
  const barClass = recoveryBarClass(r.recovery_pct, r.condition?.status)

  return (
    <div
      className={`grid items-center px-5 py-2 text-xs transition-colors hover:bg-gray-50/80 ${striped ? 'bg-gray-50/40' : ''}`}
      style={{ gridTemplateColumns: '1fr 70px 64px 80px 72px' }}
    >
      {/* Name */}
      <span className="text-gray-700 font-medium truncate pr-2">
        {r.tissue.display_name}
      </span>

      {/* Status */}
      <span>{statusBadge(r.condition) ?? <span className="text-[10px] text-gray-300">—</span>}</span>

      {/* Last worked */}
      <span className="text-gray-400 tabular-nums">{hoursLabel(r.hours_since)}</span>

      {/* Recovery bar + % */}
      <div className="flex items-center gap-1.5">
        <div className="relative h-1.5 w-10 rounded-full bg-gray-100 overflow-hidden flex-shrink-0">
          <div
            className={`absolute inset-y-0 left-0 rounded-full ${barClass} transition-all duration-500`}
            style={{ width: `${Math.min(100, r.recovery_pct)}%` }}
          />
        </div>
        <span className={`tabular-nums font-medium ${barClass.replace('bg-', 'text-').replace('-400', '-600')}`}>
          {Math.round(r.recovery_pct)}%
        </span>
      </div>

      {/* 7d volume */}
      <div className="text-right tabular-nums">
        {r.volume_7d > 0 ? (
          <span className="text-gray-600 font-medium">
            {r.volume_7d >= 1000
              ? `${(r.volume_7d / 1000).toFixed(1)}k`
              : r.volume_7d.toLocaleString()}
          </span>
        ) : (
          <span className="text-gray-300">—</span>
        )}
      </div>
    </div>
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
      <TissueStatusTable readiness={readiness} />
      <SuggestedWorkoutCard routine={routine} readiness={readiness} />
      <RecentSessionsCard sessions={sessions} />
      <ExerciseProgressCard exercises={exercises} />
    </div>
  )
}

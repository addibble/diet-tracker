import { useEffect, useMemo, useState } from 'react'
import {
  getExerciseHistory,
  type TrainingModelExerciseInsight,
  type WkExercise,
  type WkExerciseHistory,
} from '../api'

const riskColor = (risk: number) =>
  risk >= 75 ? 'text-red-600' : risk >= 55 ? 'text-amber-600' : risk >= 30 ? 'text-yellow-600' : 'text-emerald-600'

const recommendationBadge = (recommendation: string) => {
  if (recommendation === 'avoid') return 'bg-red-100 text-red-700 border-red-200'
  if (recommendation === 'caution') return 'bg-amber-100 text-amber-700 border-amber-200'
  return 'bg-emerald-100 text-emerald-700 border-emerald-200'
}

const fmtVol = (value: number) =>
  value >= 10000 ? `${(value / 1000).toFixed(0)}k` : value >= 1000 ? `${(value / 1000).toFixed(1)}k` : Math.round(value).toString()

export function ExerciseStatusCard({ exercises }: { exercises: TrainingModelExerciseInsight[] }) {
  const [filter, setFilter] = useState<'all' | 'good' | 'caution' | 'avoid'>('all')
  const counts = {
    good: exercises.filter(exercise => exercise.recommendation === 'good').length,
    caution: exercises.filter(exercise => exercise.recommendation === 'caution').length,
    avoid: exercises.filter(exercise => exercise.recommendation === 'avoid').length,
  }
  const filtered = filter === 'all' ? exercises : exercises.filter(exercise => exercise.recommendation === filter)

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5">
      <div className="mb-3">
        <h3 className="text-sm font-semibold text-gray-900">Exercise Status</h3>
        <p className="mt-0.5 text-xs text-gray-500">Ranked by exercise recommendation and recent weighted tissue risk.</p>
      </div>

      <div className="mb-3 flex flex-wrap gap-1.5">
        {([
          ['all', 'All'],
          ['good', `Good (${counts.good})`],
          ['caution', `Caution (${counts.caution})`],
          ['avoid', `Avoid (${counts.avoid})`],
        ] as const).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => setFilter(value)}
            className={`rounded-lg border px-2.5 py-1 text-xs transition-all ${
              filter === value
                ? value === 'good'
                  ? 'border-emerald-300 bg-emerald-100 font-semibold text-emerald-700'
                  : value === 'caution'
                    ? 'border-amber-300 bg-amber-100 font-semibold text-amber-700'
                    : value === 'avoid'
                      ? 'border-red-300 bg-red-100 font-semibold text-red-700'
                      : 'border-gray-900 bg-gray-900 font-semibold text-white'
                : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="max-h-72 space-y-1.5 overflow-y-auto">
        {filtered.map(exercise => (
          <div key={exercise.id} className="flex items-center gap-3 rounded-lg border border-gray-100 bg-gray-50/50 p-2.5">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-sm font-medium text-gray-900">{exercise.name}</span>
                <span className={`rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${recommendationBadge(exercise.recommendation)}`}>
                  {exercise.recommendation}
                </span>
              </div>
              <div className="mt-0.5 flex flex-wrap gap-3 text-[11px] text-gray-500">
                {exercise.equipment && <span>{exercise.equipment}</span>}
                {exercise.current_e1rm && <span>e1RM: {exercise.current_e1rm} lb</span>}
                <span>suit: {Math.round(exercise.suitability_score)}%</span>
              </div>
              {exercise.recommendation_details.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {exercise.recommendation_details.slice(0, 2).map(detail => (
                    <span key={detail} className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">
                      {detail}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <div className="shrink-0 text-right">
              <span className={`text-base font-bold tabular-nums ${riskColor(exercise.weighted_risk_7d)}`}>{Math.round(exercise.weighted_risk_7d)}</span>
              <span className="ml-0.5 text-[10px] text-gray-400">%</span>
            </div>
          </div>
        ))}
        {filtered.length === 0 && <p className="py-2 text-sm text-gray-400">No exercises in this category.</p>}
      </div>
    </div>
  )
}

export function ExerciseProgressCard({ exercises }: { exercises: WkExercise[] }) {
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [history, setHistory] = useState<WkExerciseHistory | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!selectedId) return
    let cancelled = false
    getExerciseHistory(selectedId, 200)
      .then(data => {
        if (!cancelled) setHistory(data)
      })
      .catch(() => {
        if (!cancelled) setHistory(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  const monthlyData = useMemo(() => {
    if (!history) return []
    const byMonth: Record<string, { pr: number; volume: number; e1rm: number; sessions: number }> = {}
    for (const session of history.sessions) {
      const month = session.date.slice(0, 7)
      if (!byMonth[month]) byMonth[month] = { pr: 0, volume: 0, e1rm: 0, sessions: 0 }
      byMonth[month].pr = Math.max(byMonth[month].pr, session.max_weight)
      byMonth[month].volume += session.total_volume
      byMonth[month].sessions += 1
      for (const set of session.sets) {
        if (set.weight && set.reps && set.reps > 0) {
          const reps = Math.min(set.reps, 12)
          const epley = set.weight * (1 + 0.0333 * reps)
          byMonth[month].e1rm = Math.max(byMonth[month].e1rm, epley)
        }
      }
    }
    return Object.entries(byMonth)
      .sort((left, right) => left[0].localeCompare(right[0]))
      .map(([month, values]) => ({
        month,
        label: new Date(`${month}-15`).toLocaleDateString(undefined, { month: 'short', year: '2-digit' }),
        ...values,
      }))
  }, [history])

  const allTimePR = monthlyData.length > 0 ? Math.max(...monthlyData.map(month => month.pr)) : 0
  const currentE1rm = monthlyData.length > 0 ? Math.round(monthlyData[monthlyData.length - 1].e1rm) : 0

  const svgWidth = 340
  const e1rmHeight = 140
  const volumeHeight = 100
  const marginLeft = 40
  const marginRight = 12
  const marginTop = 10
  const marginBottom = 28
  const plotWidth = svgWidth - marginLeft - marginRight

  const maxE1rm = Math.max(...monthlyData.map(month => month.e1rm), 1) * 1.1
  const maxVolume = Math.max(...monthlyData.map(month => month.volume), 1)
  const count = monthlyData.length
  const step = count > 1 ? plotWidth / (count - 1) : plotWidth
  const barStep = plotWidth / Math.max(count, 1)
  const barWidth = Math.max(3, barStep * 0.6)
  const showLabels = count <= 18

  const toX = (index: number) => marginLeft + (count > 1 ? index * step : plotWidth / 2)
  const toY = (value: number) => marginTop + ((maxE1rm - value) / maxE1rm) * (e1rmHeight - marginTop - marginBottom)

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5">
      <div className="mb-2">
        <h3 className="text-sm font-semibold text-gray-900">Exercise Progress</h3>
        <p className="mt-0.5 text-xs text-gray-500">Monthly strength and volume trend for any movement.</p>
      </div>

      <select
        className="mb-3 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700"
        value={selectedId ?? ''}
        onChange={(event) => {
          const nextId = event.target.value ? Number(event.target.value) : null
          setSelectedId(nextId)
          setHistory(null)
          setLoading(Boolean(nextId))
        }}
      >
        <option value="">Select an exercise...</option>
        {exercises.map(exercise => (
          <option key={exercise.id} value={exercise.id}>
            {exercise.name}
          </option>
        ))}
      </select>

      {loading && <p className="text-sm text-gray-400">Loading...</p>}

      {!loading && selectedId && monthlyData.length === 0 && (
        <p className="text-sm text-gray-400">No history for this exercise.</p>
      )}

      {monthlyData.length > 0 && (
        <>
          <div className="mb-3 grid grid-cols-3 gap-2">
            <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400">e1RM (cur)</p>
              <p className="mt-0.5 text-base font-bold text-gray-900">{currentE1rm} <span className="text-xs font-normal text-gray-400">lb</span></p>
            </div>
            <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400">PR</p>
              <p className="mt-0.5 text-base font-bold text-gray-900">{allTimePR} <span className="text-xs font-normal text-gray-400">lb</span></p>
            </div>
            <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-400">Sessions</p>
              <p className="mt-0.5 text-base font-bold text-gray-900">{history?.sessions.length ?? 0}</p>
            </div>
          </div>

          <p className="mb-1 text-[10px] uppercase tracking-[0.14em] text-gray-400">Estimated 1RM (monthly)</p>
          <svg viewBox={`0 0 ${svgWidth} ${e1rmHeight}`} className="w-full" style={{ height: `${e1rmHeight}px` }}>
            {[0, 0.5, 1].map((fraction, index) => {
              const value = fraction * maxE1rm
              const y = toY(value)
              return (
                <g key={index}>
                  <line x1={marginLeft} x2={svgWidth - marginRight} y1={y} y2={y} stroke="#e5e7eb" strokeDasharray="3 3" />
                  <text x={marginLeft - 3} y={y + 3} textAnchor="end" fontSize="9" fill="#9ca3af">{Math.round(value)}</text>
                </g>
              )
            })}
            <polyline
              points={monthlyData.map((month, index) => `${toX(index)},${toY(month.e1rm)}`).join(' ')}
              fill="none"
              stroke="#f97316"
              strokeWidth={2}
              strokeLinejoin="round"
            />
            {monthlyData.map((month, index) => (
              <g key={month.month}>
                <circle cx={toX(index)} cy={toY(month.e1rm)} r={3} fill="#f97316" />
                {showLabels && (
                  <text x={toX(index)} y={e1rmHeight - 6} textAnchor="middle" fontSize="8" fill="#6b7280">
                    {month.label}
                  </text>
                )}
              </g>
            ))}
          </svg>

          <p className="mb-1 mt-3 text-[10px] uppercase tracking-[0.14em] text-gray-400">Volume (monthly, lbs)</p>
          <svg viewBox={`0 0 ${svgWidth} ${volumeHeight}`} className="w-full" style={{ height: `${volumeHeight}px` }}>
            <line x1={marginLeft} x2={svgWidth - marginRight} y1={volumeHeight - marginBottom} y2={volumeHeight - marginBottom} stroke="#e5e7eb" />
            <text x={marginLeft - 3} y={marginTop + 4} textAnchor="end" fontSize="9" fill="#9ca3af">{fmtVol(maxVolume)}</text>
            {monthlyData.map((month, index) => {
              const barHeight = Math.max(1, (month.volume / maxVolume) * (volumeHeight - marginTop - marginBottom))
              const x = marginLeft + index * barStep + (barStep - barWidth) / 2
              return (
                <g key={month.month}>
                  <rect x={x} y={volumeHeight - marginBottom - barHeight} width={barWidth} height={barHeight} rx={2} fill="#3b82f6" opacity={0.75} />
                  {showLabels && (
                    <text x={x + barWidth / 2} y={volumeHeight - 6} textAnchor="middle" fontSize="8" fill="#6b7280">
                      {month.label}
                    </text>
                  )}
                </g>
              )
            })}
          </svg>

          {!showLabels && (
            <p className="mt-1 text-[10px] text-gray-400">
              {monthlyData[0]?.label} - {monthlyData[monthlyData.length - 1]?.label} ({count} months)
            </p>
          )}
        </>
      )}
    </div>
  )
}

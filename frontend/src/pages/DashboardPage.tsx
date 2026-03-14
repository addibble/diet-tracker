import { useEffect, useMemo, useState } from 'react'
import ScrollablePage from '../components/ScrollablePage'
import {
  getDailySummary,
  getDashboardTrends,
  getWorkouts,
  getWorkoutSessions,
  getVolumeByRegion,
  MACRO_KEYS,
  MACRO_LABELS,
  MACRO_UNITS,
  type DailySummary,
  type DashboardTrends,
  type Workout,
  type WkSession,
  type VolumeByRegion,
} from '../api'

function today() {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function localDate(date: string) {
  return new Date(`${date}T12:00:00`)
}

function shortDateLabel(date: string) {
  return localDate(date).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  })
}

function weekdayLabel(date: string) {
  return localDate(date).toLocaleDateString(undefined, { weekday: 'short' })
}

function formatTimestamp(timestamp: string) {
  return new Date(timestamp).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatSigned(value: number) {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}`
}

function WeightTrendCard({ trends }: { trends: DashboardTrends }) {
  const chart = useMemo(() => {
    const width = 340
    const height = 190
    const left = 22
    const right = 18
    const top = 16
    const bottom = 34
    const weightDays = trends.weight_days
    const step = (width - left - right) / Math.max(weightDays.length - 1, 1)
    const actualWeights = weightDays.map((day) => day.weight_lb)
    const regressionWeights = trends.weight_regression?.line.map((point) => point.weight_lb) ?? []
    const allWeights = [...actualWeights, ...regressionWeights]

    if (allWeights.length === 0) {
      return {
        width,
        height,
        weightDays: [] as typeof weightDays,
        actualPoints: [] as { x: number; y: number; value: number; date: string }[],
        regressionPoints: [] as { x: number; y: number }[],
        guides: [] as { y: number; label: string }[],
      }
    }

    const minWeight = Math.min(...allWeights)
    const maxWeight = Math.max(...allWeights)
    const padding = maxWeight === minWeight ? 1 : (maxWeight - minWeight) * 0.2
    const scaledMin = minWeight - padding
    const scaledMax = maxWeight + padding
    const yRange = Math.max(scaledMax - scaledMin, 1)
    const plotHeight = height - top - bottom
    const toY = (value: number) => top + ((scaledMax - value) / yRange) * plotHeight

    const actualPoints = weightDays.map((day, index) => ({
      x: left + step * index,
      y: toY(day.weight_lb),
      value: day.weight_lb,
      date: day.date,
    }))
    const regressionPoints = (trends.weight_regression?.line ?? []).map((point, index) => ({
      x: left + step * index,
      y: toY(point.weight_lb),
    }))
    const guideValues = [scaledMax, (scaledMax + scaledMin) / 2, scaledMin]
    const guides = guideValues.map((value) => ({
      y: toY(value),
      label: value.toFixed(1),
    }))

    return { width, height, weightDays, actualPoints, regressionPoints, guides }
  }, [trends])

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <div className="flex flex-wrap items-start justify-between gap-4 mb-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
            Weight Trend
          </p>
          <h2 className="text-xl font-semibold text-gray-900 mt-1">
            {trends.latest_weight_lb !== null ? `${trends.latest_weight_lb.toFixed(1)} lb` : 'No weigh-ins yet'}
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            {trends.latest_weight_logged_at
              ? `Last logged ${formatTimestamp(trends.latest_weight_logged_at)}`
              : 'Log your weight in chat to start the regression line.'}
          </p>
        </div>
        <div className="text-left sm:text-right">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
            Trend Slope
          </p>
          <p className="text-xl font-semibold text-gray-900 mt-1">
            {trends.weight_regression
              ? `${formatSigned(trends.weight_regression.slope_lb_per_week)} lb/week`
              : 'No regression yet'}
          </p>
          {trends.weight_regression && (
            <p className="text-sm text-gray-500 mt-1">
              {trends.weight_regression.points_used} weigh-in
              {trends.weight_regression.points_used === 1 ? '' : 's'} used
            </p>
          )}
        </div>
      </div>

      {chart.actualPoints.length > 0 ? (
        <div>
          <svg viewBox={`0 0 ${chart.width} ${chart.height}`} className="w-full h-52">
            {chart.guides.map((guide) => (
              <g key={guide.label}>
                <line
                  x1="18"
                  x2={chart.width - 14}
                  y1={guide.y}
                  y2={guide.y}
                  stroke="#e5e7eb"
                  strokeWidth="1"
                  strokeDasharray="4 4"
                />
                <text x="0" y={guide.y + 4} fontSize="10" fill="#9ca3af">
                  {guide.label}
                </text>
              </g>
            ))}
            {chart.regressionPoints.length > 1 && (
              <polyline
                fill="none"
                stroke="#94a3b8"
                strokeWidth="2"
                strokeDasharray="6 6"
                points={chart.regressionPoints.map((point) => `${point.x},${point.y}`).join(' ')}
              />
            )}
            {chart.actualPoints.length > 1 && (
              <polyline
                fill="none"
                stroke="#0f766e"
                strokeWidth="3"
                strokeLinejoin="round"
                strokeLinecap="round"
                points={chart.actualPoints.map((point) => `${point.x},${point.y}`).join(' ')}
              />
            )}
            {chart.actualPoints.map((point) => (
              <g key={point.date}>
                <circle cx={point.x} cy={point.y} r="4.5" fill="#0f766e" />
                <text
                  x={point.x}
                  y={point.y - 10}
                  textAnchor="middle"
                  fontSize="10"
                  fill="#0f766e"
                >
                  {point.value.toFixed(1)}
                </text>
              </g>
            ))}
            {(chart.weightDays ?? []).map((day, index) => {
              const x = 22 + ((chart.width - 22 - 18) / Math.max((chart.weightDays ?? []).length - 1, 1)) * index
              return (
                <text
                  key={day.date}
                  x={x}
                  y={chart.height - 10}
                  textAnchor="middle"
                  fontSize="10"
                  fill="#6b7280"
                >
                  {weekdayLabel(day.date)}
                </text>
              )
            })}
          </svg>
          <div className="grid grid-cols-2 sm:grid-cols-7 gap-2 mt-2">
            {trends.days.map((day) => (
              <div key={day.date} className="rounded-xl bg-gray-50 px-3 py-2 border border-gray-100">
                <p className="text-xs text-gray-500">{shortDateLabel(day.date)}</p>
                <p className="text-sm font-medium text-gray-900 mt-1">
                  {day.weight_lb !== null ? `${day.weight_lb.toFixed(1)} lb` : 'No entry'}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-gray-300 bg-gray-50 px-4 py-8 text-center text-sm text-gray-500">
          No weights logged yet.
        </div>
      )}
    </section>
  )
}

function DailyTargetsBreakdownCard({ trends }: { trends: DashboardTrends }) {
  const chartMaxRatio = useMemo(() => {
    const ratios = trends.days.map((day) => {
      const target = day.active_macro_target?.calories ?? 0
      if (target > 0) return day.total_calories / target
      return day.total_calories > 0 ? 1 : 0
    })
    return Math.max(1.25, ...ratios)
  }, [trends])

  const targetLineBottom = `${Math.min((1 / chartMaxRatio) * 100, 100)}%`

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <div className="mb-4">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
          Daily Targets
        </p>
        <h2 className="text-xl font-semibold text-gray-900 mt-1">Last 7 days</h2>
      </div>

      <div className="flex flex-wrap gap-3 text-xs text-gray-500 mb-4">
        <span className="inline-flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-amber-400" /> Fat
        </span>
        <span className="inline-flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-blue-500" /> Carbs
        </span>
        <span className="inline-flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-emerald-500" /> Protein
        </span>
      </div>

      <div className="grid grid-cols-7 gap-3 items-end h-64">
        {trends.days.map((day) => {
          const targetCalories = day.active_macro_target?.calories ?? 0
          const ratioToTarget = targetCalories > 0
            ? day.total_calories / targetCalories
            : (day.total_calories > 0 ? 1 : 0)
          const barHeightPercent = day.total_calories > 0
            ? Math.max((ratioToTarget / chartMaxRatio) * 100, 6)
            : 4

          const fat = day.macro_calorie_percentages.fat
          const carbs = day.macro_calorie_percentages.carbs
          const protein = day.macro_calorie_percentages.protein
          const remainder = Math.max(0, 100 - fat - carbs - protein)

          return (
            <div key={day.date} className="flex flex-col items-center h-full gap-2">
              <span className="text-[10px] text-gray-500 tabular-nums h-8 leading-4 text-center">
                {Math.round(day.total_calories)} kcal
              </span>
              <div className="relative w-full flex-1 min-h-0 flex items-end">
                <div
                  className="w-full rounded-t-xl overflow-hidden border border-gray-200 bg-gray-50"
                  style={{ height: `${Math.min(barHeightPercent, 100)}%` }}
                  title={`${shortDateLabel(day.date)}: ${Math.round(day.total_calories)} kcal`}
                >
                  <div className="h-full flex flex-col-reverse">
                    <div className="bg-amber-400" style={{ height: `${fat}%` }} />
                    <div className="bg-blue-500" style={{ height: `${carbs}%` }} />
                    <div className="bg-emerald-500" style={{ height: `${protein}%` }} />
                    {remainder > 0 && (
                      <div className="bg-gray-200" style={{ height: `${remainder}%` }} />
                    )}
                  </div>
                </div>
                {targetCalories > 0 && (
                  <div
                    className="absolute left-0 right-0 border-t-2 border-amber-500 border-dashed"
                    style={{ bottom: targetLineBottom }}
                    title={`Target: ${Math.round(targetCalories)} kcal`}
                  />
                )}
              </div>
              <p className="text-[10px] text-amber-600 tabular-nums h-4 leading-4">
                {targetCalories > 0 ? `T ${Math.round(targetCalories)}` : 'T —'}
              </p>
              <p className="text-[10px] text-gray-500 text-center h-8 leading-4 overflow-hidden">
                F {fat.toFixed(0)} · C {carbs.toFixed(0)} · P {protein.toFixed(0)}
              </p>
              <span className="text-xs text-gray-500 h-4 leading-4">{weekdayLabel(day.date)}</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

type TargetNormalizedMetric = {
  targetKey: 'saturated_fat' | 'cholesterol' | 'sodium' | 'fiber'
  totalKey: 'total_saturated_fat' | 'total_cholesterol' | 'total_sodium' | 'total_fiber'
  label: string
  unit: string
  colorClass: string
}

const TARGET_NORMALIZED_METRICS: TargetNormalizedMetric[] = [
  {
    targetKey: 'saturated_fat',
    totalKey: 'total_saturated_fat',
    label: 'Saturated Fat',
    unit: 'g',
    colorClass: 'bg-amber-400',
  },
  {
    targetKey: 'cholesterol',
    totalKey: 'total_cholesterol',
    label: 'Cholesterol',
    unit: 'mg',
    colorClass: 'bg-fuchsia-500',
  },
  {
    targetKey: 'sodium',
    totalKey: 'total_sodium',
    label: 'Sodium',
    unit: 'mg',
    colorClass: 'bg-indigo-500',
  },
  {
    targetKey: 'fiber',
    totalKey: 'total_fiber',
    label: 'Fiber',
    unit: 'g',
    colorClass: 'bg-lime-600',
  },
]

function formatMacroTrendValue(value: number, unit: string): string {
  if (unit === 'mg') return `${Math.round(value)}`
  return `${value.toFixed(1)}`
}

function TargetNormalizedMacroTrendsCard({ trends }: { trends: DashboardTrends }) {
  const chartMaxRatio = useMemo(() => {
    let maxRatio = 1.25
    for (const day of trends.days) {
      for (const metric of TARGET_NORMALIZED_METRICS) {
        const target = day.active_macro_target?.[metric.targetKey] ?? 0
        if (target <= 0) continue
        const total = day[metric.totalKey]
        maxRatio = Math.max(maxRatio, total / target)
      }
    }
    return maxRatio
  }, [trends])

  const targetLineBottom = `${Math.min((1 / chartMaxRatio) * 100, 100)}%`

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <div className="mb-4">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
          Target-Normalized Trends
        </p>
        <h2 className="text-xl font-semibold text-gray-900 mt-1">
          Saturated Fat, Cholesterol, Sodium, Fiber
        </h2>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {TARGET_NORMALIZED_METRICS.map((metric) => (
          <div key={metric.targetKey} className="rounded-xl border border-gray-200 p-3">
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-medium text-gray-800">{metric.label}</p>
              <p className="text-xs text-gray-500">Height = % of target</p>
            </div>
            <div className="grid grid-cols-7 gap-2 items-end h-44">
              {trends.days.map((day) => {
                const total = day[metric.totalKey]
                const target = day.active_macro_target?.[metric.targetKey] ?? 0
                const ratio = target > 0 ? total / target : 0
                const barHeightPercent = target > 0 && total > 0
                  ? Math.max((ratio / chartMaxRatio) * 100, 6)
                  : 4
                const overTarget = target > 0 && total > target
                const barClass = overTarget ? 'bg-red-500' : metric.colorClass

                return (
                  <div key={day.date} className="flex flex-col items-center justify-end h-full gap-1.5">
                    <span className="text-[10px] text-gray-500 tabular-nums">
                      {formatMacroTrendValue(total, metric.unit)}
                    </span>
                    <div className="relative w-full h-full flex items-end">
                      <div
                        className={`w-full rounded-t-md ${barClass}`}
                        style={{ height: `${Math.min(barHeightPercent, 100)}%` }}
                        title={`${shortDateLabel(day.date)}: ${formatMacroTrendValue(total, metric.unit)} ${metric.unit}`}
                      />
                      {target > 0 && (
                        <div
                          className="absolute left-0 right-0 border-t border-gray-400 border-dashed"
                          style={{ bottom: targetLineBottom }}
                          title={`Target: ${formatMacroTrendValue(target, metric.unit)} ${metric.unit}`}
                        />
                      )}
                    </div>
                    <span className="text-[10px] text-gray-400 tabular-nums">
                      {target > 0 ? `T ${formatMacroTrendValue(target, metric.unit)}` : 'T —'}
                    </span>
                    <span className="text-[10px] text-gray-500">{weekdayLabel(day.date)}</span>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

// ── Helpers shared with workout ──

function repDot(completion: string | null): string {
  if (completion === 'full') return 'bg-green-500'
  if (completion === 'partial') return 'bg-yellow-500'
  if (completion === 'failed') return 'bg-red-500'
  return 'bg-gray-300'
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

// ── Recent Sessions ──

function RecentSessionsCard({ sessions }: { sessions: WkSession[] }) {
  const [expandedId, setExpandedId] = useState<number | null>(null)

  if (sessions.length === 0) return null

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400 mb-3">Recent Sessions</p>
      <div className="space-y-2">
        {sessions.map((ws) => {
          const exerciseMap = groupSetsByExercise(ws.sets)
          const totalVolume = ws.sets.reduce((sum, s) => sum + (s.reps || 0) * (s.weight || 0), 0)
          const isExpanded = expandedId === ws.id
          return (
            <div key={ws.id} className="rounded-xl border border-gray-200">
              <button
                onClick={() => setExpandedId(isExpanded ? null : ws.id)}
                className="w-full text-left px-3 py-2 flex items-center gap-3"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-800">
                    {new Date(ws.date + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
                  </p>
                  <p className="text-xs text-gray-500">
                    {exerciseMap.size} exercise{exerciseMap.size !== 1 ? 's' : ''}
                    {totalVolume > 0 && ` · ${Math.round(totalVolume).toLocaleString()} lbs vol`}
                  </p>
                </div>
                <div className="flex gap-0.5">
                  {ws.sets.filter(s => s.rep_completion).slice(0, 8).map((s, i) => (
                    <span key={i} className={`w-2 h-2 rounded-full ${repDot(s.rep_completion)}`} />
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
                          <span key={s.id} className="text-[11px] text-gray-500 bg-gray-50 rounded px-1.5 py-0.5">
                            {s.reps != null && s.weight != null ? `${s.weight}×${s.reps}` : s.duration_secs != null ? `${s.duration_secs}s` : '—'}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                  {ws.notes && <p className="text-xs text-gray-400 italic">{ws.notes}</p>}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ── Muscle Volume Chart ──

const REGION_COLORS: Record<string, string> = {
  chest: '#f97316', shoulders: '#fb923c', triceps: '#fcd34d',
  upper_back: '#1d4ed8', biceps: '#3b82f6', forearms: '#93c5fd',
  quads: '#15803d', hamstrings: '#22c55e', glutes: '#4ade80', calves: '#86efac', tibs: '#d1fae5',
  core: '#7c3aed', lower_back: '#a855f7', hips: '#d8b4fe',
  neck: '#6b7280', other: '#9ca3af',
}

function fmtVol(v: number) {
  return v >= 10000 ? `${(v / 1000).toFixed(0)}k` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : Math.round(v).toString()
}

function MuscleVolumeCard({ data }: { data: VolumeByRegion }) {
  const { dates, regions, daily, totals } = data

  const dayLabels = dates.map(d => new Date(d + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short' }))

  // Compute max total volume across days for chart scaling
  const dayTotals = dates.map(d => Object.values(daily[d] || {}).reduce((s, v) => s + v, 0))
  const maxDayVol = Math.max(...dayTotals, 1)
  const maxTotal = Math.max(...Object.values(totals), 1)

  // SVG stacked bar chart
  const svgW = 340, svgH = 140
  const ml = 36, mr = 8, mt = 8, mb = 24
  const plotW = svgW - ml - mr
  const plotH = svgH - mt - mb
  const barW = Math.max(4, plotW / dates.length - 4)
  const step = plotW / dates.length

  const yGuides = [0, 0.5, 1].map(f => ({
    y: mt + (1 - f) * plotH,
    label: fmtVol(f * maxDayVol),
  }))

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <div className="mb-3">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">Muscle Volume</p>
        <h2 className="text-xl font-semibold text-gray-900 mt-1">Last 7 days by region</h2>
      </div>

      {regions.length === 0 ? (
        <p className="text-sm text-gray-400">No workout volume logged in the last 7 days.</p>
      ) : (
        <>
          {/* Stacked bar chart */}
          <svg viewBox={`0 0 ${svgW} ${svgH}`} className="w-full h-36 mb-4">
            {yGuides.map((g, i) => (
              <g key={i}>
                <line x1={ml} x2={svgW - mr} y1={g.y} y2={g.y} stroke="#e5e7eb" strokeDasharray="3 3" />
                <text x={ml - 3} y={g.y + 3} textAnchor="end" fontSize="9" fill="#9ca3af">{g.label}</text>
              </g>
            ))}
            {dates.map((d, di) => {
              const dayVol = dayTotals[di]
              if (dayVol === 0) return null
              const barH = (dayVol / maxDayVol) * plotH
              const x = ml + di * step + (step - barW) / 2
              let yOff = 0
              return (
                <g key={d}>
                  {regions.map(r => {
                    const vol = (daily[d] || {})[r] || 0
                    if (vol === 0) return null
                    const segH = (vol / maxDayVol) * plotH
                    const y = mt + plotH - barH + yOff
                    yOff += segH
                    return <rect key={r} x={x} y={y} width={barW} height={segH} rx={1} fill={REGION_COLORS[r] || '#9ca3af'} />
                  })}
                  <text x={x + barW / 2} y={svgH - 4} textAnchor="middle" fontSize="9" fill="#6b7280">{dayLabels[di]}</text>
                </g>
              )
            })}
          </svg>

          {/* Legend */}
          <div className="flex flex-wrap gap-x-3 gap-y-1 mb-4">
            {regions.map(r => (
              <span key={r} className="flex items-center gap-1 text-[10px] text-gray-600">
                <span className="w-2 h-2 rounded-full inline-block" style={{ background: REGION_COLORS[r] || '#9ca3af' }} />
                {r.replace(/_/g, ' ')}
              </span>
            ))}
          </div>

          {/* 7-day totals horizontal bars */}
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-400 mb-2">7-Day Totals</p>
          <div className="space-y-1.5">
            {regions.map(r => {
              const vol = totals[r] || 0
              const pct = (vol / maxTotal) * 100
              return (
                <div key={r} className="flex items-center gap-2">
                  <span className="text-xs text-gray-600 w-20 truncate shrink-0">{r.replace(/_/g, ' ')}</span>
                  <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div className="h-full rounded-full" style={{ width: `${pct}%`, background: REGION_COLORS[r] || '#9ca3af' }} />
                  </div>
                  <span className="text-[11px] text-gray-500 tabular-nums w-10 text-right shrink-0">{fmtVol(vol)}</span>
                </div>
              )
            })}
          </div>
        </>
      )}
    </section>
  )
}

export default function DashboardPage() {
  const [date, setDate] = useState(today())
  const [summary, setSummary] = useState<DailySummary | null>(null)
  const [trends, setTrends] = useState<DashboardTrends | null>(null)
  const [workouts, setWorkouts] = useState<Workout[]>([])
  const [loading, setLoading] = useState(true)
  const [sessions, setSessions] = useState<WkSession[]>([])
  const [volumeByRegion, setVolumeByRegion] = useState<VolumeByRegion | null>(null)

  useEffect(() => {
    const loadDashboard = async () => {
      setLoading(true)
      try {
        const [dailyData, trendData, workoutData] = await Promise.all([
          getDailySummary(date),
          getDashboardTrends(date),
          getWorkouts(date),
        ])
        setSummary(dailyData)
        setTrends(trendData)
        setWorkouts(workoutData)
      } catch {
        setSummary(null)
        setTrends(null)
        setWorkouts([])
      } finally {
        setLoading(false)
      }
    }

    loadDashboard()
  }, [date])

  useEffect(() => {
    Promise.all([
      getWorkoutSessions(undefined, undefined, 10).catch(() => []),
      getVolumeByRegion(7).catch(() => null),
    ]).then(([s, v]) => {
      setSessions(s as WkSession[])
      setVolumeByRegion(v as VolumeByRegion | null)
    })
  }, [])

  const activeTarget = summary?.active_macro_target ?? null

  return (
    <ScrollablePage className="space-y-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Daily Summary</h1>
          <p className="text-sm text-gray-500 mt-1">
            Daily totals for the selected date. Weight trend from first entry since current target; macro trends over the last 7 days.
          </p>
        </div>
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
        />
      </div>

      {loading ? (
        <p className="text-gray-500">Loading...</p>
      ) : summary && trends ? (
        <>
          <section className="bg-white border border-gray-200 rounded-2xl p-5">
            <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
              <div>
                <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
                  Selected Day
                </p>
                <h2 className="text-xl font-semibold text-gray-900 mt-1">{shortDateLabel(summary.date)}</h2>
              </div>
              <p className="text-sm text-gray-500">
                {activeTarget
                  ? `Active target starts ${shortDateLabel(activeTarget.day)}`
                  : 'No active target yet'}
              </p>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
              {MACRO_KEYS.map((macro) => (
                <div key={macro} className="rounded-xl bg-gray-50 border border-gray-100 p-3">
                  <p className="text-xs text-gray-500">{MACRO_LABELS[macro]}</p>
                  <p className="text-xl font-semibold text-gray-900 mt-1">
                    {summary[`total_${macro}` as keyof DailySummary] as number}
                    <span className="text-xs font-normal text-gray-400 ml-1">
                      {MACRO_UNITS[macro]}
                    </span>
                  </p>
                  <p className="text-[11px] text-gray-500 mt-1">
                    {activeTarget
                      ? `Target ${Math.round(activeTarget[macro])}${MACRO_UNITS[macro]}`
                      : 'No target'}
                  </p>
                </div>
              ))}
            </div>
            <div className="mt-5">
              <p className="text-sm font-medium text-gray-700 mb-2">Meals</p>
              {summary.meals.length === 0 ? (
                <p className="text-sm text-gray-400">No meals logged for this day.</p>
              ) : (
                <div className="space-y-2">
                  {summary.meals.map((meal) => (
                    <div
                      key={meal.id}
                      className="rounded-lg border border-gray-200 bg-white p-3"
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-sm font-medium text-gray-900 capitalize">
                          {meal.meal_type}
                        </span>
                        <span className="text-xs text-gray-500">
                          {Math.round(meal.total_calories)} kcal
                        </span>
                      </div>
                      {meal.notes && <p className="text-xs text-gray-500 mb-1">{meal.notes}</p>}
                      <div className="space-y-0.5">
                        {meal.items.map((item, idx) => (
                          <div
                            key={`${meal.id}-${idx}`}
                            className="flex items-center justify-between text-xs text-gray-600"
                          >
                            <span>{item.name}</span>
                            <span className="text-gray-400">
                              {Math.round(item.grams)}g · {Math.round(item.calories)} cal
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>

          <div className="grid grid-cols-1 xl:grid-cols-[1.15fr_0.85fr] gap-6">
            <WeightTrendCard trends={trends} />
            <DailyTargetsBreakdownCard trends={trends} />
          </div>

          <TargetNormalizedMacroTrendsCard trends={trends} />

          {volumeByRegion && <MuscleVolumeCard data={volumeByRegion} />}
          <RecentSessionsCard sessions={sessions} />

          {workouts.length > 0 && (
            <section className="bg-white border border-gray-200 rounded-2xl p-5">
              <h2 className="text-lg font-semibold text-gray-900 mb-3">Workouts</h2>
              <div className="space-y-2">
                {workouts.map((workout) => (
                  <div
                    key={workout.id}
                    className="bg-white p-3 rounded-lg border border-gray-200 flex items-center justify-between"
                  >
                    <div>
                      <span className="text-sm font-medium text-gray-900">
                        {workout.workout_type}
                      </span>
                      <span className="text-xs text-gray-400 ml-2">
                        {Math.round(workout.duration_minutes)} min
                        {workout.distance_km
                          ? ` · ${workout.distance_km.toFixed(1)} km`
                          : ''}
                      </span>
                    </div>
                    <span className="text-sm font-semibold text-orange-600">
                      −{Math.round(workout.active_calories)} kcal
                    </span>
                  </div>
                ))}
                {(() => {
                  const totalBurned = workouts.reduce(
                    (sum, workout) => sum + workout.active_calories,
                    0,
                  )
                  const netCalories = summary.total_calories - totalBurned
                  return (
                    <div className="bg-white p-3 rounded-lg border border-gray-200 flex items-center justify-between">
                      <span className="text-sm text-gray-500">
                        Net calories ({Math.round(summary.total_calories)} eaten − {Math.round(totalBurned)} burned)
                      </span>
                      <span
                        className={`text-sm font-semibold ${
                          netCalories < 0 ? 'text-green-600' : 'text-gray-900'
                        }`}
                      >
                        {Math.round(netCalories)} kcal
                      </span>
                    </div>
                  )
                })()}
              </div>
            </section>
          )}
        </>
      ) : null}
    </ScrollablePage>
  )
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ScrollablePage from '../components/ScrollablePage'
import {
  getDailySummary,
  getDashboardTrends,
  getWorkouts,
  getWorkoutSessions,
  getVolumeByRegion,
  deleteWorkoutSession,
  putTodayWeight,
  deleteMeal,
  updateMeal,
  createMeal,
  searchFoodsAndRecipes,
  MACRO_KEYS,
  MACRO_LABELS,
  MACRO_UNITS,
  type DailySummary,
  type DashboardTrends,
  type Workout,
  type WkSession,
  type VolumeByRegion,
  type FoodSearchResult,
} from '../api'
import MealItemEditor from '../components/MealItemEditor'
import WorkoutSetEditor from '../components/WorkoutSetEditor'

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

function TodayWeightInput({
  trends,
  onSaved,
}: {
  trends: DashboardTrends
  onSaved: () => void
}) {
  const todayStr = today()
  const todayDay = trends.days.find((d) => d.date === todayStr)
  const initialWeight = todayDay?.weight_lb != null ? todayDay.weight_lb.toFixed(1) : ''

  const [value, setValue] = useState(initialWeight)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    const w = todayDay?.weight_lb != null ? todayDay.weight_lb.toFixed(1) : ''
    setValue(w)
    setDirty(false)
  }, [todayDay?.weight_lb])

  const handleSave = async () => {
    const parsed = parseFloat(value)
    if (isNaN(parsed) || parsed <= 0) return
    setSaving(true)
    try {
      await putTodayWeight(parsed)
      setDirty(false)
      onSaved()
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded-xl px-3 py-2">
      <label className="text-xs font-medium text-gray-500 whitespace-nowrap">
        Today&apos;s Weight
      </label>
      <input
        type="number"
        step="0.1"
        min="0"
        placeholder="e.g. 165.0"
        value={value}
        onChange={(e) => {
          setValue(e.target.value)
          setDirty(true)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') handleSave()
        }}
        className="w-20 px-2 py-1 text-sm border border-gray-300 rounded-lg bg-white
                   text-gray-900 tabular-nums focus:outline-none focus:ring-2
                   focus:ring-teal-500 focus:border-teal-500"
      />
      <span className="text-xs text-gray-400">lb</span>
      <button
        onClick={handleSave}
        disabled={saving || !dirty || !value}
        className="px-2.5 py-1 text-xs font-medium rounded-lg
                   bg-teal-600 text-white hover:bg-teal-700
                   disabled:opacity-40 disabled:cursor-not-allowed
                   transition-colors"
      >
        {saving ? '…' : 'Save'}
      </button>
    </div>
  )
}

function WeightTrendCard({
  trends,
  onWeightSaved,
}: {
  trends: DashboardTrends
  onWeightSaved: () => void
}) {
  const chart = useMemo(() => {
    const width = 340
    const height = 230
    const left = 22
    const right = 18
    const top = 16
    const bottom = 66
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
        dateLabels: [] as { x: number; label: string }[],
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

    // Auto-detect label spacing: rotated labels need less horizontal room
    const minLabelGap = 20
    const labelStep = Math.max(1, Math.ceil(minLabelGap / step))

    const dateLabels = weightDays.map((day, index) => {
      if (index % labelStep !== 0 && index !== weightDays.length - 1) return null
      return {
        x: left + step * index,
        label: shortDateLabel(day.date),
      }
    }).filter(Boolean) as { x: number; label: string }[]

    return {
      width, height, weightDays, actualPoints, regressionPoints, guides, dateLabels,
    }
  }, [trends])

  const cs = trends.calorie_stats

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
          {cs && (
            <p className="text-sm text-gray-500 mt-1">
              Avg {cs.avg_calories_per_day} ± {cs.std_calories_per_day} kcal/day
              <span className="text-gray-400 ml-1">({cs.days_counted}d)</span>
            </p>
          )}
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
          {trends.tdee_estimate !== null && (
            <p className="text-sm font-medium text-teal-700 mt-1">
              Est. TDEE {trends.tdee_estimate} kcal/day
            </p>
          )}
        </div>
      </div>

      <TodayWeightInput trends={trends} onSaved={onWeightSaved} />

      {chart.actualPoints.length > 0 ? (
        <svg viewBox={`0 0 ${chart.width} ${chart.height}`} className="w-full">
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
            <circle key={point.date} cx={point.x} cy={point.y} r="4.5" fill="#0f766e" />
          ))}
          {chart.dateLabels.map((dl) => (
            <text
              key={dl.x}
              x={dl.x}
              y={chart.height - 10}
              textAnchor="end"
              fontSize="10"
              fill="#6b7280"
              transform={`rotate(-45 ${dl.x} ${chart.height - 10})`}
            >
              {dl.label}
            </text>
          ))}
        </svg>
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

// Formula 1-inspired dot: purple = PR, green = all reps completed, yellow = partial/failed
type F1Status = 'pr' | 'complete' | 'partial'

function f1Dot(status: F1Status): string {
  if (status === 'pr') return 'bg-purple-500'
  if (status === 'complete') return 'bg-green-500'
  return 'bg-yellow-400'
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

function formatRecentSessionSet(set: WkSession['sets'][number]) {
  if (set.reps != null && set.weight != null) return `${set.weight}×${set.reps}`
  if (set.duration_secs != null) return `${set.duration_secs}s`
  return '—'
}

function formatRecentSessionRpe(set: WkSession['sets'][number]) {
  return set.rpe == null ? 'RPE —' : `RPE ${set.rpe}`
}

// ── Recent Sessions ──

function RecentSessionsCard({
  sessions,
  onSessionChanged,
}: {
  sessions: WkSession[]
  onSessionChanged?: () => void
}) {
  const [expandedDates, setExpandedDates] = useState<string[]>([])
  const [editingDates, setEditingDates] = useState<Set<string>>(new Set())
  const [deleting, setDeleting] = useState<Set<number>>(new Set())

  // Group multiple sessions on the same date into one entry
  const byDate = useMemo(() => {
    const map = new Map<string, WkSession[]>()
    for (const ws of sessions) {
      const list = map.get(ws.date) ?? []
      list.push(ws)
      map.set(ws.date, list)
    }

    // Pre-compute max weight per exercise per date for PR detection
    const dateMaxByExercise = new Map<string, Map<string, number>>()
    for (const [date, daySessions] of map) {
      const exMax = new Map<string, number>()
      for (const ws of daySessions) {
        for (const s of ws.sets) {
          const cur = exMax.get(s.exercise_name) ?? 0
          if ((s.weight ?? 0) > cur) exMax.set(s.exercise_name, s.weight ?? 0)
        }
      }
      dateMaxByExercise.set(date, exMax)
    }

    // Sort dates descending, return array of merged day entries
    return Array.from(map.entries())
      .sort((a, b) => b[0].localeCompare(a[0]))
      .map(([date, daySessions]) => {
        const allSets = daySessions.flatMap(s => s.sets)
        const exerciseMap = groupSetsByExercise(allSets)
        const totalVolume = allSets.reduce((sum, s) => sum + (s.reps || 0) * (s.weight || 0), 0)
        const rpeMissingCount = allSets.filter(s => s.rpe == null).length
        const notes = daySessions.map(s => s.notes).filter(Boolean).join(' · ')

        // F1 status per exercise for this date
        const f1Statuses = new Map<string, F1Status>()
        for (const [name, sets] of exerciseMap) {
          const tracked = sets.filter(s => s.rep_completion != null)
          if (tracked.length === 0) continue
          const allFull = tracked.every(s => s.rep_completion === 'full')
          if (!allFull) {
            f1Statuses.set(name, 'partial')
            continue
          }
          const thisMax = dateMaxByExercise.get(date)?.get(name) ?? 0
          let histMax = 0
          for (const [otherDate, otherMap] of dateMaxByExercise) {
            if (otherDate === date) continue
            const v = otherMap.get(name) ?? 0
            if (v > histMax) histMax = v
          }
          f1Statuses.set(name, thisMax > 0 && histMax > 0 && thisMax > histMax ? 'pr' : 'complete')
        }

        return { date, sessions: daySessions, exerciseMap, totalVolume, rpeMissingCount, notes, f1Statuses }
      })
  }, [sessions])

  if (byDate.length === 0) return null

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400 mb-3">Recent Sessions</p>
      <div className="space-y-2">
        {byDate.map(({ date, sessions: daySessions, exerciseMap, totalVolume, rpeMissingCount, notes, f1Statuses }) => {
          const isExpanded = expandedDates.includes(date)
          return (
            <div key={date} className="rounded-xl border border-gray-200">
              <button
                onClick={() =>
                  setExpandedDates((current) =>
                    current.includes(date)
                      ? current.filter((expandedDate) => expandedDate !== date)
                      : [...current, date],
                  )
                }
                className="w-full text-left px-3 py-2 flex items-center gap-3"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-800">
                    {new Date(date + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
                  </p>
                  {exerciseMap.size === 0 ? (
                    <p className="text-xs text-gray-400">
                      Empty session{daySessions.length > 1 ? 's' : ''}
                      <span
                        role="button"
                        tabIndex={0}
                        className="ml-2 text-red-400 hover:text-red-600 cursor-pointer"
                        onClick={async (e) => {
                          e.stopPropagation()
                          for (const ws of daySessions) {
                            if (ws.sets.length === 0) {
                              setDeleting((p) => new Set([...p, ws.id]))
                              try {
                                await deleteWorkoutSession(ws.id)
                              } finally {
                                setDeleting((p) => {
                                  const n = new Set(p)
                                  n.delete(ws.id)
                                  return n
                                })
                              }
                            }
                          }
                          onSessionChanged?.()
                        }}
                      >
                        🗑 delete
                      </span>
                    </p>
                  ) : (
                    <p className="text-xs text-gray-500">
                      {exerciseMap.size} exercise{exerciseMap.size !== 1 ? 's' : ''}
                      {totalVolume > 0 && ` · ${Math.round(totalVolume).toLocaleString()} lbs vol`}
                      <span
                        className={
                          rpeMissingCount > 0 ? 'font-medium text-amber-700' : 'text-emerald-700'
                        }
                      >
                        {` · ${
                          rpeMissingCount > 0
                            ? `${rpeMissingCount} RPE missing`
                            : 'RPE complete'
                        }`}
                      </span>
                    </p>
                  )}
                </div>
                <div className="flex gap-0.5">
                  {Array.from(f1Statuses.values()).map((status, i) => (
                    <span key={i} className={`w-2 h-2 rounded-full ${f1Dot(status)}`} />
                  ))}
                </div>
                {isExpanded && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      setEditingDates((prev) => {
                        const next = new Set(prev)
                        if (next.has(date)) next.delete(date)
                        else next.add(date)
                        return next
                      })
                    }}
                    className="text-[10px] text-gray-400 hover:text-gray-600"
                  >
                    {editingDates.has(date) ? 'done' : 'edit'}
                  </button>
                )}
                <span className="text-gray-400 text-xs">{isExpanded ? '−' : '+'}</span>
              </button>
              {isExpanded && (
                <div className="px-3 pb-3 space-y-2">
                  {editingDates.has(date) ? (
                    <>
                      {daySessions.map((ws) => (
                        <div key={ws.id} className="space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="text-[10px] text-gray-400">
                              Session #{ws.id} · {ws.sets.length} set{ws.sets.length !== 1 ? 's' : ''}
                            </span>
                            <button
                              onClick={async () => {
                                setDeleting((p) => new Set([...p, ws.id]))
                                try {
                                  await deleteWorkoutSession(ws.id)
                                  onSessionChanged?.()
                                } finally {
                                  setDeleting((p) => {
                                    const n = new Set(p)
                                    n.delete(ws.id)
                                    return n
                                  })
                                }
                              }}
                              disabled={deleting.has(ws.id)}
                              className="text-[10px] text-red-400 hover:text-red-600
                                disabled:opacity-40"
                            >
                              {deleting.has(ws.id) ? 'deleting…' : '🗑 delete session'}
                            </button>
                          </div>
                          {ws.sets.length > 0 && (
                            <WorkoutSetEditor
                              mode="log"
                              sessionId={ws.id}
                              session={ws}
                              onSessionChanged={onSessionChanged}
                              compact
                            />
                          )}
                        </div>
                      ))}
                    </>
                  ) : (
                    <>
                      {Array.from(exerciseMap.entries()).map(([name, sets]) => {
                        const exerciseStatus = f1Statuses.get(name)
                        const missingRpeCount = sets.filter((s) => s.rpe == null).length
                        return (
                          <div key={name}>
                            <div className="flex flex-wrap items-center gap-2">
                              {exerciseStatus && (
                                <span
                                  className={`h-2 w-2 shrink-0 rounded-full ${f1Dot(exerciseStatus)}`}
                                />
                              )}
                              <p className="text-xs font-medium text-gray-700">{name}</p>
                              {missingRpeCount > 0 && (
                                <span className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                                  {missingRpeCount} RPE missing
                                </span>
                              )}
                            </div>
                            <div className="flex flex-wrap gap-1 mt-0.5">
                              {sets.map((s) => {
                                const missingRpe = s.rpe == null
                                return (
                                  <span
                                    key={s.id}
                                    className={`rounded px-1.5 py-0.5 text-[11px] ${
                                      missingRpe
                                        ? 'bg-amber-50 font-medium text-amber-700'
                                        : 'bg-gray-50 text-gray-500'
                                    }`}
                                  >
                                    {formatRecentSessionSet(s)} · {formatRecentSessionRpe(s)}
                                  </span>
                                )
                              })}
                            </div>
                          </div>
                        )
                      })}
                      {notes && <p className="text-xs text-gray-400 italic">{notes}</p>}
                    </>
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

const MEAL_TYPES = ['breakfast', 'lunch', 'dinner', 'snack'] as const

function QuickAddMeal({ date, onAdded }: { date: string; onAdded: () => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<FoodSearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [mealType, setMealType] = useState<string>('snack')
  const [saving, setSaving] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const search = useCallback((q: string) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    if (q.length < 2) { setResults([]); return }
    timerRef.current = setTimeout(async () => {
      setLoading(true)
      try { setResults(await searchFoodsAndRecipes(q)) }
      catch { setResults([]) }
      finally { setLoading(false) }
    }, 300)
  }, [])

  const handleSelect = async (item: FoodSearchResult) => {
    setSaving(true)
    try {
      const mealItem = item.type === 'food'
        ? { food_id: item.id, amount_grams: item.serving_size_grams ?? 100 }
        : { recipe_id: item.id, amount_grams: item.total_grams ?? 100 }
      await createMeal({ date, meal_type: mealType, items: [mealItem] })
      setQuery('')
      setResults([])
      onAdded()
    } catch { /* ignore */ }
    finally { setSaving(false) }
  }

  return (
    <div className="mt-3 border border-dashed border-gray-300 rounded-lg p-3">
      <div className="flex items-center gap-2 mb-2">
        <p className="text-xs font-medium text-gray-500">Quick Add</p>
        <select
          value={mealType}
          onChange={(e) => setMealType(e.target.value)}
          className="text-xs border border-gray-300 rounded px-1.5 py-0.5
                     bg-white text-gray-700"
        >
          {MEAL_TYPES.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </div>
      <input
        type="text"
        className="w-full text-sm border border-gray-300 rounded px-2 py-1.5"
        placeholder="Search foods &amp; recipes…"
        value={query}
        onChange={(e) => { setQuery(e.target.value); search(e.target.value) }}
      />
      {loading && <p className="text-xs text-gray-400 mt-1">Searching…</p>}
      {saving && <p className="text-xs text-blue-500 mt-1">Adding…</p>}
      {results.length > 0 && !saving && (
        <div className="mt-1 max-h-44 overflow-y-auto space-y-0.5">
          {results.map((r) => (
            <button
              key={`${r.type}-${r.id}`}
              type="button"
              className="w-full text-left text-sm px-2 py-1.5 rounded
                         hover:bg-blue-50 text-gray-700 flex items-center
                         justify-between gap-2"
              onClick={() => handleSelect(r)}
            >
              <span className="truncate">{r.name}</span>
              <span className="flex items-center gap-1.5 shrink-0">
                <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${
                  r.type === 'food'
                    ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-violet-100 text-violet-700'
                }`}>
                  {r.type}
                </span>
                <span className="text-xs text-gray-400">
                  {r.type === 'food'
                    ? `${r.serving_size_grams}g · ${Math.round(r.calories_per_serving ?? 0)} cal`
                    : `${r.total_grams}g · ${Math.round(r.total_calories ?? 0)} cal`}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
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
  const [refreshKey, setRefreshKey] = useState(0)
  const [editingMeals, setEditingMeals] = useState<Set<number>>(new Set())

  const refreshTrends = () => setRefreshKey((k) => k + 1)

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
  }, [date, refreshKey])

  useEffect(() => {
    Promise.all([
      getWorkoutSessions(undefined, undefined, 10).catch(() => []),
      getVolumeByRegion(7, today()).catch(() => null),
    ]).then(([s, v]) => {
      setSessions(s as WkSession[])
      setVolumeByRegion(v as VolumeByRegion | null)
    })
  }, [])

  const activeTarget = summary?.active_macro_target ?? null

  const refreshSessions = useCallback(() => {
    getWorkoutSessions(undefined, undefined, 10)
      .then((s) => setSessions(s as WkSession[]))
      .catch(() => {})
  }, [])

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
                        {editingMeals.has(meal.id) ? (
                          <select
                            value={meal.meal_type}
                            onChange={async (e) => {
                              try {
                                await updateMeal(meal.id, { meal_type: e.target.value })
                                refreshTrends()
                              } catch { /* ignore */ }
                            }}
                            className="text-sm font-medium text-gray-900 border
                                       border-gray-300 rounded px-1.5 py-0.5 bg-white
                                       capitalize"
                          >
                            {MEAL_TYPES.map((t) => (
                              <option key={t} value={t}>{t}</option>
                            ))}
                          </select>
                        ) : (
                          <span className="text-sm font-medium text-gray-900 capitalize">
                            {meal.meal_type}
                          </span>
                        )}
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-gray-500">
                            {Math.round(meal.total_calories)} kcal
                          </span>
                          {editingMeals.has(meal.id) && meal.items.length === 0 && (
                            <button
                              type="button"
                              className="text-xs text-red-500 hover:text-red-700"
                              onClick={async () => {
                                try {
                                  await deleteMeal(meal.id)
                                  setEditingMeals((prev) => {
                                    const next = new Set(prev)
                                    next.delete(meal.id)
                                    return next
                                  })
                                  refreshTrends()
                                } catch { /* ignore */ }
                              }}
                            >
                              delete
                            </button>
                          )}
                          <button
                            type="button"
                            className="text-xs text-blue-500 hover:text-blue-700"
                            onClick={() => setEditingMeals((prev) => {
                              const next = new Set(prev)
                              if (next.has(meal.id)) next.delete(meal.id)
                              else next.add(meal.id)
                              return next
                            })}
                          >
                            {editingMeals.has(meal.id) ? 'done' : 'edit'}
                          </button>
                        </div>
                      </div>
                      {meal.notes && <p className="text-xs text-gray-500 mb-1">{meal.notes}</p>}
                      {editingMeals.has(meal.id) ? (
                        <MealItemEditor
                          mode="edit"
                          meal={meal}
                          onMealChanged={refreshTrends}
                          compact
                        />
                      ) : (
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
                      )}
                    </div>
                  ))}
                </div>
              )}
              <QuickAddMeal date={date} onAdded={refreshTrends} />
            </div>
          </section>

          <div className="grid grid-cols-1 xl:grid-cols-[1.15fr_0.85fr] gap-6">
            <WeightTrendCard trends={trends} onWeightSaved={refreshTrends} />
            <DailyTargetsBreakdownCard trends={trends} />
          </div>

          <TargetNormalizedMacroTrendsCard trends={trends} />

          {volumeByRegion && <MuscleVolumeCard data={volumeByRegion} />}
          <RecentSessionsCard sessions={sessions} onSessionChanged={refreshSessions} />

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

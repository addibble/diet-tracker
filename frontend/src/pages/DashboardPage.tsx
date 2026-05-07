import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ScrollablePage from '../components/ScrollablePage'
import {
  getDailySummary,
  getDashboardTrends,
  getWorkouts,
  getWorkoutSessions,
  deleteWorkoutSession,
  putTodayWeight,
  deleteMeal,
  updateMeal,
  createMeal,
  searchFoodsAndRecipes,
  getVolumeByRegion,
  MACRO_KEYS,
  MACRO_LABELS,
  MACRO_UNITS,
  type DailySummary,
  type DashboardTrends,
  type Workout,
  type WkSession,
  type FoodSearchResult,
  type VolumeByRegion,
} from '../api'
import MealItemEditor from '../components/MealItemEditor'
import WorkoutSetEditor from '../components/WorkoutSetEditor'
import { type CompletedSet } from '../components/CurvePane'
import CurvePaneWithFatigue from '../components/CurvePaneWithFatigue'
import { SessionBetaEvolutionSparkline } from '../components/SessionBetaEvolutionSparkline'
import { asEntered, asRepsDone, asRir } from '../lib/units'
import { getCurveSnapshot, type CurveSnapshotResponse } from '../api/planner'

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
    const availableWidth = width - left - right
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

    const firstDate = localDate(weightDays[0].date).getTime()
    const lastDate = localDate(weightDays[weightDays.length - 1].date).getTime()
    const totalMs = Math.max(lastDate - firstDate, 1)
    const toX = (dateStr: string) =>
      left + ((localDate(dateStr).getTime() - firstDate) / totalMs) * availableWidth

    const actualPoints = weightDays.map((day) => ({
      x: toX(day.date),
      y: toY(day.weight_lb),
      value: day.weight_lb,
      date: day.date,
    }))
    const regressionPoints = (trends.weight_regression?.line ?? []).map((point) => ({
      x: toX(point.date),
      y: toY(point.weight_lb),
    }))
    const guideValues = [scaledMax, (scaledMax + scaledMin) / 2, scaledMin]
    const guides = guideValues.map((value) => ({
      y: toY(value),
      label: value.toFixed(1),
    }))

    const minLabelGap = 20
    const dateLabels: { x: number; label: string }[] = []
    let lastLabelX = -Infinity
    for (let i = 0; i < weightDays.length; i++) {
      const x = toX(weightDays[i].date)
      if (x - lastLabelX >= minLabelGap || i === weightDays.length - 1) {
        dateLabels.push({ x, label: shortDateLabel(weightDays[i].date) })
        lastLabelX = x
      }
    }

    return {
      width, height, weightDays, actualPoints, regressionPoints, guides, dateLabels,
    }
  }, [trends])

  const cs = trends.calorie_stats

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5 pb-10">
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
        <svg viewBox={`0 0 ${chart.width} ${chart.height}`} className="w-full" overflow="visible">
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
  const mode = set.set_metric_mode ?? 'reps'
  // Prefer the unified ``endurance_value``; fall back to legacy columns
  // for sets that haven't been backfilled yet.
  const value = set.endurance_value
    ?? (mode === 'duration' ? set.duration_secs
      : mode === 'distance' ? set.distance_steps
      : set.reps)
  if (value == null) return '—'
  if (mode === 'duration') return `${Math.round(value)}s`
  if (mode === 'distance') return `${Math.round(value)} steps`
  if (set.weight != null) return `${set.weight}×${Math.round(value)}`
  return `${Math.round(value)}`
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
  // snapshot cache keyed by `${date}:${exercise_id}`
  const [snapshots, setSnapshots] = useState<
    Map<string, CurveSnapshotResponse | 'loading' | 'error'>
  >(new Map())

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
        const totalVolume = daySessions.reduce((sum, s) => sum + (s.effective_volume ?? 0), 0)
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

  // When a date expands (and not in editing mode) fetch curve snapshots
  // for each exercise shown that day. Cache per (date, exercise_id).
  useEffect(() => {
    for (const date of expandedDates) {
      if (editingDates.has(date)) continue
      const day = byDate.find((d) => d.date === date)
      if (!day) continue
      // Resolve the first (earliest-ordered) exercise_id for each name.
      const byName = new Map<string, number>()
      for (const [name, sets] of day.exerciseMap) {
        const withId = sets.find((s) => s.exercise_id != null)
        if (withId?.exercise_id != null) byName.set(name, withId.exercise_id)
      }
      for (const [, exId] of byName) {
        const key = `${date}:${exId}`
        if (snapshots.has(key)) continue
        setSnapshots((prev) => {
          if (prev.has(key)) return prev
          const next = new Map(prev)
          next.set(key, 'loading')
          return next
        })
        getCurveSnapshot(exId, date)
          .then((resp) => {
            setSnapshots((prev) => {
              const next = new Map(prev)
              next.set(key, resp)
              return next
            })
          })
          .catch(() => {
            setSnapshots((prev) => {
              const next = new Map(prev)
              next.set(key, 'error')
              return next
            })
          })
      }
    }
  }, [expandedDates, editingDates, byDate, snapshots])

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
                      {totalVolume > 0 && ` · ${Math.round(totalVolume).toLocaleString()} vol`}
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
                  {/* In-session β evolution sparklines (one per session in
                      the day). Lazy-mounted on expand so the per-exercise
                      curve fits don't run for collapsed days. */}
                  {daySessions
                    .filter((ws) => ws.sets.length > 0)
                    .map((ws, i, arr) => (
                      <SessionBetaEvolutionSparkline
                        key={`spark-${ws.id}`}
                        sessionId={ws.id}
                        exerciseName={arr.length > 1 ? `Session ${i + 1}` : 'Session'}
                      />
                    ))}
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
                        const exId = sets.find((s) => s.exercise_id != null)?.exercise_id
                        const snap = exId != null ? snapshots.get(`${date}:${exId}`) : undefined
                        const snapData =
                          snap && snap !== 'loading' && snap !== 'error' ? snap : null
                        const completedSets: CompletedSet[] = sets
                          .filter(
                            (s) =>
                              s.weight != null && s.reps != null && s.rpe != null,
                          )
                          .map((s) => ({
                            weight: asEntered(s.weight as number),
                            reps: asRepsDone(s.reps as number),
                            rir: asRir(Math.max(0, Math.round(10 - (s.rpe as number)))),
                          }))
                        const showCurve =
                          snapData?.has_curve &&
                          snapData.curve != null &&
                          completedSets.length > 0
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
                            {showCurve ? (
                              <div className="mt-1">
                                <CurvePaneWithFatigue
                                  exerciseId={exId ?? null}
                                  sessionDate={date}
                                  mode="completed"
                                  curve={snapData.curve ?? null}
                                  priorCurve={snapData.curve_prior ?? null}
                                  observations={(snapData.observations ?? []).map(o => ({
                                    weight: asEntered(o.weight),
                                    reps: asRepsDone(o.reps),
                                    rir: o.rir == null ? undefined : asRir(o.rir),
                                    age_days: o.age_days,
                                  }))}
                                  sparkWeight={asEntered(0)}
                                  sparkReps={asRepsDone(0)}
                                  schemeRir={asRir(0)}
                                  schemeSetNumber={completedSets.length}
                                  onSparkChange={() => {}}
                                  onGo={() => {}}
                                  onConfirmRir={() => {}}
                                  completedSets={completedSets}
                                />
                              </div>
                            ) : (
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
                            )}
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

// Okabe-Ito 8-color palette: colorblind-safe, perceptually distinct.
// Mapping anchored on the most commonly used regions; rotates through the
// palette for any region not explicitly listed.
const OKABE_ITO = [
  '#E69F00', // orange
  '#56B4E9', // sky blue
  '#009E73', // bluish green
  '#F0E442', // yellow
  '#0072B2', // blue
  '#D55E00', // vermillion
  '#CC79A7', // reddish purple
  '#999999', // grey
] as const

// Explicit per-region colors. Anchored on the Okabe-Ito colorblind-safe
// palette and extended with shifted variants so each canonical region gets
// a stable, intentional color. The set matches CANONICAL_REGION_ORDER in
// backend/app/tissue_regions.py.
const REGION_COLORS: Record<string, string> = {
  // upper body
  chest: '#E69F00',       // orange
  shoulders: '#F0E442',   // yellow
  triceps: '#D55E00',     // vermillion
  biceps: '#0072B2',      // blue
  forearms: '#999999',    // grey
  upper_back: '#56B4E9',  // sky blue (now also includes neck stabilizers)
  lower_back: '#2A4D7A',  // dark blue
  core: '#444444',        // dark grey
  // lower body
  quads: '#009E73',       // green (now also includes adductors)
  hamstrings: '#CC79A7',  // reddish purple
  glutes: '#7C4DA0',      // purple (now also includes abductors / TFL)
  calves: '#5A8F3C',      // olive green
  shins: '#8FBC5C',       // light green
}

function regionColor(region: string, index: number): string {
  return REGION_COLORS[region] ?? OKABE_ITO[index % OKABE_ITO.length]
}

function formatRegionLabel(region: string): string {
  return region.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function MuscleVolumeCard({ data }: { data: VolumeByRegion | null }) {
  if (!data || data.regions.length === 0) {
    return (
      <section className="bg-white border border-gray-200 rounded-2xl p-5">
        <h2 className="text-lg font-semibold text-gray-900 mb-1">
          Volume by Muscle Group
        </h2>
        <p className="text-sm text-gray-500">No training volume in the last 10 days.</p>
      </section>
    )
  }

  const dailyTotals = data.dates.map((_, i) =>
    data.regions.reduce((sum, r) => sum + (data.daily[r]?.[i] ?? 0), 0),
  )
  const dayMax = Math.max(...dailyTotals, 1)
  const grandTotal = data.regions.reduce((s, r) => s + (data.totals[r] ?? 0), 0)
  const totalMax = Math.max(...data.regions.map((r) => data.totals[r] ?? 0), 1)

  // SVG layout
  const W = 560
  const H = 180
  const pad = { top: 8, right: 8, bottom: 22, left: 8 }
  const innerW = W - pad.left - pad.right
  const innerH = H - pad.top - pad.bottom
  const n = data.dates.length
  const barW = (innerW / n) * 0.7
  const gap = (innerW / n) * 0.3

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <h2 className="text-lg font-semibold text-gray-900">Volume by Muscle Group</h2>
        <span className="text-xs text-gray-500">
          Last {n} days · {Math.round(grandTotal).toLocaleString()} lb total
        </span>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        role="img"
        aria-label="Stacked daily training volume by muscle group"
      >
        {data.dates.map((d, i) => {
          const x = pad.left + i * (barW + gap) + gap / 2
          let yCursor = pad.top + innerH
          const stack = data.regions.map((r, ri) => {
            const v = data.daily[r]?.[i] ?? 0
            const h = (v / dayMax) * innerH
            yCursor -= h
            return (
              <rect
                key={r}
                x={x}
                y={yCursor}
                width={barW}
                height={h}
                fill={regionColor(r, ri)}
              >
                <title>
                  {formatRegionLabel(r)}: {Math.round(v).toLocaleString()} lb on{' '}
                  {shortDateLabel(d)}
                </title>
              </rect>
            )
          })
          return (
            <g key={d}>
              {stack}
              <text
                x={x + barW / 2}
                y={H - 6}
                fontSize={10}
                textAnchor="middle"
                fill="#6b7280"
              >
                {weekdayLabel(d).slice(0, 1)}
              </text>
            </g>
          )
        })}
      </svg>

      <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5">
        {data.regions.map((r, ri) => {
          const total = data.totals[r] ?? 0
          const pct = (total / totalMax) * 100
          return (
            <div key={r} className="flex items-center gap-2 text-xs">
              <span
                className="inline-block w-3 h-3 rounded-sm flex-shrink-0"
                style={{ backgroundColor: regionColor(r, ri) }}
              />
              <span className="text-gray-700 w-20 flex-shrink-0">
                {formatRegionLabel(r)}
              </span>
              <div className="flex-1 h-2 bg-gray-100 rounded-sm overflow-hidden">
                <div
                  className="h-full"
                  style={{ width: `${pct}%`, backgroundColor: regionColor(r, ri) }}
                />
              </div>
              <span className="text-gray-500 tabular-nums w-14 text-right">
                {Math.round(total).toLocaleString()}
              </span>
            </div>
          )
        })}
      </div>
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
  const [refreshKey, setRefreshKey] = useState(0)
  const [editingMeals, setEditingMeals] = useState<Set<number>>(new Set())

  const refreshTrends = () => setRefreshKey((k) => k + 1)

  // Lightweight refresh: only re-fetch daily summary (meals + macro totals)
  // without showing the loading spinner or re-fetching trends/workouts.
  const refreshMeals = useCallback(async () => {
    try {
      const dailyData = await getDailySummary(date)
      setSummary(dailyData)
    } catch { /* keep stale data on error */ }
  }, [date])

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
    getWorkoutSessions(undefined, undefined, 10)
      .then((s) => setSessions(s as WkSession[]))
      .catch(() => setSessions([]))
    getVolumeByRegion(10, date)
      .then(setVolumeByRegion)
      .catch(() => setVolumeByRegion(null))
  }, [date])

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
                                refreshMeals()
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
                                  refreshMeals()
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
                          onMealChanged={refreshMeals}
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
              <QuickAddMeal date={date} onAdded={refreshMeals} />
            </div>
          </section>

          <div className="grid grid-cols-1 xl:grid-cols-[1.15fr_0.85fr] gap-6">
            <WeightTrendCard trends={trends} onWeightSaved={refreshTrends} />
            <DailyTargetsBreakdownCard trends={trends} />
          </div>

          <TargetNormalizedMacroTrendsCard trends={trends} />

          <RecentSessionsCard sessions={sessions} onSessionChanged={refreshSessions} />

          <MuscleVolumeCard data={volumeByRegion} />

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

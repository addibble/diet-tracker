import { useEffect, useMemo, useState } from 'react'
import ScrollablePage from '../components/ScrollablePage'
import {
  getDailySummary,
  getDashboardTrends,
  getWorkouts,
  MACRO_KEYS,
  MACRO_LABELS,
  MACRO_UNITS,
  type DailySummary,
  type DashboardTrends,
  type Workout,
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
    const step = (width - left - right) / Math.max(trends.days.length - 1, 1)
    const actualWeights = trends.days
      .map((day) => day.weight_lb)
      .filter((value): value is number => value !== null)
    const regressionWeights = trends.weight_regression?.line.map((point) => point.weight_lb) ?? []
    const allWeights = [...actualWeights, ...regressionWeights]

    if (allWeights.length === 0) {
      return {
        width,
        height,
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

    const actualPoints = trends.days.flatMap((day, index) => {
      if (day.weight_lb === null) return []
      return [{
        x: left + step * index,
        y: toY(day.weight_lb),
        value: day.weight_lb,
        date: day.date,
      }]
    })
    const regressionPoints = (trends.weight_regression?.line ?? []).map((point, index) => ({
      x: left + step * index,
      y: toY(point.weight_lb),
    }))
    const guideValues = [scaledMax, (scaledMax + scaledMin) / 2, scaledMin]
    const guides = guideValues.map((value) => ({
      y: toY(value),
      label: value.toFixed(1),
    }))

    return { width, height, actualPoints, regressionPoints, guides }
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
            7-Day Slope
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
            {trends.days.map((day, index) => {
              const x = 22 + ((chart.width - 22 - 18) / Math.max(trends.days.length - 1, 1)) * index
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
          No weights logged in this 7-day window.
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
        <h2 className="text-xl font-semibold text-gray-900 mt-1">Estimated 7-day breakdown</h2>
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
            <div key={day.date} className="flex flex-col items-center justify-end h-full gap-2">
              <span className="text-[10px] text-gray-500 tabular-nums">
                {Math.round(day.total_calories)} kcal
              </span>
              <div className="relative w-full h-full flex items-end">
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
              <p className="text-[10px] text-amber-600 tabular-nums">
                {targetCalories > 0 ? `T ${Math.round(targetCalories)}` : 'T —'}
              </p>
              <p className="text-[10px] text-gray-500 text-center">
                F {fat.toFixed(0)} · C {carbs.toFixed(0)} · P {protein.toFixed(0)}
              </p>
              <span className="text-xs text-gray-500">{weekdayLabel(day.date)}</span>
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
    colorClass: 'bg-rose-500',
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
          Saturated Fat, Cholesterol, Sodium, Fiber (7 days)
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

export default function DashboardPage() {
  const [date, setDate] = useState(today())
  const [summary, setSummary] = useState<DailySummary | null>(null)
  const [trends, setTrends] = useState<DashboardTrends | null>(null)
  const [workouts, setWorkouts] = useState<Workout[]>([])
  const [loading, setLoading] = useState(true)

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

  const activeTarget = summary?.active_macro_target ?? null

  return (
    <ScrollablePage className="space-y-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Daily Summary</h1>
          <p className="text-sm text-gray-500 mt-1">
            Daily totals for the selected date plus 7-day weight and macro trends.
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

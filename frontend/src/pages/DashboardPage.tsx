import { useEffect, useMemo, useState } from 'react'
import {
  getDailySummary,
  getDashboardTrends,
  MACRO_KEYS,
  MACRO_LABELS,
  MACRO_UNITS,
  type DailySummary,
  type DashboardTrends,
} from '../api'

function today() {
  return new Date().toISOString().split('T')[0]
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

function CaloriesTrendCard({ trends }: { trends: DashboardTrends }) {
  const maxCalories = Math.max(...trends.days.map((day) => day.total_calories), 1)

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <div className="mb-4">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
          Calories Per Day
        </p>
        <h2 className="text-xl font-semibold text-gray-900 mt-1">Past 7 days</h2>
      </div>

      <div className="grid grid-cols-7 gap-3 items-end h-56">
        {trends.days.map((day) => {
          const ratio = day.total_calories / maxCalories
          const height = day.total_calories > 0 ? `${Math.max(ratio * 100, 6)}%` : '4%'
          return (
            <div key={day.date} className="flex flex-col items-center justify-end h-full gap-2">
              <span className="text-xs text-gray-500 tabular-nums">
                {Math.round(day.total_calories)}
              </span>
              <div className="w-full h-full flex items-end">
                <div
                  className="w-full rounded-t-2xl bg-gradient-to-b from-blue-400 to-blue-600"
                  style={{ height }}
                  title={`${shortDateLabel(day.date)}: ${Math.round(day.total_calories)} kcal`}
                />
              </div>
              <span className="text-xs text-gray-500">{weekdayLabel(day.date)}</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function MacroBreakdownCard({ trends }: { trends: DashboardTrends }) {
  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5">
      <div className="mb-4">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-400">
          Macro Mix By Calories
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

      <div className="space-y-4">
        {trends.days.map((day) => {
          const fat = day.macro_calorie_percentages.fat
          const carbs = day.macro_calorie_percentages.carbs
          const protein = day.macro_calorie_percentages.protein
          const remainder = Math.max(0, 100 - fat - carbs - protein)

          return (
            <div key={day.date}>
              <div className="flex items-center justify-between text-sm mb-1.5">
                <span className="font-medium text-gray-700">{shortDateLabel(day.date)}</span>
                <span className="text-gray-500">{Math.round(day.total_calories)} kcal</span>
              </div>
              <div className="flex h-4 overflow-hidden rounded-full bg-gray-100 border border-gray-200">
                <div className="bg-amber-400" style={{ width: `${fat}%` }} />
                <div className="bg-blue-500" style={{ width: `${carbs}%` }} />
                <div className="bg-emerald-500" style={{ width: `${protein}%` }} />
                {remainder > 0 && (
                  <div className="bg-gray-200" style={{ width: `${remainder}%` }} />
                )}
              </div>
              <p className="text-xs text-gray-500 mt-1.5">
                F {fat.toFixed(1)}% · C {carbs.toFixed(1)}% · P {protein.toFixed(1)}%
              </p>
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
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const [dailyData, trendData] = await Promise.all([
          getDailySummary(date),
          getDashboardTrends(date),
        ])
        setSummary(dailyData)
        setTrends(trendData)
      } catch {
        setSummary(null)
        setTrends(null)
      } finally {
        setLoading(false)
      }
    }

    load()
  }, [date])

  return (
    <div className="space-y-6">
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
              <p className="text-sm text-gray-500">Macro totals for the selected date</p>
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
                </div>
              ))}
            </div>
          </section>

          <div className="grid grid-cols-1 xl:grid-cols-[1.15fr_0.85fr] gap-6">
            <WeightTrendCard trends={trends} />
            <CaloriesTrendCard trends={trends} />
          </div>

          <MacroBreakdownCard trends={trends} />
        </>
      ) : null}
    </div>
  )
}

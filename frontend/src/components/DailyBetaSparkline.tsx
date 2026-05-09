import type { DailyBeta } from '../api'
import { BetaSparkline, type BetaSparklinePoint } from './BetaSparkline'

export interface DailyBetaSparklineProps {
  /** Full daily-beta payload from the backend. May be null while loading. */
  data: DailyBeta | null
  /** Optional pre-known date window (e.g. from the volume-by-region card)
   *  used to render an aligned skeleton before `data` arrives. The
   *  rendered strip always uses `data.dates` once present. */
  fallbackDates?: string[]
}

function localDate(d: string) {
  return new Date(`${d}T12:00:00`)
}
function shortDateLabel(d: string) {
  return localDate(d).toLocaleDateString(undefined, {
    month: 'short', day: 'numeric',
  })
}

/** 10-day per-session β strip aligned to the volume-by-region bar chart.
 *  One node per date in the window: workout days show a solid β node,
 *  rest days are hollow null nodes so the x-axis stays aligned with the
 *  bars below. Same green/red split styling as the Recent Sessions
 *  sparkline. */
export function DailyBetaSparkline({
  data, fallbackDates,
}: DailyBetaSparklineProps) {
  const dates = data?.dates ?? fallbackDates ?? []
  if (dates.length === 0) return null

  const byDate = new Map<string, DailyBeta['points'][number]>()
  if (data) {
    for (const p of data.points) byDate.set(p.date, p)
  }

  const points: BetaSparklinePoint[] = dates.map((d) => {
    const p = byDate.get(d)
    if (!p || !p.worked_out) {
      return { key: d, beta: null, tooltip: `${shortDateLabel(d)}: rest` }
    }
    if (p.beta == null) {
      return {
        key: d,
        beta: null,
        tooltip: `${shortDateLabel(d)}: workout · no RPE β · ${p.set_count} sets`,
      }
    }
    return {
      key: d,
      beta: p.beta,
      tooltip: `${shortDateLabel(d)}: β ${
        p.beta >= 0 ? '+' : ''
      }${p.beta.toFixed(2)} · ${p.exercise_count} ex · ${p.set_count} sets`,
    }
  })

  const tooltip = points.map((p) => p.tooltip).join('\n')
  const lastWithBeta = [...points].reverse().find((p) => p.beta != null)
  const labelText = lastWithBeta?.beta != null
    ? `β ${lastWithBeta.beta >= 0 ? '+' : ''}${lastWithBeta.beta.toFixed(2)}`
    : 'β —'

  return (
    <div className="text-[11px] text-gray-500 mb-2" title={tooltip}>
      <div className="mb-1 flex items-baseline justify-between">
        <span className="opacity-80">
          Daily β <span className="opacity-60">(per-exercise mean)</span>
        </span>
        <span className="opacity-90 font-medium">{labelText}</span>
      </div>
      <BetaSparkline points={points} large />
    </div>
  )
}

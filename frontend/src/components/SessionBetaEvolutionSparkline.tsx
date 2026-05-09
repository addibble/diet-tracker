import { useEffect, useState } from 'react'

import {
  getSessionBetaEvolution,
  type SessionBetaEvolution,
  type SessionBetaEvolutionPoint,
} from '../api'
import { BetaSparkline, type BetaSparklinePoint } from './BetaSparkline'

export interface SessionBetaEvolutionSparklineProps {
  sessionId: number
  /** Bumping this string forces a refetch (e.g. after a set is logged). */
  refreshKey?: string | number
  exerciseName?: string
  /** Stacked, full-width layout for the dashboard expanded view. Renders
   *  the label on a line above and the sparkline as a tall, container-wide
   *  SVG below. Defaults to the compact inline layout. */
  large?: boolean
}

/** In-session β evolution: one node per exercise that has any logged set
 *  today (in completion order by max set_order). Each node's β is fit
 *  using only that exercise's RPE-eligible sets vs that exercise's
 *  regularized prior fresh-curve — no cross-exercise contamination. */
export function SessionBetaEvolutionSparkline({
  sessionId, refreshKey, exerciseName, large = false,
}: SessionBetaEvolutionSparklineProps) {
  const [data, setData] = useState<SessionBetaEvolution | null>(null)

  useEffect(() => {
    let cancelled = false
    getSessionBetaEvolution(sessionId)
      .then((d) => { if (!cancelled) setData(d) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [sessionId, refreshKey])

  const pts: SessionBetaEvolutionPoint[] = data?.points ?? []
  const lastWithBeta = [...pts].reverse().find((p) => p.beta != null)
  const labelText = lastWithBeta?.beta != null
    ? `β ${lastWithBeta.beta >= 0 ? '+' : ''}${lastWithBeta.beta.toFixed(2)}`
    : 'β —'
  const namePrefix = exerciseName ?? 'Session'

  if (pts.length === 0) {
    return (
      <div className="text-[11px] text-gray-500 mt-1">
        <span className="mr-1 opacity-80">{namePrefix} β:</span>
        <span className="opacity-60">—</span>
      </div>
    )
  }

  const sparkPoints: BetaSparklinePoint[] = pts.map((p, i) => ({
    key: `${p.exercise_id}-${i}`,
    beta: p.beta,
    tooltip: `${p.exercise_name}: β ${
      p.beta == null ? '—' : p.beta.toFixed(2)
    } (${p.set_count} sets)`,
  }))
  const tooltip = sparkPoints.map((p) => p.tooltip).join('\n')

  if (large) {
    return (
      <div className="text-[11px] text-gray-500 mt-1" title={tooltip}>
        <div className="mb-1">
          <span className="opacity-80">{namePrefix} β:</span>{' '}
          <span className="opacity-90 font-medium">{labelText}</span>
        </div>
        <BetaSparkline points={sparkPoints} large />
      </div>
    )
  }

  return (
    <div className="text-[11px] text-gray-500 mt-1 flex items-center gap-2"
         title={tooltip}>
      <span className="opacity-80">{namePrefix} β:</span>
      <BetaSparkline points={sparkPoints} />
      <span className="opacity-70">{labelText}</span>
    </div>
  )
}


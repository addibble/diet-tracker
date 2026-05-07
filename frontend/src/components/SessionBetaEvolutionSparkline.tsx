import { useEffect, useState } from 'react'

import {
  getSessionBetaEvolution,
  type SessionBetaEvolution,
  type SessionBetaEvolutionPoint,
} from '../api'

export interface SessionBetaEvolutionSparklineProps {
  sessionId: number
  /** Bumping this string forces a refetch (e.g. after a set is logged). */
  refreshKey?: string | number
  exerciseName?: string
  /** Stacked, full-width layout for the dashboard expanded view. Renders
   *  the label on a line above and the sparkline as a tall, container-wide
   *  SVG below. Defaults to the compact inline layout used in-session. */
  large?: boolean
}

/** In-session β evolution: one node per completed exercise (in completion
 *  order), showing how the day's readiness signal evolved. Background is
 *  split into a light-green top half (β > 0) and a light-red bottom half
 *  (β < 0) with a dashed 0-line down the middle. Nodes with insufficient
 *  data are rendered as hollow circles and skipped from the path. */
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

  // Last numeric β for the inline label, even when only one node exists.
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

  // viewBox stays in fixed coordinates; outer width is fixed for the
  // inline variant (compact, sits next to a label), and 100% for the
  // large variant so the SVG stretches to match the curve pane below.
  const W = 140
  const H = large ? 84 : 28
  const PAD = large ? 6 : 3
  // Symmetric range so 0-line sits at the visual midpoint.
  const betas = pts.map((p) => p.beta).filter((b): b is number => b != null)
  const maxAbs = Math.max(0.1, ...betas.map((b) => Math.abs(b)))
  const minB = -maxAbs
  const maxB = maxAbs
  const rangeB = maxB - minB
  const xStep = pts.length > 1 ? (W - 2 * PAD) / (pts.length - 1) : 0
  const y = (b: number) => PAD + (1 - (b - minB) / rangeB) * (H - 2 * PAD)
  const yZero = y(0)
  // Build the line path, breaking at null β values.
  let path = ''
  let penDown = false
  pts.forEach((p, i) => {
    if (p.beta == null) { penDown = false; return }
    const cx = PAD + i * xStep
    const cy = y(p.beta)
    path += `${penDown ? 'L' : 'M'}${cx.toFixed(2)},${cy.toFixed(2)} `
    penDown = true
  })
  const tooltip = pts.map((p) =>
    `${p.exercise_name}: β ${p.beta == null ? '—' : p.beta.toFixed(2)} (${p.set_count} sets)`
  ).join('\n')
  const lastDotR = large ? 4 : 2.75
  const dotR = large ? 2.5 : 1.75
  const svg = (
    <svg
      width={large ? '100%' : W}
      height={large ? undefined : H}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className={large ? 'block w-full' : 'overflow-visible'}
      style={large ? { height: `${H}px` } : undefined}
    >
      <rect x={PAD} y={PAD} width={W - 2 * PAD} height={yZero - PAD}
            fill="#dcfce7" />
      <rect x={PAD} y={yZero} width={W - 2 * PAD} height={H - PAD - yZero}
            fill="#fee2e2" />
      <line x1={PAD} y1={yZero} x2={W - PAD} y2={yZero}
            stroke="currentColor" strokeOpacity="0.35" strokeDasharray="2,2" />
      <path d={path} fill="none" stroke="currentColor"
            strokeWidth={large ? 1.75 : 1.5} strokeOpacity="0.7"
            vectorEffect="non-scaling-stroke" />
      {pts.map((p, i) => {
        const cx = PAD + i * xStep
        const isLast = i === pts.length - 1
        if (p.beta == null) {
          return (
            <circle key={`${p.exercise_id}-${i}`} cx={cx} cy={yZero}
              r={dotR} fill="white" stroke="currentColor"
              strokeOpacity={0.6} strokeWidth={1}
              vectorEffect="non-scaling-stroke" />
          )
        }
        return (
          <circle key={`${p.exercise_id}-${i}`} cx={cx} cy={y(p.beta)}
            r={isLast ? lastDotR : dotR}
            fill="currentColor"
            fillOpacity={isLast ? 1 : 0.7} />
        )
      })}
    </svg>
  )

  if (large) {
    return (
      <div className="text-[11px] text-gray-500 mt-1" title={tooltip}>
        <div className="mb-1">
          <span className="opacity-80">{namePrefix} β:</span>{' '}
          <span className="opacity-90 font-medium">{labelText}</span>
        </div>
        {svg}
      </div>
    )
  }

  return (
    <div className="text-[11px] text-gray-500 mt-1 flex items-center gap-2"
         title={tooltip}>
      <span className="opacity-80">{namePrefix} β:</span>
      {svg}
      <span className="opacity-70">{labelText}</span>
    </div>
  )
}

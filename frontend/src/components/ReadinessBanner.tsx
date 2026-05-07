import { useEffect, useMemo, useState } from 'react'

import {
  getExerciseReadinessTrend,
  getReadinessTrend,
  type ReadinessTrend,
  type WkSession,
} from '../api'

const READINESS_STYLES: Record<string, { label: string; cls: string }> = {
  strong: { label: 'Strong day', cls: 'bg-green-100 text-green-900 border-green-300' },
  above_baseline: { label: 'Above baseline', cls: 'bg-emerald-50 text-emerald-900 border-emerald-200' },
  baseline: { label: 'Baseline', cls: 'bg-gray-50 text-gray-700 border-gray-200' },
  below_baseline: { label: 'Below baseline', cls: 'bg-amber-50 text-amber-900 border-amber-200' },
  fatigued: { label: 'Fatigued — take it easy', cls: 'bg-rose-100 text-rose-900 border-rose-300' },
}

export interface ReadinessSparklineProps {
  session: WkSession
  /** When provided, the sparkline tracks β recomputed from just this exercise's
   *  RPE-tagged sets across recent sessions (per-exercise trend). */
  exerciseId?: number
  /** Number of days to look back. Defaults to 14. */
  days?: number
}

export function ReadinessSparkline({ session, exerciseId, days = 14 }: ReadinessSparklineProps) {
  const [trend, setTrend] = useState<ReadinessTrend | null>(null)
  // Fetch trend once per mount / session+exercise change. Today's point is
  // patched from `session.readiness_beta` for the per-session variant so the
  // chart stays live without re-fetching the network on every set edit.
  useEffect(() => {
    let cancelled = false
    const p = exerciseId == null
      ? getReadinessTrend(days)
      : getExerciseReadinessTrend(exerciseId, days)
    p.then((t) => {
      if (!cancelled) setTrend(t)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [session.id, exerciseId, days])

  // For the per-session sparkline, splice today's β into the trend point;
  // for the per-exercise sparkline, the backend already returns the latest
  // recomputed value, so no patch is needed.
  const merged = useMemo(() => {
    if (!trend) return null
    if (exerciseId != null) return trend
    const sessDate = session.date
    return {
      ...trend,
      points: trend.points.map((p) =>
        p.session_id === session.id || p.date === sessDate
          ? { ...p,
              readiness_beta: session.readiness_beta ?? p.readiness_beta,
              readiness_clamped: session.readiness_clamped ?? p.readiness_clamped,
            }
          : p,
      ),
    }
  }, [trend, exerciseId, session.id, session.date, session.readiness_beta, session.readiness_clamped])

  const pts = merged?.points.filter((p) => p.readiness_beta != null) ?? []
  if (pts.length < 2) return null

  const W = 140
  const H = 28
  const PAD = 3
  const betas = pts.map((p) => p.readiness_beta as number)
  const minB = Math.min(-0.1, ...betas)
  const maxB = Math.max(0.1, ...betas)
  const rangeB = maxB - minB || 1
  const xStep = (W - 2 * PAD) / Math.max(1, pts.length - 1)
  const y = (b: number) => PAD + (1 - (b - minB) / rangeB) * (H - 2 * PAD)
  const yZero = y(0)
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${PAD + i * xStep},${y(p.readiness_beta as number)}`).join(' ')
  // Only call out "today" if the most recent point's date matches the
  // session date (otherwise it's a stale prior session and the label
  // would mislead the user).
  const last = pts[pts.length - 1]
  const lastIsToday = last.date === session.date
  const title = exerciseId == null
    ? `${days}-day readiness β trend`
    : `${days}-day per-exercise β trend`
  return (
    <div className="flex items-center gap-2 mt-1" title={title}>
      <svg width={W} height={H} className="overflow-visible">
        <line x1={PAD} y1={yZero} x2={W - PAD} y2={yZero}
          stroke="currentColor" strokeOpacity="0.25" strokeDasharray="2,2" />
        <path d={path} fill="none" stroke="currentColor" strokeWidth={1.5} strokeOpacity="0.7" />
        {pts.map((p, i) => {
          const cx = PAD + i * xStep
          const cy = y(p.readiness_beta as number)
          const isLast = i === pts.length - 1
          return (
            <circle key={p.session_id} cx={cx} cy={cy}
              r={isLast ? 2.5 : 1.5}
              fill="currentColor"
              fillOpacity={isLast ? 1 : 0.6} />
          )
        })}
      </svg>
      <span className="text-[10px] opacity-70">
        {lastIsToday ? 'today' : 'last'} β {(last.readiness_beta as number) >= 0 ? '+' : ''}
        {(last.readiness_beta as number).toFixed(2)}
      </span>
    </div>
  )
}

export interface ReadinessBannerProps {
  session: WkSession
  /** Optional title rendered alongside the readiness label (e.g. "Today"). */
  title?: string
  /** When set, also render a per-exercise β sparkline below the session
   *  sparkline (used inside an active exercise's card). */
  exerciseId?: number
  exerciseName?: string
}

export function ReadinessBanner({ session, title, exerciseId, exerciseName }: ReadinessBannerProps) {
  const beta = session.readiness_beta
  const label = session.readiness_label
  const pct = session.readiness_pct
  const clamped = session.readiness_clamped
  if (beta == null || label == null) return null
  const style = READINESS_STYLES[label] ?? READINESS_STYLES.baseline
  const sign = pct != null && pct >= 0 ? '+' : ''
  const pctStr = pct != null ? `${sign}${pct.toFixed(1)}%` : ''
  return (
    <div
      className={`rounded border px-2 py-1 text-xs ${style.cls}`}
      title={
        `β = ${beta.toFixed(3)} (multiplicative readiness from today's RPE-tagged sets)` +
        (clamped ? '\nLabel clamped to ±0.35 — outside empirical support range.' : '')
      }
    >
      <div className="flex items-center justify-between">
        <span className="font-medium">
          {title ? `${title}: ` : 'Readiness: '}{style.label}
          {clamped && (
            <span className="ml-1 inline-flex items-center rounded border border-current/40 px-1 py-0 text-[9px] uppercase tracking-wide opacity-80">
              clamped
            </span>
          )}
        </span>
        <span className="font-mono">
          β {beta >= 0 ? '+' : ''}{beta.toFixed(2)} {pctStr && <>({pctStr})</>}
        </span>
      </div>
      <ReadinessSparkline session={session} />
      {exerciseId != null && (
        <div className="mt-1 pt-1 border-t border-current/15">
          <div className="text-[10px] opacity-70 mb-0.5">
            {exerciseName ?? 'this exercise'} trend
          </div>
          <ReadinessSparkline session={session} exerciseId={exerciseId} />
        </div>
      )}
    </div>
  )
}

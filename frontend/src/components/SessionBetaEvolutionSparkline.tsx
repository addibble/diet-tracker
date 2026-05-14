import { useEffect, useMemo, useState } from 'react'

import {
  getSessionBetaEvolution,
  type SessionBetaEvolution,
  type SessionBetaGroup,
  type SessionBetaPoint,
} from '../api'
import { BetaSparkline, type BetaSparklinePoint } from './BetaSparkline'

export interface SessionBetaEvolutionSparklineProps {
  sessionId: number
  /** Bumping this string forces a refetch (e.g. after a set is logged). */
  refreshKey?: string | number
  /** Reserved for backwards-compatibility with the older single-curve
   *  variant. Currently unused — the per-group view labels groups directly. */
  exerciseName?: string
  /** Stacked, full-width layout for the dashboard expanded view. Renders
   *  one row per training group below a header. Defaults to the compact
   *  inline layout. */
  large?: boolean
}

interface SelectedRef {
  group: string
  index: number
}

function formatBeta(beta: number | null): string {
  if (beta == null) return '—'
  const sign = beta >= 0 ? '+' : ''
  return `${sign}${beta.toFixed(2)}`
}

function formatWeight(w: number | null): string {
  if (w == null) return ''
  // Integer-ish weights without trailing .0
  const rounded = Math.round(w * 10) / 10
  return Number.isInteger(rounded) ? `${rounded}` : rounded.toString()
}

function groupToPoints(group: SessionBetaGroup): BetaSparklinePoint[] {
  return group.points.map((p, i) => ({
    key: `${group.group}-${p.set_id ?? `${p.exercise_id}-${p.set_order}`}-${i}`,
    beta: p.beta,
    tooltip: `${p.exercise_name} set ${p.set_index}: β ${formatBeta(p.beta)}`,
  }))
}

function PointDetail({ point }: { point: SessionBetaPoint }) {
  const reps = Number.isInteger(point.reps_done)
    ? point.reps_done
    : Math.round(point.reps_done * 10) / 10
  const weightStr = formatWeight(point.weight)
  return (
    <div className="mt-1 text-[11px] text-gray-700">
      <span className="font-medium">{point.exercise_name}</span>
      {' · '}set {point.set_index}
      {' · '}
      <span className={
        point.beta == null
          ? 'text-gray-500'
          : point.beta >= 0
            ? 'text-emerald-700'
            : 'text-red-700'
      }>
        β {formatBeta(point.beta)}
      </span>
      {' · '}
      {reps} reps{weightStr ? ` @ ${weightStr}` : ''}
    </div>
  )
}

/** In-session β: one sparkline per active exercise group, with one dot
 *  per RPE-eligible set. Each dot is clickable/tappable to reveal the
 *  underlying exercise, set index, β, reps and weight. */
export function SessionBetaEvolutionSparkline({
  sessionId, refreshKey, large = false,
}: SessionBetaEvolutionSparklineProps) {
  const [data, setData] = useState<SessionBetaEvolution | null>(null)
  const [selected, setSelected] = useState<SelectedRef | null>(null)

  useEffect(() => {
    let cancelled = false
    getSessionBetaEvolution(sessionId)
      .then((d) => { if (!cancelled) setData(d) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [sessionId, refreshKey])

  const groups = useMemo(() => data?.groups ?? [], [data])
  const sparkByGroup = useMemo(
    () => Object.fromEntries(
      groups.map((g) => [g.group, groupToPoints(g)] as const),
    ),
    [groups],
  )

  // Resolve the selection during render so we don't need an effect to
  // reconcile stale clicks after a refresh.
  const effectiveSelected = (() => {
    if (!selected) return null
    const g = groups.find((x) => x.group === selected.group)
    if (!g || selected.index >= g.points.length) return null
    return selected
  })()

  if (groups.length === 0) {
    return (
      <div className="text-[11px] text-gray-500 mt-1">
        <span className="opacity-80">Session β:</span>{' '}
        <span className="opacity-60">—</span>
      </div>
    )
  }

  const handleClick = (group: string, index: number) => {
    setSelected((prev) =>
      prev && prev.group === group && prev.index === index
        ? null
        : { group, index },
    )
  }

  const renderRow = (group: SessionBetaGroup) => {
    const sparkPoints = sparkByGroup[group.group] ?? []
    const selectedIndex = effectiveSelected?.group === group.group
      ? effectiveSelected.index
      : null
    const selectedPoint = selectedIndex != null
      ? group.points[selectedIndex] ?? null
      : null
    return (
      <div key={group.group} className="text-gray-600">
        {large ? (
          <>
            <div className="mb-1 flex items-baseline justify-between text-[11px]">
              <span className="font-medium opacity-90">{group.group}</span>
              <span className="opacity-60">{group.points.length} set
                {group.points.length === 1 ? '' : 's'}</span>
            </div>
            <BetaSparkline
              points={sparkPoints}
              large
              onPointClick={(i) => handleClick(group.group, i)}
              selectedIndex={selectedIndex}
            />
            {selectedPoint && <PointDetail point={selectedPoint} />}
          </>
        ) : (
          <>
            <div className="flex items-center gap-2 text-[11px]">
              <span className="opacity-80 min-w-[3.5rem]">{group.group}</span>
              <BetaSparkline
                points={sparkPoints}
                onPointClick={(i) => handleClick(group.group, i)}
                selectedIndex={selectedIndex}
              />
            </div>
            {selectedPoint && <PointDetail point={selectedPoint} />}
          </>
        )}
      </div>
    )
  }

  return (
    <div className={large ? 'space-y-3 mt-1' : 'space-y-1 mt-1'}>
      {groups.map(renderRow)}
    </div>
  )
}

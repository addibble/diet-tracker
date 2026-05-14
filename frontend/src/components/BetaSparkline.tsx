import type React from 'react'

/** Generic β sparkline shared by the in-session per-exercise variant and
 *  the dashboard daily strip. Renders a horizontal sparkline with a
 *  light-green top half (β > 0) and a light-red bottom half (β < 0),
 *  separated by a dashed 0-line. The β-range is symmetric around 0 so
 *  the 0-line always sits at the visual midpoint. The path breaks at
 *  null β values; nulls are rendered either as hollow circles at y=0
 *  (when `showNullAsHollow`) or skipped entirely. */
export interface BetaSparklinePoint {
  key: string
  beta: number | null
  tooltip: string
}

export interface BetaSparklineProps {
  points: BetaSparklinePoint[]
  /** When true, β=null nodes render as a hollow circle at y=0. When
   *  false, null nodes are omitted (creates a gap). Defaults to true. */
  showNullAsHollow?: boolean
  /** When true, render the full-width tall variant used inside cards.
   *  When false, render the compact inline variant used next to a label. */
  large?: boolean
  /** Optional click handler — fires with the index of the clicked point.
   *  When provided, dots become interactive (pointer cursor + larger hit
   *  area on touch). */
  onPointClick?: (index: number) => void
  /** Highlight this index as the selected dot (drawn with a ring). */
  selectedIndex?: number | null
}

export function BetaSparkline({
  points, showNullAsHollow = true, large = false,
  onPointClick, selectedIndex = null,
}: BetaSparklineProps) {
  if (points.length === 0) return null

  const W = 140
  const H = large ? 84 : 28
  const PAD = large ? 6 : 3
  const lastDotR = large ? 4 : 2.75
  const dotR = large ? 2.5 : 1.75

  const betas = points.map((p) => p.beta).filter((b): b is number => b != null)
  const maxAbs = Math.max(0.1, ...betas.map((b) => Math.abs(b)))
  const minB = -maxAbs
  const maxB = maxAbs
  const rangeB = maxB - minB
  const xStep = points.length > 1 ? (W - 2 * PAD) / (points.length - 1) : 0
  const y = (b: number) =>
    PAD + (1 - (b - minB) / rangeB) * (H - 2 * PAD)
  const yZero = y(0)

  let path = ''
  let penDown = false
  points.forEach((p, i) => {
    if (p.beta == null) { penDown = false; return }
    const cx = PAD + i * xStep
    const cy = y(p.beta)
    path += `${penDown ? 'L' : 'M'}${cx.toFixed(2)},${cy.toFixed(2)} `
    penDown = true
  })
  const tooltip = points.map((p) => p.tooltip).join('\n')

  return (
    <svg
      width={large ? '100%' : W}
      height={large ? undefined : H}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className={large ? 'block w-full' : 'overflow-visible'}
      style={large ? { height: `${H}px` } : undefined}
    >
      <title>{tooltip}</title>
      <rect x={PAD} y={PAD} width={W - 2 * PAD} height={yZero - PAD}
            fill="#dcfce7" />
      <rect x={PAD} y={yZero} width={W - 2 * PAD} height={H - PAD - yZero}
            fill="#fee2e2" />
      <line x1={PAD} y1={yZero} x2={W - PAD} y2={yZero}
            stroke="currentColor" strokeOpacity="0.35" strokeDasharray="2,2" />
      <path d={path} fill="none" stroke="currentColor"
            strokeWidth={large ? 1.75 : 1.5} strokeOpacity="0.7"
            vectorEffect="non-scaling-stroke" />
      {points.map((p, i) => {
        const cx = PAD + i * xStep
        const isLast = i === points.length - 1
        const isSelected = selectedIndex === i
        const clickable = onPointClick != null
        const handleClick = clickable
          ? (e: React.MouseEvent) => { e.stopPropagation(); onPointClick(i) }
          : undefined
        const cursor = clickable ? 'pointer' : undefined
        if (p.beta == null) {
          if (!showNullAsHollow) return null
          return (
            <g key={p.key} style={cursor ? { cursor } : undefined}>
              <circle cx={cx} cy={yZero}
                r={dotR} fill="white" stroke="currentColor"
                strokeOpacity={0.6} strokeWidth={1}
                vectorEffect="non-scaling-stroke" />
              {clickable && (
                <circle cx={cx} cy={yZero} r={Math.max(8, dotR * 3)}
                  fill="transparent" onClick={handleClick} />
              )}
              {isSelected && (
                <circle cx={cx} cy={yZero} r={dotR + 2.5}
                  fill="none" stroke="currentColor" strokeOpacity={0.9}
                  strokeWidth={1.25}
                  vectorEffect="non-scaling-stroke" />
              )}
            </g>
          )
        }
        const cy = y(p.beta)
        const r = isSelected ? Math.max(lastDotR, dotR + 1.5) : (isLast ? lastDotR : dotR)
        return (
          <g key={p.key} style={cursor ? { cursor } : undefined}>
            <circle cx={cx} cy={cy} r={r}
              fill="currentColor"
              fillOpacity={isSelected ? 1 : (isLast ? 1 : 0.7)} />
            {isSelected && (
              <circle cx={cx} cy={cy} r={r + 2.5}
                fill="none" stroke="currentColor" strokeOpacity={0.9}
                strokeWidth={1.25}
                vectorEffect="non-scaling-stroke" />
            )}
            {clickable && (
              <circle cx={cx} cy={cy} r={Math.max(8, dotR * 3)}
                fill="transparent" onClick={handleClick} />
            )}
          </g>
        )
      })}
    </svg>
  )
}

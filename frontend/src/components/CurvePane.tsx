import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import {
  predictReps,
  snapWeight,
  weightStep,
  type CurveFit,
} from '../lib/weight_grid'

export interface CurveObservation {
  weight: number
  reps: number
  rir?: number
  age_days: number
}

export type CurvePaneMode = 'pre' | 'logging' | 'completed'

export type ConfirmedRir = 0 | 1 | 2 | 3 | 5

export interface CompletedSet {
  weight: number
  reps: number
  rir: number
}

interface Props {
  mode: CurvePaneMode
  curve: CurveFit | null
  priorCurve?: CurveFit | null
  bootstrapTargetReps?: number  // used when curve == null
  observations: CurveObservation[]
  sparkWeight: number
  sparkReps: number
  schemeRir: number
  schemeSetNumber: number
  onSparkChange: (weight: number, reps: number) => void
  onGo: () => void
  onConfirmRir: (rir: ConfirmedRir) => void
  submitting?: boolean
  completedSets?: CompletedSet[]
  // Fatigue curves: β_s (1-based, β[0] = 0 for set 1). When provided, a
  // set-index-colored curve is drawn at each β offset so the athlete can
  // see where sets 1..N are expected to land relative to today's fit. The
  // dot for each set spark uses the same color as its curve.
  fatigueBetaPerSet?: readonly number[]
  fatigueMaxSetIndex?: number
  fatigueBetaSource?: 'learned' | 'fallback'
}

// Cool-palette colors for today's sets, in set order. Chosen to avoid
// red/orange/yellow (which clashed with historical curve colors) and to
// progress from fresh → deeply fatigued. SET_COLORS[0] matches the
// emerald "today" fit curve so set 1 reads as "fresh".
const SET_COLORS = [
  '#10b981', // emerald — set 1 (fresh)
  '#0ea5e9', // sky     — set 2
  '#6366f1', // indigo  — set 3
  '#a855f7', // violet  — set 4
  '#ec4899', // pink    — set 5
  '#64748b', // slate   — set 6+
]

const VB_W = 320
const VB_H = 200
const PAD_L = 32
const PAD_R = 14
const PAD_T = 14
const PAD_B = 28
const PLOT_W = VB_W - PAD_L - PAD_R
const PLOT_H = VB_H - PAD_T - PAD_B

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

function computeDomain(
  curve: CurveFit | null,
  sparkWeight: number,
  observations: CurveObservation[],
  completedSets?: CompletedSet[],
): { xMin: number; xMax: number; yMax: number } {
  // "Primary" points = today's sets (completed) + the spark (if active in
  // a pre/logging flow). Historical observations are secondary — included
  // only if doing so doesn't push today's spread below 50% of the viewport.
  const todayPts: { w: number; r: number }[] = (completedSets ?? [])
    .filter(s => s.weight > 0 && s.reps > 0)
    .map(s => ({ w: s.weight, r: s.reps }))
  if (sparkWeight > 0) {
    todayPts.push({
      w: sparkWeight,
      r: curve ? predictReps(sparkWeight, curve) : 15,
    })
  }
  const histPts = observations
    .filter(o => o.weight > 0 && o.reps > 0)
    .map(o => ({ w: o.weight, r: o.reps }))

  // X domain seeded from today's points.
  let xLo: number
  let xHi: number
  let center: number
  if (todayPts.length > 0) {
    const ws = todayPts.map(p => p.w)
    const pLo = Math.min(...ws)
    const pHi = Math.max(...ws)
    center = ws.reduce((a, b) => a + b, 0) / ws.length
    const pad = Math.max(1, (pHi - pLo) * 0.25, pHi * 0.06)
    xLo = pLo - pad
    xHi = pHi + pad
  } else if (histPts.length > 0) {
    const ws = histPts.map(p => p.w)
    const pLo = Math.min(...ws)
    const pHi = Math.max(...ws)
    center = ws.reduce((a, b) => a + b, 0) / ws.length
    const pad = Math.max(1, (pHi - pLo) * 0.15, pHi * 0.05)
    xLo = pLo - pad
    xHi = pHi + pad
  } else {
    center = sparkWeight > 0 ? sparkWeight : 100
    xLo = center * 0.7
    xHi = center * 1.3
  }

  // Expand to include historical points, preferring those nearest to today's
  // center; drop outliers that would shrink today's span below 50% of the
  // domain. When there's no "today" data we always include.
  const todaySpanX = todayPts.length >= 2
    ? Math.max(...todayPts.map(p => p.w)) - Math.min(...todayPts.map(p => p.w))
    : 0
  const sortedHistX = [...histPts].sort(
    (a, b) => Math.abs(a.w - center) - Math.abs(b.w - center),
  )
  for (const p of sortedHistX) {
    const newLo = Math.min(xLo, p.w * 0.95)
    const newHi = Math.max(xHi, p.w * 1.05)
    if (todayPts.length > 0 && todaySpanX > 0) {
      const newRange = newHi - newLo
      if (todaySpanX < newRange * 0.5) continue
    }
    xLo = newLo
    xHi = newHi
  }
  let xMin = Math.max(0, xLo)
  let xMax = xHi
  if (xMax - xMin < weightStep(xMax) * 4) {
    xMax = xMin + weightStep(xMax) * 4 + 0.01
  }
  xMin = Math.floor(xMin)
  xMax = Math.ceil(xMax)

  // Y domain: anchor on today's max reps + a little headroom, grow for
  // historical points only while today still occupies ≥50% of the viewport.
  let yHi: number
  if (todayPts.length > 0) {
    yHi = Math.max(...todayPts.map(p => p.r)) * 1.2
  } else if (histPts.length > 0) {
    yHi = Math.max(...histPts.map(p => p.r)) * 1.15
  } else {
    yHi = 20
  }
  const todayMaxY = todayPts.length > 0 ? Math.max(...todayPts.map(p => p.r)) : 0
  const sortedHistY = [...histPts].sort((a, b) => a.r - b.r)
  for (const p of sortedHistY) {
    const newHi = Math.max(yHi, p.r * 1.15)
    if (todayPts.length > 0 && todayMaxY > 0) {
      // Y starts at 0, so "today's spread ≥ 50% of viewport" ≈ todayMaxY ≥ 0.5 * newHi.
      if (todayMaxY < newHi * 0.5) continue
    }
    yHi = newHi
  }
  const yMax = Math.max(8, Math.ceil(yHi))
  return { xMin, xMax, yMax }
}

function curvePath(
  curve: CurveFit,
  xMin: number,
  xMax: number,
  xToPxRaw: (x: number) => number,
  yToPxRaw: (y: number) => number,
): string {
  const n = 120
  const pts: string[] = []
  for (let i = 0; i <= n; i++) {
    const w = xMin + ((xMax - xMin) * i) / n
    const r = predictReps(w, curve)
    if (!Number.isFinite(r) || r <= 0) continue
    pts.push(`${xToPxRaw(w).toFixed(1)},${yToPxRaw(r).toFixed(1)}`)
  }
  if (pts.length < 2) return ''
  return `M ${pts.join(' L ')}`
}

function shiftedCurvePath(
  curve: CurveFit,
  shiftReps: number,
  xMin: number,
  xMax: number,
  xToPxRaw: (x: number) => number,
  yToPxRaw: (y: number) => number,
): string {
  const n = 120
  const pts: string[] = []
  for (let i = 0; i <= n; i++) {
    const w = xMin + ((xMax - xMin) * i) / n
    const rFresh = predictReps(w, curve)
    if (!Number.isFinite(rFresh) || rFresh <= 0) continue
    const r = Math.max(0, rFresh + shiftReps)
    pts.push(`${xToPxRaw(w).toFixed(1)},${yToPxRaw(r).toFixed(1)}`)
  }
  if (pts.length < 2) return ''
  return `M ${pts.join(' L ')}`
}

export default function CurvePane({
  mode,
  curve,
  priorCurve = null,
  bootstrapTargetReps = 15,
  observations,
  sparkWeight,
  sparkReps,
  schemeRir,
  schemeSetNumber,
  onSparkChange,
  onGo,
  onConfirmRir,
  submitting = false,
  completedSets,
  fatigueBetaPerSet,
  fatigueMaxSetIndex = 3,
  fatigueBetaSource,
}: Props) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const rafRef = useRef<number | null>(null)
  const [dragging, setDragging] = useState(false)
  // Drag reference point: pointer coords + spark values at pointerdown.
  // Enables half-sensitivity delta dragging (rather than absolute tap-tracking).
  const dragStart = useRef<{
    cx: number
    cy: number
    w: number
    r: number
  } | null>(null)

  // Sticky domain: seeded from curve + observations + initial spark, and
  // only expanded when the spark approaches the edges. This keeps the graph
  // from sliding around as the user drags.
  const [domain, setDomain] = useState(() =>
    computeDomain(curve, sparkWeight, observations, completedSets),
  )

  // Reset when the underlying fit or history changes (new prescription / refit).
  useEffect(() => {
    setDomain(computeDomain(curve, sparkWeight, observations, completedSets))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curve, observations, completedSets])

  // Expand the window only if the spark is near the very edge.
  useEffect(() => {
    if (sparkWeight <= 0) return
    const span = domain.xMax - domain.xMin
    if (span <= 0) return
    const edge = span * 0.05
    if (sparkWeight > domain.xMax - edge) {
      setDomain(d => ({
        ...d,
        xMax: Math.ceil(sparkWeight + span * 0.15),
      }))
    } else if (sparkWeight < domain.xMin + edge) {
      setDomain(d => ({
        ...d,
        xMin: Math.max(0, Math.floor(sparkWeight - span * 0.15)),
      }))
    }
  }, [sparkWeight, domain.xMin, domain.xMax])

  const { xMin, xMax, yMax } = domain

  const xToPx = useCallback(
    (x: number) => PAD_L + (clamp(x, xMin, xMax) - xMin) / (xMax - xMin) * PLOT_W,
    [xMin, xMax],
  )
  const yToPx = useCallback(
    (y: number) => PAD_T + (1 - clamp(y, 0, yMax) / yMax) * PLOT_H,
    [yMax],
  )
  // Unclamped versions used to render curves: the path is clipped by the
  // plot rect via <clipPath>, so asymptotes near x=0 / x=M stay hidden
  // instead of being pinned to the chart edges.
  const xToPxRaw = useCallback(
    (x: number) => PAD_L + (x - xMin) / (xMax - xMin) * PLOT_W,
    [xMin, xMax],
  )
  const yToPxRaw = useCallback(
    (y: number) => PAD_T + (1 - y / yMax) * PLOT_H,
    [yMax],
  )

  const pxToData = useCallback(
    (clientX: number, clientY: number): { w: number; r: number } => {
      const svg = svgRef.current
      if (!svg) return { w: sparkWeight, r: sparkReps }
      const rect = svg.getBoundingClientRect()
      const px = ((clientX - rect.left) / rect.width) * VB_W
      const py = ((clientY - rect.top) / rect.height) * VB_H
      const wRaw = xMin + ((clamp(px, PAD_L, VB_W - PAD_R) - PAD_L) / PLOT_W) * (xMax - xMin)
      const rRaw = (1 - (clamp(py, PAD_T, VB_H - PAD_B) - PAD_T) / PLOT_H) * yMax
      return { w: wRaw, r: rRaw }
    },
    [sparkWeight, sparkReps, xMin, xMax, yMax],
  )

  // Convert a pixel delta (in client coords) into a data delta, then halve it
  // so the spark moves at half the pointer speed.
  const DRAG_SENSITIVITY = 0.5

  const deltaToData = useCallback(
    (dxPx: number, dyPx: number): { dw: number; dr: number } => {
      const svg = svgRef.current
      if (!svg) return { dw: 0, dr: 0 }
      const rect = svg.getBoundingClientRect()
      const dwPx = (dxPx / rect.width) * VB_W
      const drPx = (dyPx / rect.height) * VB_H
      const dw = (dwPx / PLOT_W) * (xMax - xMin) * DRAG_SENSITIVITY
      const dr = -(drPx / PLOT_H) * yMax * DRAG_SENSITIVITY
      return { dw, dr }
    },
    [xMin, xMax, yMax],
  )

  const applySpark = useCallback(
    (w: number, r: number) => {
      if (mode === 'pre') {
        const snapped = snapWeight(clamp(w, xMin, xMax))
        const newReps = curve ? predictReps(snapped, curve) : bootstrapTargetReps
        onSparkChange(snapped, newReps)
      } else {
        const newReps = Math.max(1, Math.round(clamp(r, 0, yMax)))
        onSparkChange(sparkWeight, newReps)
      }
    },
    [mode, curve, bootstrapTargetReps, sparkWeight, xMin, xMax, yMax, onSparkChange],
  )

  const handlePointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (submitting) return
    e.currentTarget.setPointerCapture(e.pointerId)
    setDragging(true)
    // Tap-to-place: snap the spark to wherever the user tapped, then record
    // that as the drag reference. Subsequent drags use half-sensitivity deltas.
    const { w, r } = pxToData(e.clientX, e.clientY)
    let seedW = sparkWeight
    let seedR = sparkReps
    if (mode === 'pre') {
      seedW = snapWeight(clamp(w, xMin, xMax))
      seedR = curve ? predictReps(seedW, curve) : bootstrapTargetReps
      onSparkChange(seedW, seedR)
    } else {
      seedR = Math.max(1, Math.round(clamp(r, 0, yMax)))
      onSparkChange(sparkWeight, seedR)
      seedW = sparkWeight
    }
    dragStart.current = { cx: e.clientX, cy: e.clientY, w: seedW, r: seedR }
  }

  const handlePointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (!dragging || !dragStart.current) return
    if (rafRef.current != null) return
    const cx = e.clientX
    const cy = e.clientY
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      if (!dragStart.current) return
      const { dw, dr } = deltaToData(
        cx - dragStart.current.cx,
        cy - dragStart.current.cy,
      )
      applySpark(dragStart.current.w + dw, dragStart.current.r + dr)
    })
  }

  const handlePointerUp = (e: ReactPointerEvent<SVGSVGElement>) => {
    setDragging(false)
    dragStart.current = null
    try { e.currentTarget.releasePointerCapture(e.pointerId) } catch { /* ignore */ }
  }

  // X-axis tick labels: ~5 ticks.
  const xTicks = useMemo(() => {
    const ticks: number[] = []
    const n = 4
    for (let i = 0; i <= n; i++) {
      const w = xMin + ((xMax - xMin) * i) / n
      ticks.push(Math.round(w))
    }
    return ticks
  }, [xMin, xMax])

  // Y-axis tick labels: 0, mid, max.
  const yTicks = useMemo(() => {
    const n = 4
    const ticks: number[] = []
    for (let i = 0; i <= n; i++) {
      ticks.push(Math.round((yMax * i) / n))
    }
    return ticks
  }, [yMax])

  const sparkX = xToPx(sparkWeight)
  const sparkY = yToPx(sparkReps)

  const isCompleted = mode === 'completed'
  // In completed mode hide today's gray dots so they don't duplicate the
  // colored sparks rendered from completedSets.
  const visibleObservations = isCompleted
    ? observations.filter(o => o.age_days > 0)
    : observations

  return (
    <div className="relative">
      {/* Go button (pre mode only) */}
      {mode === 'pre' && (
        <button
          type="button"
          onClick={onGo}
          disabled={submitting || sparkWeight <= 0}
          className="absolute right-2 top-2 z-10 rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-emerald-700 disabled:opacity-50"
          style={{ minHeight: 40 }}
        >
          Go →
        </button>
      )}

      {/* Set + live readout banner */}
      <div className="mb-1 flex items-end justify-between gap-2 px-1">
        <span className="text-[11px] font-medium text-gray-500">
          {isCompleted ? (
            <>Exercise complete · {completedSets?.length ?? 0} sets</>
          ) : (
            <>
              Set {schemeSetNumber}
              <span className="ml-1 text-gray-400">· target RIR {schemeRir}</span>
              {mode === 'logging' && (
                <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
                  log reps
                </span>
              )}
            </>
          )}
        </span>
        {!isCompleted && (
          <span
            className={`text-xl font-bold tabular-nums ${
              dragging ? 'text-emerald-600' : 'text-gray-900'
            }`}
          >
            {Math.max(0, Math.round(sparkReps))} reps @{' '}
            {sparkWeight % 1 === 0 ? sparkWeight : sparkWeight.toFixed(1)} lbs
          </span>
        )}
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        preserveAspectRatio="none"
        className="block w-full select-none rounded-lg border border-gray-200 bg-white"
        style={{ aspectRatio: '16 / 10', touchAction: isCompleted ? 'auto' : 'none' }}
        onPointerDown={isCompleted ? undefined : handlePointerDown}
        onPointerMove={isCompleted ? undefined : handlePointerMove}
        onPointerUp={isCompleted ? undefined : handlePointerUp}
        onPointerCancel={isCompleted ? undefined : handlePointerUp}
      >
        <defs>
          <clipPath id="curve-pane-plot-clip">
            <rect x={PAD_L} y={PAD_T} width={PLOT_W} height={PLOT_H} />
          </clipPath>
        </defs>
        {/* Plot background */}
        <rect
          x={PAD_L}
          y={PAD_T}
          width={PLOT_W}
          height={PLOT_H}
          fill="#fafafa"
          stroke="#e5e7eb"
        />

        {/* Grid + Y labels */}
        {yTicks.map(t => (
          <g key={`yt-${t}`}>
            <line
              x1={PAD_L}
              x2={PAD_L + PLOT_W}
              y1={yToPx(t)}
              y2={yToPx(t)}
              stroke="#f3f4f6"
            />
            <text
              x={PAD_L - 4}
              y={yToPx(t) + 3}
              textAnchor="end"
              fontSize="9"
              fill="#9ca3af"
            >
              {t}
            </text>
          </g>
        ))}
        {/* X labels */}
        {xTicks.map((t, i) => (
          <text
            key={`xt-${i}`}
            x={xToPx(t)}
            y={VB_H - 10}
            textAnchor="middle"
            fontSize="9"
            fill="#9ca3af"
          >
            {t}
          </text>
        ))}
        <text
          x={PAD_L + PLOT_W / 2}
          y={VB_H - 1}
          textAnchor="middle"
          fontSize="8"
          fill="#6b7280"
        >
          weight (lb)
        </text>
        <text
          x={6}
          y={PAD_T + PLOT_H / 2}
          textAnchor="middle"
          fontSize="8"
          fill="#6b7280"
          transform={`rotate(-90 6 ${PAD_T + PLOT_H / 2})`}
        >
          reps
        </text>

        {/* Prior curve (black, dotted) — drawn under the green curve */}
        {isCompleted && priorCurve && (
          <path
            d={curvePath(priorCurve, xMin, xMax, xToPxRaw, yToPxRaw)}
            fill="none"
            stroke="#111827"
            strokeWidth={1.25}
            strokeDasharray="1 3"
            strokeLinecap="round"
            opacity={0.7}
            clipPath="url(#curve-pane-plot-clip)"
          />
        )}

        {/* Per-set fatigue curves — one colored line per set index up to
            fatigueMaxSetIndex. Each line is the fresh curve shifted down
            by β_s reps. Colors match the set-spark dots so a dot near its
            set curve means "on pace with historical fatigue", above means
            "outperforming", below means "underperforming". Set 1 (β=0)
            coincides with the fresh fit curve rendered below. */}
        {curve && fatigueBetaPerSet && fatigueBetaPerSet.length > 0 && (() => {
          const lastIdx = Math.min(
            fatigueMaxSetIndex,
            fatigueBetaPerSet.length,
          )
          const opacity = fatigueBetaSource === 'learned' ? 0.75 : 0.45
          return (
            <g>
              {fatigueBetaPerSet.slice(1, lastIdx).map((b, i) => {
                if (!(b < 0)) return null
                const color = SET_COLORS[(i + 1) % SET_COLORS.length]
                return (
                  <path
                    key={`beta-${i + 2}`}
                    d={shiftedCurvePath(
                      curve, b, xMin, xMax, xToPxRaw, yToPxRaw,
                    )}
                    fill="none"
                    stroke={color}
                    strokeWidth={1.25}
                    strokeDasharray="3 3"
                    opacity={opacity}
                    clipPath="url(#curve-pane-plot-clip)"
                  />
                )
              })}
            </g>
          )
        })()}

        {/* Curve or flat target line */}
        {curve ? (
          <path
            d={curvePath(curve, xMin, xMax, xToPxRaw, yToPxRaw)}
            fill="none"
            stroke="#10b981"
            strokeWidth={1.5}
            clipPath="url(#curve-pane-plot-clip)"
          />
        ) : (
          <line
            x1={PAD_L}
            x2={PAD_L + PLOT_W}
            y1={yToPx(bootstrapTargetReps)}
            y2={yToPx(bootstrapTargetReps)}
            stroke="#10b981"
            strokeWidth={1.5}
            strokeDasharray="4 3"
          />
        )}

        {/* Historical observations */}
        {visibleObservations.map((o, i) => {
          if (o.weight <= 0 || o.reps <= 0) return null
          const op = clamp(1 - o.age_days / 30, 0.25, 1)
          return (
            <circle
              key={`obs-${i}`}
              cx={xToPx(o.weight)}
              cy={yToPx(o.reps)}
              r={3}
              fill="#9ca3af"
              opacity={op}
            />
          )
        })}

        {/* Completed-mode sparks for today's sets, with labels */}
        {isCompleted && completedSets?.map((s, i) => {
          const cx = xToPx(s.weight)
          const cy = yToPx(s.reps)
          const color = SET_COLORS[i % SET_COLORS.length]
          // Alternate label above / below to reduce overlap.
          const above = cy > PAD_T + PLOT_H / 2
          const labelY = above ? cy - 12 : cy + 18
          // Nudge label horizontally to stay within plot.
          const labelText = `${s.weight}×${s.reps} RIR${s.rir}`
          const approxWidth = labelText.length * 4.3 + 6
          let tx = cx
          const minX = PAD_L + approxWidth / 2 + 2
          const maxX = PAD_L + PLOT_W - approxWidth / 2 - 2
          if (tx < minX) tx = minX
          if (tx > maxX) tx = maxX
          return (
            <g key={`set-${i}`}>
              <circle cx={cx} cy={cy} r={9} fill={color} fillOpacity={0.18} />
              <circle
                cx={cx} cy={cy} r={5}
                fill={color} stroke="#fff" strokeWidth={1.5}
              />
              <text
                x={cx} y={cy + 2}
                textAnchor="middle" fontSize="7"
                fill="#fff" fontWeight="bold"
              >
                {i + 1}
              </text>
              <rect
                x={tx - approxWidth / 2}
                y={labelY - 7}
                width={approxWidth}
                height={10}
                rx={2}
                fill="white"
                stroke={color}
                strokeWidth={0.75}
              />
              <text
                x={tx} y={labelY + 1}
                textAnchor="middle" fontSize="7"
                fill="#111827" fontWeight="600"
              >
                {labelText}
              </text>
            </g>
          )
        })}

        {/* Interactive spark (pre / logging only) */}
        {!isCompleted && (
          <g pointerEvents="none">
            {/* Vertical/horizontal guide */}
            {mode === 'pre' ? (
              <line
                x1={sparkX}
                x2={sparkX}
                y1={PAD_T}
                y2={PAD_T + PLOT_H}
                stroke="#10b981"
                strokeOpacity="0.25"
                strokeDasharray="2 3"
              />
            ) : (
              <>
                <line
                  x1={sparkX}
                  x2={sparkX}
                  y1={PAD_T}
                  y2={PAD_T + PLOT_H}
                  stroke="#f59e0b"
                  strokeOpacity="0.35"
                  strokeDasharray="2 3"
                />
                <line
                  x1={PAD_L}
                  x2={PAD_L + PLOT_W}
                  y1={sparkY}
                  y2={sparkY}
                  stroke="#f59e0b"
                  strokeOpacity="0.35"
                  strokeDasharray="2 3"
                />
              </>
            )}
            <circle
              cx={sparkX}
              cy={sparkY}
              r={10}
              fill={mode === 'pre' ? '#10b981' : '#f59e0b'}
              fillOpacity="0.2"
            />
            <circle
              cx={sparkX}
              cy={sparkY}
              r={6}
              fill={mode === 'pre' ? '#10b981' : '#f59e0b'}
              stroke="#fff"
              strokeWidth={1.5}
            />
          </g>
        )}
      </svg>

      {/* Spark readout (drag-mode only) */}
      {!isCompleted && (
        <div className="mt-2 flex items-baseline justify-between px-1 text-sm">
          <span className="font-semibold tabular-nums text-gray-900">
            {snapWeight(sparkWeight)} lb × {Math.max(0, Math.round(sparkReps))} reps
          </span>
          <span className="text-[11px] text-gray-500">
            {mode === 'pre' ? 'drag to pick weight' : 'drag to set reps achieved'}
          </span>
        </div>
      )}

      {/* Legend (completed mode) */}
      {isCompleted && (
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[10px] text-gray-500">
          <span className="flex items-center gap-1">
            <span className="inline-block h-[2px] w-4 bg-emerald-500" />
            set 1 (fresh)
          </span>
          {fatigueBetaPerSet && fatigueBetaPerSet.slice(1, Math.min(
            fatigueMaxSetIndex, fatigueBetaPerSet.length,
          )).map((b, i) => b < 0 ? (
            <span key={`legend-set-${i + 2}`} className="flex items-center gap-1">
              <span
                className="inline-block h-[2px] w-4"
                style={{
                  backgroundImage: `repeating-linear-gradient(to right, ${
                    SET_COLORS[(i + 1) % SET_COLORS.length]
                  } 0 3px, transparent 3px 6px)`,
                }}
              />
              {`set ${i + 2}`}
            </span>
          ) : null)}
          {priorCurve && (
            <span className="flex items-center gap-1">
              <span
                className="inline-block h-[2px] w-4"
                style={{
                  backgroundImage: 'repeating-linear-gradient(to right, #111827 0 1px, transparent 1px 4px)',
                }}
              />
              before today
            </span>
          )}
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full bg-gray-400" />
            history
          </span>
        </div>
      )}

      {/* RIR confirm row (logging) */}
      {mode === 'logging' && (
        <div className="mt-2 grid grid-cols-5 gap-2">
          {([0, 1, 2, 3, 5] as ConfirmedRir[]).map(val => (
            <button
              key={val}
              type="button"
              onClick={() => onConfirmRir(val)}
              disabled={submitting}
              className="rounded-lg bg-gray-900 px-2 py-3 text-sm font-semibold text-white transition-colors hover:bg-gray-700 disabled:opacity-40"
              style={{ minHeight: 44 }}
            >
              {val === 5 ? 'More' : val}
            </button>
          ))}
        </div>
      )}
      {mode === 'logging' && (
        <p className="mt-1 text-center text-[10px] text-gray-400">
          RIR = reps in reserve. Pick how many more you could have done.
        </p>
      )}
    </div>
  )
}

// Helper re-export for the caller.

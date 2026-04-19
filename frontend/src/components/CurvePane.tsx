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
}

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
): { xMin: number; xMax: number; yMax: number } {
  const obsWeights = observations.map(o => o.weight).filter(w => w > 0)
  const obsReps = observations.map(o => o.reps).filter(r => r > 0)

  // X: auto ±30% around spark, expanded to cover observations so the user can see history.
  let xMin = sparkWeight > 0 ? sparkWeight * 0.7 : 0
  let xMax = sparkWeight > 0 ? sparkWeight * 1.3 : 100
  if (obsWeights.length > 0) {
    xMin = Math.min(xMin, Math.min(...obsWeights) * 0.95)
    xMax = Math.max(xMax, Math.max(...obsWeights) * 1.05)
  }
  // Cap xMax a bit below M so the curve doesn't asymptote off-chart.
  if (curve && xMax > curve.M * 0.98) xMax = curve.M * 0.98
  xMin = Math.max(0, xMin)
  if (xMax - xMin < weightStep(xMax) * 4) {
    xMax = xMin + weightStep(xMax) * 4 + 0.01
  }
  xMin = Math.floor(xMin)
  xMax = Math.ceil(xMax)

  // Y: cap at 1.15x the largest reps we need to show.
  const maxReps = Math.max(
    sparkWeight > 0
      ? (curve ? predictReps(xMin, curve) : (observations[0]?.reps ?? 20))
      : 20,
    ...obsReps,
  )
  const yMax = Math.max(8, Math.ceil(maxReps * 1.15))
  return { xMin, xMax, yMax }
}

function curvePath(
  curve: CurveFit,
  xMin: number,
  xMax: number,
  xToPx: (x: number) => number,
  yToPx: (y: number) => number,
): string {
  const n = 60
  const pts: string[] = []
  for (let i = 0; i <= n; i++) {
    const w = xMin + ((xMax - xMin) * i) / n
    const r = predictReps(w, curve)
    pts.push(`${xToPx(w).toFixed(1)},${yToPx(r).toFixed(1)}`)
  }
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
    computeDomain(curve, sparkWeight, observations),
  )

  // Reset when the underlying fit or history changes (new prescription / refit).
  useEffect(() => {
    setDomain(computeDomain(curve, sparkWeight, observations))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curve, observations])

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

  // Colors for today's completed sets (in set order). Up to 6 distinct hues.
  const SET_COLORS = [
    '#ef4444', // red
    '#f97316', // orange
    '#eab308', // yellow
    '#22c55e', // green
    '#3b82f6', // blue
    '#a855f7', // purple
  ]

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

        {/* Prior curve (yellow, dashed) — drawn under the green curve */}
        {isCompleted && priorCurve && (
          <path
            d={curvePath(priorCurve, xMin, xMax, xToPx, yToPx)}
            fill="none"
            stroke="#eab308"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            opacity={0.85}
          />
        )}

        {/* Curve or flat target line */}
        {curve ? (
          <path
            d={curvePath(curve, xMin, xMax, xToPx, yToPx)}
            fill="none"
            stroke="#10b981"
            strokeWidth={1.5}
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
            today
          </span>
          {priorCurve && (
            <span className="flex items-center gap-1">
              <span
                className="inline-block h-[2px] w-4"
                style={{
                  backgroundImage: 'repeating-linear-gradient(to right, #eab308 0 4px, transparent 4px 7px)',
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

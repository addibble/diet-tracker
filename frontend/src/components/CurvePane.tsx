import { useCallback, useMemo, useRef, useState } from 'react'
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

export type CurvePaneMode = 'pre' | 'logging'

export type ConfirmedRir = 0 | 1 | 2 | 3 | 5

interface Props {
  mode: CurvePaneMode
  curve: CurveFit | null
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
}: Props) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const rafRef = useRef<number | null>(null)
  const [dragging, setDragging] = useState(false)

  const { xMin, xMax, yMax } = useMemo(
    () => computeDomain(curve, sparkWeight, observations),
    [curve, sparkWeight, observations],
  )

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

  const updateFromPointer = useCallback(
    (clientX: number, clientY: number) => {
      const { w, r } = pxToData(clientX, clientY)
      if (mode === 'pre') {
        const snapped = snapWeight(clamp(w, xMin, xMax))
        const newReps = curve ? predictReps(snapped, curve) : bootstrapTargetReps
        onSparkChange(snapped, newReps)
      } else {
        // logging: only Y moves.
        const newReps = Math.max(1, Math.round(r))
        onSparkChange(sparkWeight, newReps)
      }
    },
    [mode, curve, bootstrapTargetReps, sparkWeight, xMin, xMax, pxToData, onSparkChange],
  )

  const handlePointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (submitting) return
    e.currentTarget.setPointerCapture(e.pointerId)
    setDragging(true)
    updateFromPointer(e.clientX, e.clientY)
  }
  const handlePointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (!dragging) return
    if (rafRef.current != null) return
    const cx = e.clientX
    const cy = e.clientY
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      updateFromPointer(cx, cy)
    })
  }
  const handlePointerUp = (e: ReactPointerEvent<SVGSVGElement>) => {
    setDragging(false)
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

      {/* Set + RIR banner */}
      <div className="mb-1 flex items-center justify-between px-1 text-[11px]">
        <span className="font-medium text-gray-700">
          Set {schemeSetNumber}{' '}
          <span className="text-gray-400">· target RIR {schemeRir}</span>
        </span>
        {mode === 'logging' && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 font-semibold text-amber-800">
            RIR {schemeRir} — log reps
          </span>
        )}
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        preserveAspectRatio="none"
        className="block w-full select-none rounded-lg border border-gray-200 bg-white"
        style={{ aspectRatio: '16 / 10', touchAction: 'none' }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
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
        {observations.map((o, i) => {
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

        {/* Spark hit target (larger for touch) + visible dot */}
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
      </svg>

      {/* Spark readout */}
      <div className="mt-2 flex items-baseline justify-between px-1 text-sm">
        <span className="font-semibold tabular-nums text-gray-900">
          {snapWeight(sparkWeight)} lb × {Math.max(0, Math.round(sparkReps))} reps
        </span>
        <span className="text-[11px] text-gray-500">
          {mode === 'pre' ? 'drag to pick weight' : 'drag to set reps achieved'}
        </span>
      </div>

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

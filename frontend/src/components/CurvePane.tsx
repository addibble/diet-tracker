import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import {
  predictReps,
  snapWeight,
  solveWeight,
  weightStep,
  type CurveFit,
} from '../lib/weight_grid'
import {
  asRepsDone,
  asRtf,
  rtfToRepsDone,
  type EnteredWeightLb,
  type RepsDone,
  type Rir,
} from '../lib/units'
import type { CurveBandsPayload } from '../api/planner'

export interface CurveObservation {
  weight: EnteredWeightLb
  reps: RepsDone
  rir?: Rir
  age_days: number
}

export type CurvePaneMode = 'pre' | 'logging' | 'completed'

export type ConfirmedRir = 0 | 1 | 2 | 3 | 5

export interface CompletedSet {
  weight: EnteredWeightLb
  reps: RepsDone
  rir: Rir
}

interface Props {
  mode: CurvePaneMode
  curve: CurveFit | null
  priorCurve?: CurveFit | null
  bootstrapTargetReps?: RepsDone  // used when curve == null
  observations: CurveObservation[]
  sparkWeight: EnteredWeightLb
  sparkReps: RepsDone
  schemeRir: Rir
  schemeSetNumber: number
  // Acceptable completed-rep window for the current set (from backend's
  // scheme). Used to color the top readout emerald when the predicted
  // actual reps at the chosen weight fall within the window, amber when
  // they don't. Falls back to bootstrapTargetReps ± 3 when absent.
  acceptableRepMin?: RepsDone
  acceptableRepMax?: RepsDone
  onSparkChange: (weight: EnteredWeightLb, reps: RepsDone) => void
  onGo: () => void
  /** Returns from `logging` mode back to `pre` so the user can correct
   *  the chosen weight. Optional; when absent, the back button is hidden. */
  onBack?: () => void
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
  // Optional bootstrap CI bands for the historical fit. When present,
  // a filled polygon (q05↔q95 outer, q25↔q75 inner) is drawn behind the
  // point curve along with a dashed q50 line. The band uses
  // ``exclude_today`` semantics — it represents historical uncertainty
  // of the fresh-data fit; the live point curve is the current best
  // estimate including today's sets. A small caption clarifies this.
  bands?: CurveBandsPayload
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
  targetReps?: number,
  schemeRir?: number,
  fullDomain?: boolean,
): { xMin: number; xMax: number; yMin: number; yMax: number } {
  // "Primary" points = today's sets (completed) + the spark (if active in
  // a pre/logging flow). Historical observations are secondary — included
  // only if doing so doesn't push today's spread below 50% of the viewport.
  const todayPts: { w: number; r: number }[] = (completedSets ?? [])
    .filter(s => s.weight > 0 && s.reps > 0)
    .map(s => ({ w: s.weight, r: s.reps + (s.rir ?? 0) }))
  if (sparkWeight > 0) {
    todayPts.push({
      w: sparkWeight,
      r: curve ? predictReps(sparkWeight, curve) : 15,
    })
  }
  // If we have a curve and a known target (reps + RIR = target rtf), include
  // the weight that actually hits that target so the curve is visible even
  // when the initial prescription happens to be far from target.
  if (curve && targetReps && targetReps > 0) {
    const targetRtf = targetReps + (schemeRir ?? 0)
    const wTarget = solveWeight(targetRtf, curve)
    // wTarget is entered-space; compare to a generous entered-space cap
    // (the curve's max observed entered weight plus some headroom) rather
    // than to curve.M which lives in effective space.
    const entCap = ((curve.max_observed_weight ?? Infinity) as number) * 3 + 50
    if (Number.isFinite(wTarget) && wTarget > 0 && wTarget < entCap) {
      todayPts.push({ w: wTarget, r: targetRtf })
    }
  }
  const histPts = observations
    .filter(o => o.weight > 0 && o.reps > 0)
    .map(o => ({ w: o.weight, r: o.reps + (o.rir ?? 0) }))

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
  let yMax = Math.max(8, Math.ceil(yHi))

  // Full-domain (completed-view zoom-out): anchor both axes at 0 and
  // expand the window so the curve's x- and y-intercepts are fully on
  // screen. x runs 0 → max-weight (rtf=0); y runs 0 → max-reps (curve
  // value at a small weight, capped to avoid the W→0 asymptote).
  if (fullDomain && curve) {
    xMin = 0
    const xIntercept = solveWeight(0, curve) as number
    if (Number.isFinite(xIntercept) && xIntercept > 0) {
      xMax = Math.max(xMax, Math.ceil(xIntercept))
    }
    const probeW = Math.max(xMax / 50, 0.5)
    const yIntercept = predictReps(probeW, curve) as number
    if (Number.isFinite(yIntercept) && yIntercept > 0) {
      yMax = Math.max(yMax, Math.ceil(yIntercept))
    }
  } else if (fullDomain) {
    xMin = 0
  }

  return { xMin, xMax, yMin: 0, yMax }
}

function curvePath(
  curve: CurveFit,
  xMin: number,
  xMax: number,
  xToPxRaw: (x: number) => number,
  yToPxRaw: (y: number) => number,
  clipLo?: number,
  clipHi?: number,
): string {
  const n = 120
  const pts: string[] = []
  for (let i = 0; i <= n; i++) {
    const w = xMin + ((xMax - xMin) * i) / n
    if (clipLo != null && w < clipLo) continue
    if (clipHi != null && w > clipHi) continue
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
  clipLo?: number,
  clipHi?: number,
): string {
  const n = 120
  const pts: string[] = []
  for (let i = 0; i <= n; i++) {
    const w = xMin + ((xMax - xMin) * i) / n
    if (clipLo != null && w < clipLo) continue
    if (clipHi != null && w > clipHi) continue
    const rFresh = predictReps(w, curve)
    if (!Number.isFinite(rFresh) || rFresh <= 0) continue
    const r = Math.max(0, rFresh + shiftReps)
    pts.push(`${xToPxRaw(w).toFixed(1)},${yToPxRaw(r).toFixed(1)}`)
  }
  if (pts.length < 2) return ''
  return `M ${pts.join(' L ')}`
}

// Build a closed filled polygon between two bootstrap quantile lines.
// Both lines are sampled over the band's entered-space W grid; we walk
// the lower line forward then the upper line backward to form a closed
// path. Points outside [clipLo, clipHi] are dropped; if the resulting
// vertex list is too short we skip the polygon entirely.
function bandPolygonPath(
  W_grid: readonly number[],
  qLow: readonly number[],
  qHigh: readonly number[],
  xToPxRaw: (x: number) => number,
  yToPxRaw: (y: number) => number,
  clipLo: number,
  clipHi: number,
): string {
  const lower: string[] = []
  const upper: string[] = []
  for (let i = 0; i < W_grid.length; i++) {
    const w = W_grid[i]
    if (w < clipLo || w > clipHi) continue
    const rLo = qLow[i]
    const rHi = qHigh[i]
    if (!Number.isFinite(rLo) || !Number.isFinite(rHi)) continue
    if (rHi < 0) continue
    const x = xToPxRaw(w).toFixed(1)
    lower.push(`${x},${yToPxRaw(Math.max(0, rLo)).toFixed(1)}`)
    upper.push(`${x},${yToPxRaw(Math.max(0, rHi)).toFixed(1)}`)
  }
  if (lower.length < 2) return ''
  // Close the polygon: forward along lower, backward along upper.
  return `M ${lower.join(' L ')} L ${upper.reverse().join(' L ')} Z`
}

// Single quantile line (used for q50 dashed median).
function quantileLinePath(
  W_grid: readonly number[],
  q: readonly number[],
  xToPxRaw: (x: number) => number,
  yToPxRaw: (y: number) => number,
  clipLo: number,
  clipHi: number,
): string {
  const pts: string[] = []
  for (let i = 0; i < W_grid.length; i++) {
    const w = W_grid[i]
    if (w < clipLo || w > clipHi) continue
    const r = q[i]
    if (!Number.isFinite(r) || r < 0) continue
    pts.push(`${xToPxRaw(w).toFixed(1)},${yToPxRaw(r).toFixed(1)}`)
  }
  if (pts.length < 2) return ''
  return `M ${pts.join(' L ')}`
}

export default function CurvePane({
  mode,
  curve,
  priorCurve = null,
  bootstrapTargetReps = asRepsDone(15),
  observations,
  sparkWeight,
  sparkReps,
  schemeRir,
  schemeSetNumber,
  acceptableRepMin,
  acceptableRepMax,
  onSparkChange,
  onGo,
  onBack,
  onConfirmRir,
  submitting = false,
  completedSets,
  fatigueBetaPerSet,
  fatigueMaxSetIndex = 3,
  fatigueBetaSource,
  bands,
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
  // Continuous edge-hold auto-scroll (pre mode only). When the pointer is
  // parked near either edge of the plot during a drag, we advance the spark
  // weight one snap step every ~140ms toward that edge so the user can
  // simply hold rather than repeatedly tap.
  const edgeDirRef = useRef<-1 | 0 | 1>(0)
  const edgeIntervalRef = useRef<number | null>(null)
  const sparkRef = useRef({ w: sparkWeight, r: sparkReps })
  useEffect(() => {
    sparkRef.current = { w: sparkWeight, r: sparkReps }
  }, [sparkWeight, sparkReps])

  // Sticky domain: seeded from curve + observations + initial spark, and
  // only expanded when the spark approaches the edges. This keeps the graph
  // from sliding around as the user drags.
  const fullDomain = mode === 'completed'
  const [domain, setDomain] = useState(() =>
    computeDomain(curve, sparkWeight, observations, completedSets, bootstrapTargetReps, schemeRir, fullDomain),
  )

  // Reset when the underlying fit or history changes (new prescription / refit).
  useEffect(() => {
    setDomain(computeDomain(curve, sparkWeight, observations, completedSets, bootstrapTargetReps, schemeRir, fullDomain))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curve, observations, completedSets, bootstrapTargetReps, schemeRir, fullDomain])

  // Pan (never expand) the window when the spark approaches an edge. This
  // keeps the x/y ranges constant so the chart never zooms out to a scale
  // where selecting a precise weight or reps value is awkward.
  useEffect(() => {
    if (sparkWeight <= 0) return
    const xSpan = domain.xMax - domain.xMin
    if (xSpan <= 0) return
    let next = domain

    // X pan: shift the window by the minimum amount needed to keep the
    // spark inside the inner 84% of the plot.
    const xEdge = xSpan * 0.08
    if (sparkWeight > next.xMax - xEdge) {
      const shift = sparkWeight - (next.xMax - xEdge)
      next = { ...next, xMin: next.xMin + shift, xMax: next.xMax + shift }
    } else if (sparkWeight < next.xMin + xEdge) {
      const shift = (next.xMin + xEdge) - sparkWeight
      const newXMin = Math.max(0, next.xMin - shift)
      next = { ...next, xMin: newXMin, xMax: newXMin + xSpan }
    }

    // Y pan: anchor on the dot's current y (effectiveSparkY for logging
    // mode, or the curve/β-shifted rtf at sparkWeight in pre mode) so
    // dragging weight never pushes the dot off the top or bottom of the
    // visible range.
    let targetY: number | null = null
    if (mode === 'pre' && curve) {
      const betaIdx = Math.max(0, schemeSetNumber - 1)
      const betaHere = fatigueBetaPerSet?.[betaIdx] ?? 0
      const rtfAtSpark = predictReps(sparkWeight, curve) + betaHere
      if (Number.isFinite(rtfAtSpark)) targetY = Math.max(0, rtfAtSpark)
    } else if (mode !== 'completed') {
      targetY = Math.max(0, sparkReps + schemeRir)
    }
    if (targetY != null) {
      const ySpan = next.yMax - next.yMin
      const yEdge = ySpan * 0.08
      if (targetY > next.yMax - yEdge) {
        const shift = targetY - (next.yMax - yEdge)
        next = { ...next, yMin: next.yMin + shift, yMax: next.yMax + shift }
      } else if (targetY < next.yMin + yEdge) {
        const shift = (next.yMin + yEdge) - targetY
        const newYMin = Math.max(0, next.yMin - shift)
        next = { ...next, yMin: newYMin, yMax: newYMin + ySpan }
      }
    }

    if (next !== domain) setDomain(next)
  }, [sparkWeight, sparkReps, schemeRir, mode, curve, schemeSetNumber,
      fatigueBetaPerSet, domain])

  const { xMin, xMax, yMin, yMax } = domain

  const xToPx = useCallback(
    (x: number) => PAD_L + (clamp(x, xMin, xMax) - xMin) / (xMax - xMin) * PLOT_W,
    [xMin, xMax],
  )
  const yToPx = useCallback(
    (y: number) => PAD_T + (1 - (clamp(y, yMin, yMax) - yMin) / (yMax - yMin)) * PLOT_H,
    [yMin, yMax],
  )
  // Unclamped versions used to render curves: the path is clipped by the
  // plot rect via <clipPath>, so asymptotes near x=0 / x=M stay hidden
  // instead of being pinned to the chart edges.
  const xToPxRaw = useCallback(
    (x: number) => PAD_L + (x - xMin) / (xMax - xMin) * PLOT_W,
    [xMin, xMax],
  )
  const yToPxRaw = useCallback(
    (y: number) => PAD_T + (1 - (y - yMin) / (yMax - yMin)) * PLOT_H,
    [yMin, yMax],
  )

  // Clip envelope for the rendered curve, β-shifted lines, and bands.
  // Priority: bands envelope (from bootstrap) → curve.min/max_observed
  // (point fit) → fallback. We render only inside this envelope so
  // unsupported regions aren't drawn with false confidence.
  const clipEnvelope = useMemo(() => {
    if (bands) {
      // Per user: clip to ±20% of observed range.
      return {
        clipLo: bands.W_lo_entered * 0.8,
        clipHi: bands.W_hi_entered * 1.2,
      }
    }
    const lo = curve?.min_observed_weight
    const hi = curve?.max_observed_weight
    if (lo != null && hi != null && hi > lo) {
      return { clipLo: lo * 0.8, clipHi: hi * 1.2 }
    }
    // No envelope known — don't clip (preserves prior behavior).
    return { clipLo: undefined as number | undefined, clipHi: undefined as number | undefined }
  }, [bands, curve?.min_observed_weight, curve?.max_observed_weight])

  const pxToData = useCallback(
    (clientX: number, clientY: number): { w: number; r: number } => {
      const svg = svgRef.current
      if (!svg) return { w: sparkWeight, r: sparkReps }
      const rect = svg.getBoundingClientRect()
      const px = ((clientX - rect.left) / rect.width) * VB_W
      const py = ((clientY - rect.top) / rect.height) * VB_H
      const wRaw = xMin + ((clamp(px, PAD_L, VB_W - PAD_R) - PAD_L) / PLOT_W) * (xMax - xMin)
      const rRaw = yMin + (1 - (clamp(py, PAD_T, VB_H - PAD_B) - PAD_T) / PLOT_H) * (yMax - yMin)
      return { w: wRaw, r: rRaw }
    },
    [sparkWeight, sparkReps, xMin, xMax, yMin, yMax],
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
      const dr = -(drPx / PLOT_H) * (yMax - yMin) * DRAG_SENSITIVITY
      return { dw, dr }
    },
    [xMin, xMax, yMin, yMax],
  )

  const applySpark = useCallback(
    (w: number, r: number) => {
      if (mode === 'pre') {
        const snapped = snapWeight(Math.max(0, w))
        const setIdx = Math.max(0, schemeSetNumber - 1)
        const betaHere = (fatigueBetaPerSet && fatigueBetaPerSet[setIdx] != null)
          ? fatigueBetaPerSet[setIdx]
          : 0
        // sparkReps is reps_done (what gets stored): β-shifted predicted
        // reps for the current set, minus the scheme RIR. Keeping it in
        // reps_done units means the dot stays aligned across pre → logging
        // (it's always rendered at sparkReps + schemeRir on the rtf y-axis)
        // and handleConfirmRir can send sparkReps directly without further
        // conversion.
        let newReps: RepsDone
        if (curve) {
          const freshRtf = predictReps(snapped, curve)
          newReps = rtfToRepsDone(asRtf((freshRtf as number) + betaHere), schemeRir)
        } else {
          newReps = bootstrapTargetReps
        }
        onSparkChange(snapped, newReps)
      } else {
        // Drag y is in rtf pixel coordinates; convert to reps_done.
        const clampedRtf = asRtf(Math.max(0, r))
        onSparkChange(sparkWeight, rtfToRepsDone(clampedRtf, schemeRir))
      }
    },
    [mode, curve, bootstrapTargetReps, sparkWeight, onSparkChange,
     schemeSetNumber, schemeRir, fatigueBetaPerSet],
  )

  const stopEdgeScroll = useCallback(() => {
    edgeDirRef.current = 0
    if (edgeIntervalRef.current != null) {
      window.clearInterval(edgeIntervalRef.current)
      edgeIntervalRef.current = null
    }
  }, [])

  // Sync seed: when the caller seeds sparkReps from the backend's target_reps
  // (already reps_done), it's already in the right space, but if the user
  // drags sparkWeight via the x-axis in pre mode we need to realign
  // sparkReps to the β-shifted reps_done at the new weight. Fires in pre
  // mode with a curve; guarded by a round(1) threshold to avoid loops.
  useEffect(() => {
    if (mode !== 'pre' || !curve || sparkWeight <= 0) return
    const setIdx = Math.max(0, schemeSetNumber - 1)
    const betaHere = (fatigueBetaPerSet && fatigueBetaPerSet[setIdx] != null)
      ? fatigueBetaPerSet[setIdx]
      : 0
    const shiftedRtf = asRtf((predictReps(sparkWeight, curve) as number) + betaHere)
    const expected = rtfToRepsDone(shiftedRtf, schemeRir)
    if (Math.abs((expected as number) - (sparkReps as number)) >= 1) {
      onSparkChange(sparkWeight, expected)
    }
  }, [mode, curve, sparkWeight, sparkReps, schemeSetNumber, schemeRir,
      fatigueBetaPerSet, onSparkChange])

  const ensureEdgeScroll = useCallback(() => {
    if (edgeIntervalRef.current != null) return
    edgeIntervalRef.current = window.setInterval(() => {
      if (mode !== 'pre') return
      const dir = edgeDirRef.current
      if (dir === 0) return
      const curW = sparkRef.current.w
      const step = weightStep(curW > 0 ? curW : 10)
      const nextW = Math.max(0, curW + dir * step)
      // Keep drag reference in sync so deltaToData remains correct when the
      // user resumes moving their finger.
      if (dragStart.current) {
        dragStart.current.w += dir * step
      }
      applySpark(nextW, sparkRef.current.r)
    }, 140)
  }, [applySpark, mode])

  const updateEdgeDir = useCallback((clientX: number) => {
    const svg = svgRef.current
    if (!svg || mode !== 'pre') {
      edgeDirRef.current = 0
      return
    }
    const rect = svg.getBoundingClientRect()
    const px = ((clientX - rect.left) / rect.width) * VB_W
    const zone = PLOT_W * 0.08
    if (px < PAD_L + zone) edgeDirRef.current = -1
    else if (px > PAD_L + PLOT_W - zone) edgeDirRef.current = 1
    else edgeDirRef.current = 0
  }, [mode])

  useEffect(() => () => stopEdgeScroll(), [stopEdgeScroll])

  const handlePointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (submitting) return
    e.currentTarget.setPointerCapture(e.pointerId)
    setDragging(true)
    // Tap-to-place: snap the spark to wherever the user tapped, then record
    // that as the drag reference. Subsequent drags use half-sensitivity deltas.
    const { w, r } = pxToData(e.clientX, e.clientY)
    let seedW: EnteredWeightLb = sparkWeight
    let seedR: RepsDone = sparkReps
    if (mode === 'pre') {
      seedW = snapWeight(Math.max(0, w))
      const setIdx = Math.max(0, schemeSetNumber - 1)
      const betaHere = (fatigueBetaPerSet && fatigueBetaPerSet[setIdx] != null)
        ? fatigueBetaPerSet[setIdx]
        : 0
      if (curve) {
        const freshRtf = predictReps(seedW, curve)
        seedR = rtfToRepsDone(asRtf((freshRtf as number) + betaHere), schemeRir)
      } else {
        seedR = bootstrapTargetReps
      }
      onSparkChange(seedW, seedR)
    } else {
      const clampedRtf = asRtf(Math.max(0, r))
      seedR = rtfToRepsDone(clampedRtf, schemeRir)
      onSparkChange(sparkWeight, seedR)
      seedW = sparkWeight
    }
    dragStart.current = {
      cx: e.clientX, cy: e.clientY,
      w: seedW as number, r: seedR as number,
    }
    updateEdgeDir(e.clientX)
    ensureEdgeScroll()
  }

  const handlePointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (!dragging || !dragStart.current) return
    updateEdgeDir(e.clientX)
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
    stopEdgeScroll()
    try { e.currentTarget.releasePointerCapture(e.pointerId) } catch { /* ignore */ }

    // Re-center the domain around the final spark position (preserving the
    // current span) so lines + dot settle nicely in the middle of the plot.
    if (mode !== 'completed') {
      setDomain(d => {
        const xSpan = d.xMax - d.xMin
        const ySpan = d.yMax - d.yMin
        const w = sparkRef.current.w
        const r = mode === 'pre' && curve
          ? Math.max(0, (predictReps(w, curve) as number) +
              (fatigueBetaPerSet?.[Math.max(0, schemeSetNumber - 1)] ?? 0))
          : Math.max(0, sparkRef.current.r + schemeRir)
        const newXMin = Math.max(0, w - xSpan / 2)
        const newYMin = Math.max(0, r - ySpan / 2)
        return {
          xMin: newXMin,
          xMax: newXMin + xSpan,
          yMin: newYMin,
          yMax: newYMin + ySpan,
        }
      })
    }
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

  // Y-axis tick labels: span yMin..yMax in 4 steps.
  const yTicks = useMemo(() => {
    const n = 4
    const ticks: number[] = []
    for (let i = 0; i <= n; i++) {
      ticks.push(Math.round(yMin + ((yMax - yMin) * i) / n))
    }
    return ticks
  }, [yMin, yMax])

  const sparkX = xToPx(sparkWeight)
  const setIdx = Math.max(0, schemeSetNumber - 1)
  const setColor = SET_COLORS[setIdx % SET_COLORS.length]
  // sparkReps is reps_done (what the DB stores). The y-axis is rtf, so add
  // the scheme RIR when placing the dot. This keeps pre and logging modes
  // in sync and gives handleConfirmRir a trivially-correct payload.
  const effectiveSparkY = sparkReps + schemeRir
  const sparkY = yToPx(effectiveSparkY)
  const predictedReps = Math.max(0, Math.round(sparkReps))
  // Color the top readout emerald when predictedReps is inside the
  // acceptable window (backend-provided, or target ± 3), amber when it's
  // not. Outside of pre mode we keep the default neutral color.
  const repMin = acceptableRepMin ?? Math.max(1, Math.round(bootstrapTargetReps - 3))
  const repMax = acceptableRepMax ?? Math.round(bootstrapTargetReps + 3)
  const inRepRange = predictedReps >= repMin && predictedReps <= repMax
  const readoutColor = mode !== 'pre'
    ? 'text-gray-900'
    : inRepRange
      ? (dragging ? 'text-emerald-600' : 'text-emerald-700')
      : (dragging ? 'text-amber-600' : 'text-amber-700')

  const isCompleted = mode === 'completed'
  // In completed mode hide today's gray dots so they don't duplicate the
  // colored sparks rendered from completedSets.
  const visibleObservations = isCompleted
    ? observations.filter(o => o.age_days > 0)
    : observations

  return (
    <div className="relative">
      {/* Top bar: {weight} lbs · {reps} reps + {rir} RIR (+ Go in pre mode) */}
      <div className="mb-1 flex items-center justify-between gap-2 px-1">
        <span className="text-sm font-semibold tabular-nums text-gray-900">
          {isCompleted ? (
            <span className="text-[11px] font-medium text-gray-500">
              Exercise complete · {completedSets?.length ?? 0} sets
            </span>
          ) : (
            <>
              <span className={readoutColor}>
                {sparkWeight % 1 === 0 ? sparkWeight : sparkWeight.toFixed(1)} lbs
              </span>
              <span className="mx-1.5 text-gray-300">·</span>
              <span className={readoutColor}>
                {predictedReps} reps + {schemeRir} RIR
              </span>
            </>
          )}
        </span>
        {mode === 'pre' && (
          <button
            type="button"
            onClick={onGo}
            disabled={submitting || sparkWeight <= 0}
            className="rounded-lg bg-emerald-600 px-4 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-emerald-700 disabled:opacity-50"
          >
            Go →
          </button>
        )}
        {mode === 'logging' && (
          onBack ? (
            <button
              type="button"
              onClick={onBack}
              disabled={submitting}
              className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-[11px] font-semibold text-amber-800 shadow-sm transition-colors hover:bg-amber-100 disabled:opacity-50"
              title="Wrong weight? Go back and re-select."
            >
              ← Select Weight
            </button>
          ) : (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
              log reps
            </span>
          )
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

        {/* Caption clarifying that the band excludes today's sets. */}
        {bands && (
          <text
            x={PAD_L + PLOT_W - 2}
            y={PAD_T + 8}
            textAnchor="end"
            fontSize="7"
            fill="#10b981"
            opacity={0.7}
          >
            historical 5–95% CI (excludes today)
          </text>
        )}

        {/* Bootstrap CI bands (rendered behind everything else so the
            point curve and observations sit on top). The band represents
            historical uncertainty of the fresh-data fit excluding today's
            sets — by design it stays stable through a session as today's
            point curve updates. */}
        {bands && clipEnvelope.clipLo != null && clipEnvelope.clipHi != null && (() => {
          const clo = clipEnvelope.clipLo
          const chi = clipEnvelope.clipHi
          const outer = bandPolygonPath(
            bands.W_grid, bands.q05, bands.q95,
            xToPxRaw, yToPxRaw, clo, chi,
          )
          const inner = bandPolygonPath(
            bands.W_grid, bands.q25, bands.q75,
            xToPxRaw, yToPxRaw, clo, chi,
          )
          const median = quantileLinePath(
            bands.W_grid, bands.q50,
            xToPxRaw, yToPxRaw, clo, chi,
          )
          return (
            <g>
              {outer && (
                <path
                  d={outer}
                  fill="#10b981"
                  fillOpacity={0.07}
                  stroke="none"
                  clipPath="url(#curve-pane-plot-clip)"
                />
              )}
              {inner && (
                <path
                  d={inner}
                  fill="#10b981"
                  fillOpacity={0.10}
                  stroke="none"
                  clipPath="url(#curve-pane-plot-clip)"
                />
              )}
              {median && (
                <path
                  d={median}
                  fill="none"
                  stroke="#10b981"
                  strokeWidth={1}
                  strokeDasharray="2 2"
                  opacity={0.5}
                  clipPath="url(#curve-pane-plot-clip)"
                />
              )}
            </g>
          )
        })()}

        {/* Prior curve (gray, dotted — matches history-point color) */}
        {isCompleted && priorCurve && (
          <path
            d={curvePath(
              priorCurve, xMin, xMax, xToPxRaw, yToPxRaw,
              clipEnvelope.clipLo, clipEnvelope.clipHi,
            )}
            fill="none"
            stroke="#9ca3af"
            strokeWidth={1.25}
            strokeDasharray="1 3"
            strokeLinecap="round"
            opacity={0.85}
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
                      clipEnvelope.clipLo, clipEnvelope.clipHi,
                    )}
                    fill="none"
                    stroke={color}
                    strokeWidth={1.25}
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
            d={curvePath(
              curve, xMin, xMax, xToPxRaw, yToPxRaw,
              clipEnvelope.clipLo, clipEnvelope.clipHi,
            )}
            fill="none"
            stroke="#10b981"
            strokeWidth={1.5}
            clipPath="url(#curve-pane-plot-clip)"
          />
        ) : (
          <line
            x1={PAD_L}
            x2={PAD_L + PLOT_W}
            y1={yToPx(bootstrapTargetReps + schemeRir)}
            y2={yToPx(bootstrapTargetReps + schemeRir)}
            stroke="#10b981"
            strokeWidth={1.5}
            strokeDasharray="4 3"
          />
        )}

        {/* Historical observations — plotted at reps-to-failure so they
            live in the same y-space as the fitted curve. */}
        {visibleObservations.map((o, i) => {
          if (o.weight <= 0 || o.reps <= 0) return null
          const op = clamp(1 - o.age_days / 30, 0.25, 1)
          return (
            <circle
              key={`obs-${i}`}
              cx={xToPx(o.weight)}
              cy={yToPx(o.reps + (o.rir ?? 0))}
              r={3}
              fill="#9ca3af"
              opacity={op}
            />
          )
        })}

        {/* Completed-mode sparks for today's sets, with labels. Dots are
            plotted at reps-to-failure (reps + RIR) so they share the y-axis
            with the fitted curves; the label still shows actual reps. */}
        {isCompleted && completedSets?.map((s, i) => {
          const cx = xToPx(s.weight)
          const cy = yToPx(s.reps + s.rir)
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
                stroke={setColor}
                strokeOpacity="0.3"
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
              fill={mode === 'pre' ? setColor : '#f59e0b'}
              fillOpacity="0.22"
            />
            <circle
              cx={sparkX}
              cy={sparkY}
              r={6}
              fill={mode === 'pre' ? setColor : '#f59e0b'}
              stroke="#fff"
              strokeWidth={1.5}
            />
          </g>
        )}
      </svg>

      {/* Spark tooltip (pre: set + target; logging: reps + hint) */}
      {!isCompleted && (
        <div className="mt-1 flex items-baseline justify-between px-1 text-[10px] text-gray-500">
          {mode === 'pre' ? (
            <>
              <span>
                Set {schemeSetNumber} · Target {Math.max(0, Math.round(bootstrapTargetReps))} reps + {schemeRir} RIR
              </span>
              <span className="text-gray-400">drag to pick weight</span>
            </>
          ) : (
            <>
              <span className="font-semibold tabular-nums text-gray-900">
                {Math.max(0, Math.round(sparkReps))} reps
              </span>
              <span className="text-gray-400">drag to set reps achieved</span>
            </>
          )}
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
                  backgroundColor: SET_COLORS[(i + 1) % SET_COLORS.length],
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
                  backgroundImage: 'repeating-linear-gradient(to right, #9ca3af 0 1px, transparent 1px 4px)',
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

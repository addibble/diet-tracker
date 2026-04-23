// Dynamic weight-snap grid for dragging the strength-curve "spark".
//
// Bands:
//   0  ≤ w < 15  → 1 lb integer grid plus the half-pound points 2.5, 7.5, 12.5.
//   15 ≤ w < 100 → 2.5 lb.
//   w  ≥ 100     → 5 lb.

import {
  asEntered,
  asRtf,
  type EnteredWeightLb,
  type Rtf,
} from "./units"

export interface CurveFit {
  M: number
  k: number
  gamma: number
  /**
   * Which weight space the curve parameters live in. Backend-emitted curves
   * passed through `_curve_dict` are always "entered" (reprojected to match
   * the frontend's plot coordinates). Kept optional so legacy callers still
   * typecheck; predictReps / solveWeight default to treating the curve as
   * entered-space.
   */
  weight_space?: "entered" | "effective"
}

function snapLow(w: number): number {
  // Allowed points: every integer 0..14 plus {2.5, 7.5, 12.5}.
  const candidates = [0, 1, 2, 2.5, 3, 4, 5, 6, 7, 7.5, 8, 9, 10, 11, 12, 12.5, 13, 14]
  let best = candidates[0]
  let bestDist = Math.abs(w - best)
  for (const c of candidates) {
    const d = Math.abs(w - c)
    if (d < bestDist) {
      best = c
      bestDist = d
    }
  }
  return best
}

export function snapWeight(w: number | EnteredWeightLb): EnteredWeightLb {
  const raw = w as number
  if (!Number.isFinite(raw) || raw <= 0) return asEntered(0)
  if (raw < 15) return asEntered(snapLow(raw))
  if (raw < 100) return asEntered(Math.round(raw / 2.5) * 2.5)
  return asEntered(Math.round(raw / 5) * 5)
}

// Step at a given weight — useful for keyboard nudging. Returns an
// unbranded lb delta (not an absolute weight).
export function weightStep(w: number | EnteredWeightLb): number {
  const raw = w as number
  if (raw < 15) return 1
  if (raw < 100) return 2.5
  return 5
}

// Returns the snapped weight one step above the given weight.
export function nextWeight(w: number | EnteredWeightLb): EnteredWeightLb {
  const raw = w as number
  return snapWeight(raw + weightStep(raw) + 1e-9)
}

// Returns the snapped weight one step below.
export function prevWeight(w: number | EnteredWeightLb): EnteredWeightLb {
  const raw = w as number
  return snapWeight(Math.max(0, raw - weightStep(raw) - 1e-9))
}

// Build the ordered list of snap points covering [min, max] inclusive.
export function weightGrid(min: number, max: number): EnteredWeightLb[] {
  const out: EnteredWeightLb[] = []
  let w = snapWeight(Math.max(0, min))
  out.push(w)
  let guard = 0
  while ((w as number) < max && guard < 2000) {
    const next = nextWeight(w)
    if ((next as number) <= (w as number)) break
    w = next
    out.push(w)
    guard++
  }
  return out
}

/**
 * r_fresh(W) = k * (M/W - 1)^gamma. 0 when W >= M.
 *
 * Returns reps-to-failure (rtf), not reps_done. Callers who need reps_done
 * must subtract the scheme RIR via `rtfToRepsDone()` in lib/units.
 *
 * `w` must be in the same weight space as `fit.weight_space` (entered-space
 * for any curve coming from the backend via `_curve_dict`). Frontend code
 * should never call this with effective weights; the backend owns
 * effective-space prescription math.
 */
export function predictReps(w: number | EnteredWeightLb, fit: CurveFit): Rtf {
  const raw = w as number
  if (raw <= 0 || raw >= fit.M || fit.k <= 0) return asRtf(0)
  const ratio = fit.M / raw - 1
  if (ratio <= 0) return asRtf(0)
  return asRtf(fit.k * Math.pow(ratio, fit.gamma))
}

/**
 * Invert the curve: find W such that predictReps(W) ≈ targetReps.
 * W = M / (1 + (target/k)^(1/gamma))
 *
 * `targetReps` is rtf (reps-to-failure), not reps_done. Returns weight in
 * `fit.weight_space` (entered-space for backend-emitted curves).
 */
export function solveWeight(targetReps: number | Rtf, fit: CurveFit): EnteredWeightLb {
  const raw = targetReps as number
  if (raw <= 0 || fit.k <= 0) return asEntered(fit.M * 0.95)
  const ratio = Math.pow(raw / fit.k, 1 / fit.gamma)
  return asEntered(fit.M / (1 + ratio))
}

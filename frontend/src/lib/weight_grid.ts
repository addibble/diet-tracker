// Dynamic weight-snap grid for dragging the strength-curve "spark".
//
// Bands:
//   0  ≤ w < 15  → 1 lb integer grid plus the half-pound points 2.5, 7.5, 12.5.
//   15 ≤ w < 100 → 2.5 lb.
//   w  ≥ 100     → 5 lb.

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

export function snapWeight(w: number): number {
  if (!Number.isFinite(w) || w <= 0) return 0
  if (w < 15) return snapLow(w)
  if (w < 100) return Math.round(w / 2.5) * 2.5
  return Math.round(w / 5) * 5
}

// Step at a given weight — useful for keyboard nudging.
export function weightStep(w: number): number {
  if (w < 15) return 1
  if (w < 100) return 2.5
  return 5
}

// Returns the snapped weight one step above the given weight.
export function nextWeight(w: number): number {
  return snapWeight(w + weightStep(w) + 1e-9)
}

// Returns the snapped weight one step below.
export function prevWeight(w: number): number {
  return snapWeight(Math.max(0, w - weightStep(w) - 1e-9))
}

// Build the ordered list of snap points covering [min, max] inclusive.
export function weightGrid(min: number, max: number): number[] {
  const out: number[] = []
  let w = snapWeight(Math.max(0, min))
  out.push(w)
  let guard = 0
  while (w < max && guard < 2000) {
    const next = nextWeight(w)
    if (next <= w) break
    w = next
    out.push(w)
    guard++
  }
  return out
}

/**
 * r_fresh(W) = k * (M/W - 1)^gamma. 0 when W >= M.
 *
 * `w` must be in the same weight space as `fit.weight_space` (entered-space
 * for any curve coming from the backend via `_curve_dict`). Frontend code
 * should never call this with effective weights; the backend owns
 * effective-space prescription math.
 */
export function predictReps(w: number, fit: CurveFit): number {
  if (w <= 0 || w >= fit.M || fit.k <= 0) return 0
  const ratio = fit.M / w - 1
  if (ratio <= 0) return 0
  return fit.k * Math.pow(ratio, fit.gamma)
}

/**
 * Invert the curve: find W such that predictReps(W) ≈ targetReps.
 * W = M / (1 + (target/k)^(1/gamma))
 *
 * `targetReps` is rtf (reps-to-failure), not reps_done. Returns weight in
 * `fit.weight_space`.
 */
export function solveWeight(targetReps: number, fit: CurveFit): number {
  if (targetReps <= 0 || fit.k <= 0) return fit.M * 0.95
  const ratio = Math.pow(targetReps / fit.k, 1 / fit.gamma)
  return fit.M / (1 + ratio)
}

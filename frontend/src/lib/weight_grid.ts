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
  /**
   * Effective-space virtual 1RM. NOT directly comparable to entered weights —
   * the chart X-axis is entered, but the curve params live in effective
   * space. Use predictReps / solveWeight which convert via
   * ``W_eff = W_ent * ext_mult + bw_offset``.
   */
  M: number
  k: number
  gamma: number
  /**
   * v4 shifted-form parameter: ``r_fresh(W) = k * (M / (W + delta) - 1)^gamma``.
   * Older snapshots may omit it; treat ``undefined`` as ``0`` to recover the
   * v3 form. Lives in the same space as ``M`` and ``W_eff`` (effective).
   */
  delta?: number
  /**
   * Curve parameter space. Backend ships "effective" now (all modes); keep
   * the union for robustness against older snapshots — any non-"effective"
   * value is treated as legacy-entered (no conversion, offset=0, mult=1).
   */
  weight_space?: "effective" | "entered"
  /** Entered-weight axis (for plotting) — always "entered" today. */
  x_axis_space?: "entered"
  /** BW × bw_frac, lb. Added to W_ent * ext_mult to get effective weight. */
  bw_offset?: number
  /** External load multiplier (e.g. dumbbells carrying 2×). Default 1. */
  ext_mult?: number
  /** Entered-space max observed weight, for chart X-domain. */
  max_observed_weight?: number
}

function effectiveWeight(w: number, fit: CurveFit): number {
  // Legacy "entered"-space curves (old backend) have already been reprojected
  // and shouldn't be converted again. New backend emits "effective" + the
  // bw_offset/ext_mult metadata so we convert here.
  if (fit.weight_space === "entered") return w
  const mult = fit.ext_mult ?? 1
  const offset = fit.bw_offset ?? 0
  return w * mult + offset
}

function enteredFromEffective(w_eff: number, fit: CurveFit): number {
  if (fit.weight_space === "entered") return w_eff
  const mult = fit.ext_mult ?? 1
  const offset = fit.bw_offset ?? 0
  if (mult <= 0) return Math.max(0, w_eff - offset)
  return Math.max(0, (w_eff - offset) / mult)
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
 * Predict rtf with v4 form:
 *   r_fresh(W_entered) = k * (M / (W_eff + delta) - 1)^gamma
 * where W_eff = W_entered * ext_mult + bw_offset. ``delta`` defaults to 0 for
 * snapshots from older backends (recovers v3 form ``k * (M/W_eff - 1)^gamma``).
 * Returns 0 when (W_eff + delta) >= M or inputs are invalid.
 *
 * Returns reps-to-failure (rtf), not reps_done. Callers who need reps_done
 * must subtract the scheme RIR via `rtfToRepsDone()` in lib/units.
 *
 * `w` is ENTERED-space weight (chart X-axis). The curve params live in
 * effective space; we convert internally so mixed-mode exercises (with a
 * bodyweight component) evaluate correctly instead of the broken affine
 * approximation we used to ship.
 */
export function predictReps(w: number | EnteredWeightLb, fit: CurveFit): Rtf {
  const raw = w as number
  if (raw <= 0 || fit.k <= 0) return asRtf(0)
  const w_eff = effectiveWeight(raw, fit)
  const delta = fit.delta ?? 0
  const denom = w_eff + delta
  if (denom <= 0 || denom >= fit.M) return asRtf(0)
  const ratio = fit.M / denom - 1
  if (ratio <= 0) return asRtf(0)
  return asRtf(fit.k * Math.pow(ratio, fit.gamma))
}

/**
 * Invert v4 form: W_eff = M / (1 + (target/k)^(1/gamma)) - delta;
 * W_ent = (W_eff - bw_offset) / ext_mult. ``delta`` defaults to 0 (v3 form).
 *
 * `targetReps` is rtf (reps-to-failure), not reps_done. Returns entered-space
 * weight unsnapped — callers should apply `snapWeight` at UI boundaries only.
 */
export function solveWeight(targetReps: number | Rtf, fit: CurveFit): EnteredWeightLb {
  const raw = targetReps as number
  const delta = fit.delta ?? 0
  if (raw <= 0 || fit.k <= 0) {
    return asEntered(enteredFromEffective(Math.max(0, fit.M * 0.95 - delta), fit))
  }
  const ratio = Math.pow(raw / fit.k, 1 / fit.gamma)
  const w_eff = Math.max(0, fit.M / (1 + ratio) - delta)
  return asEntered(enteredFromEffective(w_eff, fit))
}

/**
 * Single source of truth for weight/rep space conversions and branded types
 * on the frontend.
 *
 * Mirrors `backend/app/units.py`. See that module's docstring for the full
 * description of the four spaces (entered weight, effective weight, rtf,
 * reps_done).
 *
 * The branded types exist to catch space-mixup bugs at compile time: mixing
 * an `EnteredWeightLb` and an `EffectiveWeightLb` in arithmetic is a TS
 * error. Cast at the JSON boundary via the `asEntered` / `asEffective`
 * helpers; never cast inside component code.
 */

export type EnteredWeightLb = number & { readonly __brand: "EnteredWeightLb" };
export type EffectiveWeightLb = number & { readonly __brand: "EffectiveWeightLb" };
export type Rtf = number & { readonly __brand: "Rtf" };
export type RepsDone = number & { readonly __brand: "RepsDone" };
export type Rir = number & { readonly __brand: "Rir" };
export type Rpe = number & { readonly __brand: "Rpe" };

export const asEntered = (n: number): EnteredWeightLb => n as EnteredWeightLb;
export const asEffective = (n: number): EffectiveWeightLb =>
  n as EffectiveWeightLb;
export const asRtf = (n: number): Rtf => n as Rtf;
export const asRepsDone = (n: number): RepsDone => n as RepsDone;
export const asRir = (n: number): Rir => n as Rir;
export const asRpe = (n: number): Rpe => n as Rpe;

/** reps_done = rtf - rir, floored at 1. */
export function rtfToRepsDone(rtf: Rtf | number, rir: Rir | number): RepsDone {
  return asRepsDone(Math.max(1, Math.round((rtf as number) - (rir as number))));
}

/** rtf = reps_done + rir. */
export function repsDoneToRtf(reps: RepsDone | number, rir: Rir | number): Rtf {
  return asRtf((reps as number) + (rir as number));
}

/** RIR = 10 - RPE. */
export function rpeToRir(rpe: Rpe | number): Rir {
  return asRir(10 - (rpe as number));
}

/** RPE = 10 - RIR. */
export function rirToRpe(rir: Rir | number): Rpe {
  return asRpe(10 - (rir as number));
}

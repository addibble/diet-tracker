import { useEffect, useState } from 'react'
import CurvePane from './CurvePane'
import type { ComponentProps } from 'react'
import { getFatigueProfile } from '../api/planner'
import type { CurveBandsPayload } from '../api/planner'
import { fetchCurveBandsThrottled } from '../lib/bands_concurrency'

type CurvePaneProps = ComponentProps<typeof CurvePane>

interface Props extends Omit<
  CurvePaneProps,
  'fatigueBetaPerSet' | 'fatigueBetaSource' | 'fatigueMaxSetIndex' | 'bands'
> {
  exerciseId?: number | null
  sessionDate?: string
  fatigueMaxSetIndex?: number
}

// CurvePane + fatigue band. Fetches the per-set β profile for the exercise
// and hands it to CurvePane so the band is drawn as a companion to the curve.
// Parents should NOT render a separate <FatiguePane> alongside this component.
export default function CurvePaneWithFatigue({
  exerciseId,
  sessionDate,
  fatigueMaxSetIndex = 3,
  ...rest
}: Props) {
  const [beta, setBeta] = useState<number[] | null>(null)
  const [source, setSource] = useState<'learned' | 'fallback' | undefined>()
  const [fetchedFor, setFetchedFor] = useState<number | null>(null)
  const [bands, setBands] = useState<CurveBandsPayload | null>(null)
  const [bandsFetchedFor, setBandsFetchedFor] = useState<
    { exerciseId: number; date: string } | null
  >(null)

  const setCount = rest.completedSets?.length ?? 0

  useEffect(() => {
    if (!exerciseId) return
    let cancelled = false
    getFatigueProfile(exerciseId, 30, sessionDate)
      .then((d) => {
        if (cancelled) return
        if (d.is_bodyweight) {
          setBeta(null)
          setSource(undefined)
        } else {
          setBeta(d.beta_per_set ?? null)
          setSource(d.beta_source as 'learned' | 'fallback' | undefined)
        }
        setFetchedFor(exerciseId)
      })
      .catch(() => {
        if (!cancelled) {
          setBeta(null)
          setSource(undefined)
          setFetchedFor(exerciseId)
        }
      })
    return () => { cancelled = true }
  }, [exerciseId, sessionDate, setCount])

  // Bands fetch keyed only on (exerciseId, bandDate) — NOT setCount,
  // because the band uses ``exclude_today`` semantics so today's logged
  // sets don't change today's band cache. This also avoids hammering the
  // bootstrap endpoint on every set log. The throttle helper caps
  // concurrent fetches across the page at 2. Stale state when exerciseId
  // becomes null is masked downstream by ``effectiveBands`` (which gates
  // on a matching id) so we don't need a synchronous reset here.
  useEffect(() => {
    if (!exerciseId) return
    const bandDate = sessionDate ?? new Date().toISOString().slice(0, 10)
    let cancelled = false
    fetchCurveBandsThrottled(exerciseId, bandDate)
      .then((d) => {
        if (cancelled) return
        if (d.has_bands) {
          setBands({
            W_grid: d.W_grid,
            q05: d.q05,
            q25: d.q25,
            q50: d.q50,
            q75: d.q75,
            q95: d.q95,
            n_boot_success: d.n_boot_success,
            W_lo_entered: d.W_lo_entered,
            W_hi_entered: d.W_hi_entered,
            bw_offset: d.bw_offset,
            ext_mult: d.ext_mult,
          })
        } else {
          setBands(null)
        }
        setBandsFetchedFor({ exerciseId, date: bandDate })
      })
      .catch(() => {
        if (!cancelled) {
          setBands(null)
          setBandsFetchedFor({ exerciseId, date: bandDate })
        }
      })
    return () => { cancelled = true }
  }, [exerciseId, sessionDate])

  // Treat beta as stale if the exerciseId changed since the last fetch.
  const effectiveBeta = fetchedFor === exerciseId ? beta : null
  const effectiveSource = fetchedFor === exerciseId ? source : undefined
  const effectiveBands =
    bandsFetchedFor != null && bandsFetchedFor.exerciseId === exerciseId
      ? bands
      : null

  return (
    <CurvePane
      {...rest}
      fatigueBetaPerSet={effectiveBeta ?? undefined}
      fatigueBetaSource={effectiveSource}
      fatigueMaxSetIndex={fatigueMaxSetIndex}
      bands={effectiveBands ?? undefined}
    />
  )
}

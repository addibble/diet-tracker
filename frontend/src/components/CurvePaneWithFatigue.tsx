import { useEffect, useRef, useState } from 'react'
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
  // Lazy-load bands when the chart scrolls into view. Bootstrap bands
  // are an expensive backend compute (~7-14s cold per exercise on prod),
  // so kicking off requests for off-screen charts wastes CPU and starves
  // the throttle queue from serving the visible chart first.
  const [bandsEligible, setBandsEligible] = useState<boolean>(
    () => typeof IntersectionObserver === 'undefined',
  )
  const containerRef = useRef<HTMLDivElement | null>(null)

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

  // Observe the container so we only fetch bands when the chart is
  // (about to be) visible. ``rootMargin`` of 400px starts the fetch a
  // bit before scroll-in so the band paints by the time the user lands
  // on it. Once eligible we stay eligible — no need to re-disable.
  useEffect(() => {
    if (bandsEligible) return
    const node = containerRef.current
    if (!node) return
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setBandsEligible(true)
          obs.disconnect()
        }
      },
      { rootMargin: '400px' },
    )
    obs.observe(node)
    return () => obs.disconnect()
  }, [bandsEligible])

  // Bands fetch keyed only on (exerciseId, bandDate) — NOT setCount,
  // because the band uses ``exclude_today`` semantics so today's logged
  // sets don't change today's band cache. This also avoids hammering the
  // bootstrap endpoint on every set log. The throttle helper caps
  // concurrent fetches across the page at 2. Stale state when exerciseId
  // becomes null is masked downstream by ``effectiveBands`` (which gates
  // on a matching id) so we don't need a synchronous reset here.
  useEffect(() => {
    if (!exerciseId) return
    if (!bandsEligible) return
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
  }, [exerciseId, sessionDate, bandsEligible])

  // Treat beta as stale if the exerciseId changed since the last fetch.
  const effectiveBeta = fetchedFor === exerciseId ? beta : null
  const effectiveSource = fetchedFor === exerciseId ? source : undefined
  const effectiveBands =
    bandsFetchedFor != null && bandsFetchedFor.exerciseId === exerciseId
      ? bands
      : null

  return (
    <div ref={containerRef}>
      <CurvePane
        {...rest}
        fatigueBetaPerSet={effectiveBeta ?? undefined}
        fatigueBetaSource={effectiveSource}
        fatigueMaxSetIndex={fatigueMaxSetIndex}
        bands={effectiveBands ?? undefined}
      />
    </div>
  )
}

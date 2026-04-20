import { useEffect, useState } from 'react'
import CurvePane from './CurvePane'
import type { ComponentProps } from 'react'
import { getFatigueProfile } from '../api/planner'

type CurvePaneProps = ComponentProps<typeof CurvePane>

interface Props extends Omit<
  CurvePaneProps,
  'fatigueBetaPerSet' | 'fatigueBetaSource' | 'fatigueMaxSetIndex'
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

  // Treat beta as stale if the exerciseId changed since the last fetch.
  const effectiveBeta = fetchedFor === exerciseId ? beta : null
  const effectiveSource = fetchedFor === exerciseId ? source : undefined

  return (
    <CurvePane
      {...rest}
      fatigueBetaPerSet={effectiveBeta ?? undefined}
      fatigueBetaSource={effectiveSource}
      fatigueMaxSetIndex={fatigueMaxSetIndex}
    />
  )
}

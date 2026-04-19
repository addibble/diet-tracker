import { useEffect, useState } from 'react'
import { getFatigueProfile, type FatigueProfileResponse } from '../api/planner'

interface Props {
  exerciseId: number
  // Re-fetch whenever set_count changes so the pane reflects today's sets.
  setCount: number
  // Optional: anchor session_observations to a specific historical session.
  sessionDate?: string
}

const VB_W = 320
const VB_H = 180
const PAD_L = 34
const PAD_R = 12
const PAD_T = 12
const PAD_B = 26
const PLOT_W = VB_W - PAD_L - PAD_R
const PLOT_H = VB_H - PAD_T - PAD_B

export default function FatiguePane({ exerciseId, setCount, sessionDate }: Props) {
  const [data, setData] = useState<FatigueProfileResponse | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let cancelled = false
    getFatigueProfile(exerciseId, 30, sessionDate)
      .then((d) => { if (!cancelled) { setData(d); setErr('') } })
      .catch((e) => {
        if (!cancelled) {
          setErr(e instanceof Error ? e.message : 'Failed to load fatigue profile')
        }
      })
    return () => { cancelled = true }
  }, [exerciseId, setCount, sessionDate])

  if (err) return <div className="text-xs text-red-600">{err}</div>
  if (!data) return null
  if (data.is_bodyweight) return null

  const maxIdx = Math.max(
    data.beta_per_set.length,
    ...data.session_observations.map(o => o.set_index),
    3,
  )
  const obsMax = data.session_observations.reduce(
    (m, o) => Math.max(m, o.reps), 0,
  )
  const predMax = data.model_prediction.reduce(
    (m, p) => Math.max(m, p.predicted_rtf), 0,
  )
  const yMax = Math.max(obsMax, predMax, 15) * 1.1

  const xOf = (s: number) =>
    PAD_L + ((s - 1) / Math.max(1, maxIdx - 1)) * PLOT_W
  const yOf = (r: number) =>
    PAD_T + PLOT_H - (r / yMax) * PLOT_H

  // Dotted line: predicted rtf (r_fresh(W) + β_s) across observed set indices.
  const pred = data.model_prediction
    .slice()
    .sort((a, b) => a.set_index - b.set_index)
  const predPath = pred.length > 0
    ? pred.map((p, i) =>
        `${i === 0 ? 'M' : 'L'} ${xOf(p.set_index)} ${yOf(p.predicted_rtf)}`,
      ).join(' ')
    : ''

  // Global-fallback β curve for visual reference when we have no session obs.
  const fallbackW = data.session_observations[0]?.weight ?? 0
  const showFallback = data.session_observations.length === 0

  return (
    <div className="mt-2">
      <div className="text-xs text-gray-500 mb-1 flex items-center justify-between">
        <span>Reps by set index</span>
        <span className="font-mono">
          β {data.beta_per_set.slice(0, 3).map(b => b.toFixed(1)).join(', ')}
          {' '}({data.beta_source})
        </span>
      </div>
      <svg
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        className="w-full h-36 bg-gray-50 border rounded"
      >
        {/* y-axis ticks */}
        {[0, Math.round(yMax / 2), Math.round(yMax)].map((v) => (
          <g key={v}>
            <line
              x1={PAD_L} x2={VB_W - PAD_R} y1={yOf(v)} y2={yOf(v)}
              stroke="#e5e7eb" strokeWidth="0.5"
            />
            <text
              x={PAD_L - 4} y={yOf(v) + 3}
              fontSize="8" fill="#6b7280" textAnchor="end"
            >{v}</text>
          </g>
        ))}
        {/* x-axis labels */}
        {Array.from({ length: maxIdx }, (_, i) => i + 1).map((s) => (
          <text
            key={s} x={xOf(s)} y={VB_H - 8}
            fontSize="8" fill="#6b7280" textAnchor="middle"
          >{s}</text>
        ))}

        {/* predicted line (dotted) */}
        {predPath && (
          <path
            d={predPath} fill="none" stroke="#3b82f6"
            strokeWidth="1.5" strokeDasharray="3 3"
          />
        )}

        {/* observed dots */}
        {data.session_observations.map((o, i) => (
          <g key={i}>
            <circle
              cx={xOf(o.set_index)} cy={yOf(o.reps)} r="4"
              fill="#1f2937"
            />
            <text
              x={xOf(o.set_index) + 6} y={yOf(o.reps) - 4}
              fontSize="8" fill="#6b7280"
            >
              RIR{Math.round(10 - o.rpe)}
            </text>
          </g>
        ))}

        {/* fallback β visualization if no session data yet */}
        {showFallback && data.beta_per_set.slice(0, maxIdx).map((b, i) => (
          <circle
            key={i} cx={xOf(i + 1)} cy={yOf(Math.max(0, 12 + b))} r="3"
            fill="none" stroke="#9ca3af" strokeWidth="1"
          />
        ))}
      </svg>
      {fallbackW > 0 && data.session_observations.length > 0 && (
        <div className="text-[10px] text-gray-500 mt-1">
          dotted line = r_fresh(W) + β_s at each set's weight
        </div>
      )}
    </div>
  )
}
